#!/usr/bin/env python3
"""Private RPM transfer accounting. No client addresses, cookies, or user agents retained."""
import argparse
import datetime as dt
import fcntl
import gzip
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import urllib.request

CONFIG = Path('/etc/mykos/rpm-download-stats.json')
STATE = Path('/var/lib/mykos/rpm-download-stats')
SUMMARY = Path('/opt/mykos-web/data/rpm-download-stats.json')
PACKAGE = re.compile(r'^/(?:repos/)?(mykos-1\.5-for-x86_64-el(?:9|10))/([A-Za-z0-9][A-Za-z0-9_.+\-]{0,250}\.rpm)$')
NOTICE = ('Full-file responses are GET 200 with matching response length and no logged connection abort. '
          'Range/partial responses are separate and are not stitched into completed downloads. '
          'Requests are not unique people, installations, or customers. GitHub counts are provider-reported '
          'cumulative counters, including repeated and validation downloads; sources are not deduplicated.')


def database(path):
    c = sqlite3.connect(path)
    c.execute('PRAGMA busy_timeout=10000')
    c.executescript('''CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, day TEXT, package TEXT, repo TEXT, audience TEXT, category TEXT, bytes INTEGER);
CREATE TABLE IF NOT EXISTS cursors(device INTEGER, inode INTEGER, offset INTEGER, head BLOB, PRIMARY KEY(device,inode));
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);''')
    return c


def parse(line):
    fields = line.rstrip('\n').split('|')
    if len(fields) != 11:
        raise ValueError('Invalid log field count')
    uid, epoch, method, path, status, size, connection, length, local, test, version = fields
    if version != 'v1' or not re.fullmatch(r'[A-Za-z0-9_@!\-]{8,128}', uid):
        raise ValueError('Missing unique request identifier')
    match = PACKAGE.fullmatch(path)
    if method != 'GET' or not match or test == '1':
        return None
    epoch, status, size = int(epoch), int(status), int(size)
    if not 100 <= status <= 599 or not 0 <= size <= 2**50 or connection not in ('X', '+', '-'):
        raise ValueError('Invalid request fields')
    day = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime('%Y-%m-%d')
    category = ('aborted' if connection == 'X' else 'partial' if status == 206 else
                'full' if status == 200 and length.isdigit() and int(length) == size and size > 0 else
                'failed' if status >= 400 else 'other')
    return uid, day, match[2], match[1], 'internal' if local == '1' else 'external', category, size


def ingest(c, folder, since):
    accepted = invalid = 0
    # Apache uses normal rename/create rotation. Inode checkpoints plus opaque request IDs
    # also make compressed files and a repeated collection idempotent.
    for path in sorted(folder.glob('mykos-rpm-downloads.log*')):
        if path.is_symlink() or not path.is_file():
            continue
        st = path.stat()
        cursor = c.execute('SELECT offset,head FROM cursors WHERE device=? AND inode=?', (st.st_dev, st.st_ino)).fetchone()
        zipped = path.suffix == '.gz'
        offset = cursor[0] if cursor else 0
        if not zipped and offset > st.st_size:
            offset = 0
        opener = gzip.open if zipped else open
        with opener(path, 'rb') as handle:
            head = handle.read(64); handle.seek(0)
            if cursor and head != cursor[1]: offset = 0
            if offset:
                handle.seek(offset)
            while True:
                start = handle.tell()
                raw = handle.readline(8193)
                if not raw:
                    break
                if not raw.endswith(b'\n'):
                    if len(raw) > 8192:
                        raise ValueError('Oversized log record')
                    handle.seek(start)
                    break
                try:
                    line = raw.decode('ascii'); value = parse(line)
                    if value and int(line.split('|', 2)[1]) >= since:
                        accepted += c.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?)', value).rowcount
                except (ValueError, UnicodeError, OverflowError):
                    invalid += 1
            c.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?,?)', (st.st_dev, st.st_ino, handle.tell(), head))
    return {'new_requests': accepted, 'invalid_records': invalid}


def repository_report(c):
    totals = []
    for audience, category, count, size in c.execute('SELECT audience,category,count(*),sum(bytes) FROM events GROUP BY audience,category'):
        totals.append(dict(audience=audience, category=category, requests=count, bytes=size))
    packages = [dict(zip(('package','repo','audience','full','partial','aborted','failed','other','bytes'), row)) for row in c.execute('''SELECT package,repo,audience,
sum(category='full'),sum(category='partial'),sum(category='aborted'),sum(category='failed'),sum(category='other'),sum(bytes)
FROM events GROUP BY package,repo,audience ORDER BY sum(category='full') DESC,package LIMIT 5000''')]
    daily = [dict(zip(('day','audience','full','partial','bytes'), row)) for row in c.execute("SELECT day,audience,sum(category='full'),sum(category='partial'),sum(bytes) FROM events GROUP BY day,audience ORDER BY day DESC LIMIT 730")]
    return {'totals': totals, 'packages': packages, 'daily': daily, 'package_rows_truncated': len(packages) == 5000}


def github_report(repo, now, previous, opener=urllib.request.urlopen):
    if not repo:
        return {'state': 'not-configured', 'assets': []}
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid GitHub owner/repository')
    assets = []
    try:
        for page in range(1, 11):
            url = 'https://api.github.com/repos/' + repo + '/releases?per_page=100&page=' + str(page)
            request = urllib.request.Request(url, headers={'Accept':'application/vnd.github+json', 'User-Agent':'MYKOS-RPM-Statistics/1'})
            with opener(request, timeout=10) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('GitHub response too large')
                releases = json.loads(raw)
            if not isinstance(releases, list):
                raise ValueError('Invalid GitHub release response')
            for release in releases:
                if release.get('draft'):
                    continue
                for asset in release.get('assets', []):
                    name = asset.get('name', '')
                    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+\-]{0,250}\.rpm', name):
                        continue
                    count = asset.get('download_count')
                    if type(count) is not int or count < 0:
                        raise ValueError('Invalid GitHub download counter')
                    assets.append({'id':asset['id'], 'release':str(release['tag_name'])[:128], 'package':name, 'downloads':count})
            if len(releases) < 100:
                break
        else:
            raise ValueError('GitHub pagination limit; report incomplete')
        return {'state':'ok', 'repository':repo, 'updated_at':now, 'assets':assets, 'total':sum(a['downloads'] for a in assets)}
    except Exception as error:
        # Keep a last-good snapshot; never report a fetch failure as zero downloads.
        result = dict(previous or {'assets':[]})
        result.update(state='stale' if previous and previous.get('updated_at') else 'unavailable', repository=repo,
                      error='GitHub statistics refresh failed (' + type(error).__name__ + ')')
        return result


def atomic_json(path, value, group=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.rpm-stats-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, separators=(',', ':')); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o640)
        if group is not None:
            os.chown(temporary, 0, group)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def collect(config_path=CONFIG, state=STATE, output=SUMMARY, now=None):
    config_path = Path(config_path)
    if config_path == CONFIG:
        st = config_path.lstat()
        if config_path.is_symlink() or st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError('Statistics configuration must be root-owned and not writable by others')
    cfg = json.loads(config_path.read_text())
    if cfg.get('format') != 1:
        raise ValueError('Unsupported statistics configuration')
    now = int(now or time.time()); state = Path(state); state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state/'collect.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        c = database(state/'stats.db')
        try:
            since_row = c.execute("SELECT value FROM metadata WHERE key='since'").fetchone()
            since = int(since_row[0]) if since_row else now
            if not since_row:c.execute('INSERT INTO metadata VALUES(?,?)', ('since',str(since)))
            ingestion = ingest(c, Path(cfg.get('log_directory','/var/log/httpd/rpm-downloads')), since)
            repo = repository_report(c); c.commit()
        finally:c.close()
        previous = json.loads(Path(output).read_text()).get('github', {}) if Path(output).exists() else {}
        github = github_report(cfg.get('github_repository'), now, previous)
        result = {'format':1,'updated_at':now,'tracking_since':since,'notice':NOTICE,'repository':repo,'ingestion':ingestion,'github':github}
        group = None
        if os.geteuid() == 0 and Path(output) == SUMMARY:
            import grp
            group = grp.getgrnam('mykosweb').gr_gid
        atomic_json(Path(output), result, group)
        return result


def text_report(value):
    lines = ['MYKOS RPM download statistics', 'Repository tracking since: ' + dt.datetime.fromtimestamp(value['tracking_since'],dt.timezone.utc).isoformat(), NOTICE, '', 'REPOSITORY: audience/category requests bytes']
    lines += ['{audience}/{category} {requests} {bytes}'.format(**r) for r in value['repository']['totals']]
    lines += ['', 'PACKAGES: full partial aborted failed bytes audience package']
    lines += ['{full} {partial} {aborted} {failed} {bytes} {audience} {package}'.format(**r) for r in value['repository']['packages']]
    g = value['github']; lines += ['', 'GITHUB: ' + g.get('state','unavailable') + '; last good refresh: ' + str(g.get('updated_at','never'))]
    lines += [str(a['downloads'])+' '+a['package']+' ('+a['release']+')' for a in g.get('assets',[])]
    if g.get('error'):lines.append(g['error'])
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('action',choices=('collect','report'));p.add_argument('--json',action='store_true');p.add_argument('--config',type=Path,default=CONFIG);p.add_argument('--state',type=Path,default=STATE);p.add_argument('--output',type=Path,default=SUMMARY);a=p.parse_args()
    os.umask(0o077)
    if a.action=='collect':value=collect(a.config,a.state,a.output)
    else:value=json.loads(a.output.read_text())
    print(json.dumps(value,indent=2) if a.json else text_report(value))

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,sqlite3.Error) as e:raise SystemExit('RPM statistics: '+str(e))
