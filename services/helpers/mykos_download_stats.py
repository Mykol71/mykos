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
import stat
import tempfile
import time
import urllib.request
from zoneinfo import ZoneInfo

REPORT_ZONE = ZoneInfo("America/Chicago")

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
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS event_times(id TEXT PRIMARY KEY,epoch INTEGER NOT NULL);''')
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
    missing = c.execute('SELECT count(*) FROM events e LEFT JOIN event_times t ON e.id=t.id WHERE t.id IS NULL').fetchone()[0]
    prior = c.execute("SELECT value FROM metadata WHERE key='central_missing_count'").fetchone()
    rescan = bool(missing and (not prior or prior[0] != str(missing)))
    # Apache uses normal rename/create rotation. Inode checkpoints plus opaque request IDs
    # also make compressed files and a repeated collection idempotent.
    for path in sorted(folder.glob('mykos-rpm-downloads.log*')):
        if path.is_symlink() or not path.is_file():
            continue
        st = path.stat()
        cursor = c.execute('SELECT offset,head FROM cursors WHERE device=? AND inode=?', (st.st_dev, st.st_ino)).fetchone()
        zipped = path.suffix == '.gz'
        offset = cursor[0] if cursor and not rescan else 0
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
                        c.execute('INSERT OR IGNORE INTO event_times VALUES(?,?)', (value[0],int(line.split('|',2)[1])))
                except (ValueError, UnicodeError, OverflowError):
                    invalid += 1
            c.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?,?)', (st.st_dev, st.st_ino, handle.tell(), head))
    remaining = c.execute('SELECT count(*) FROM events e LEFT JOIN event_times t ON e.id=t.id WHERE t.id IS NULL').fetchone()[0]
    c.execute("INSERT OR REPLACE INTO metadata VALUES('central_missing_count',?)", (str(remaining),))
    return {'new_requests': accepted, 'invalid_records': invalid}


def repository_report(c):
    totals = []
    for audience, category, count, size in c.execute('SELECT audience,category,count(*),sum(bytes) FROM events GROUP BY audience,category'):
        totals.append(dict(audience=audience, category=category, requests=count, bytes=size))
    packages = [dict(zip(('package','repo','audience','full','partial','aborted','failed','other','bytes'), row)) for row in c.execute('''SELECT package,repo,audience,
sum(category='full'),sum(category='partial'),sum(category='aborted'),sum(category='failed'),sum(category='other'),sum(bytes)
FROM events GROUP BY package,repo,audience ORDER BY sum(category='full') DESC,package LIMIT 5000''')]
    c.create_function('central_day',1,lambda epoch:dt.datetime.fromtimestamp(epoch,REPORT_ZONE).strftime('%Y-%m-%d'),deterministic=True)
    daily = [dict(zip(('day','audience','full','partial','bytes'), row)) for row in c.execute("SELECT central_day(t.epoch) AS local_day,e.audience,sum(e.category='full'),sum(e.category='partial'),sum(e.bytes) FROM events e JOIN event_times t ON e.id=t.id GROUP BY local_day,e.audience ORDER BY local_day DESC LIMIT 730")]
    legacy = [dict(zip(('day','audience','full','partial','bytes'), row)) for row in c.execute("SELECT e.day,e.audience,sum(e.category='full'),sum(e.category='partial'),sum(e.bytes) FROM events e LEFT JOIN event_times t ON e.id=t.id WHERE t.id IS NULL GROUP BY e.day,e.audience ORDER BY e.day DESC LIMIT 730")]
    missing = c.execute('SELECT count(*) FROM events e LEFT JOIN event_times t ON e.id=t.id WHERE t.id IS NULL').fetchone()[0]
    return {'totals': totals, 'packages': packages, 'daily': daily, 'daily_timezone':'America/Chicago', 'daily_legacy_utc':legacy, 'untimed_requests':missing, 'package_rows_truncated': len(packages) == 5000}


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



def validate_traffic(value, repository):
    """Accept bounded provider aggregates only; never credentials or request identities."""
    if not isinstance(value, dict) or value.get('repository') != repository:
        raise ValueError('Traffic repository mismatch')
    def count(v):
        if type(v) is not int or not 0 <= v <= 10**12:
            raise ValueError('Invalid traffic count')
        return v
    def metric(v):
        if not isinstance(v, dict): raise ValueError('Invalid traffic metric')
        out={'count':count(v.get('count')), 'uniques':count(v.get('uniques'))}
        if out['uniques'] > out['count']: raise ValueError('Invalid unique count')
        return out
    result={'repository':repository,'updated_at':count(value.get('updated_at'))}
    for key in ('views','clones'):
        v=value.get(key);out=metric(v);days=v.get(key)
        if not isinstance(days,list) or len(days)>31: raise ValueError('Invalid daily traffic')
        seen=set();out[key]=[]
        for row in days:
            stamp=row.get('timestamp')
            if not isinstance(stamp,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T00:00:00Z',stamp): raise ValueError('Invalid traffic day')
            dt.datetime.strptime(stamp,'%Y-%m-%dT%H:%M:%SZ')
            if stamp in seen: raise ValueError('Duplicate traffic day')
            seen.add(stamp);out[key].append(dict(timestamp=stamp,**metric(row)))
        out[key].sort(key=lambda x:x['timestamp'],reverse=True);result[key]=out
    for key,label,limit in (('referrers','referrer',300),('paths','path',2000)):
        rows=value.get(key)
        if not isinstance(rows,list) or len(rows)>10: raise ValueError('Invalid popular traffic')
        result[key]=[]
        for row in rows:
            name=row.get(label)
            if not isinstance(name,str) or not name or len(name)>limit or any(ord(c)<32 for c in name): raise ValueError('Invalid traffic label')
            result[key].append(dict(metric(row),**{label:name}))
    return result


def traffic_report(repository, now, incoming, previous=None):
    previous=previous or {}
    try:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository or ''): raise ValueError('Invalid repository')
        path=Path(incoming)
        if path.is_symlink(): raise ValueError('Unsafe traffic snapshot')
        with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
            st=os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077: raise ValueError('Traffic snapshot must be a private regular file')
            raw=stream.read(256*1024+1)
        if len(raw)>256*1024: raise ValueError('Traffic snapshot too large')
        result=validate_traffic(json.loads(raw),repository)
        if result['updated_at']>now+300: raise ValueError('Traffic timestamp is in the future')
        if previous.get('repository')==repository and previous.get('updated_at',0)>result['updated_at']:
            result=validate_traffic(previous,repository)
        result['state']='stale' if now-result['updated_at']>36*3600 else 'ok'
        if result['state']=='stale':result['error']='Mac traffic refresh is overdue; showing last successful data.'
        return result
    except Exception:
        try:result=validate_traffic(previous,repository)
        except (ValueError,TypeError,KeyError,AttributeError):result={'repository':repository}
        result.update(state='stale' if result.get('updated_at') else 'unavailable',error='Mac traffic snapshot unavailable; last successful data retained when available.')
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
        prior = json.loads(Path(output).read_text()) if Path(output).exists() else {}
        previous = prior.get('github', {})
        github = github_report(cfg.get('github_repository'), now, previous)
        traffic = traffic_report(cfg.get('github_repository'),now,cfg.get('github_traffic_file','/var/lib/mykos-github-traffic-inbox/traffic.json'),prior.get('github_traffic'))
        result = {'format':1,'updated_at':now,'tracking_since':since,'notice':NOTICE,'repository':repo,'ingestion':ingestion,'github':github,'github_traffic':traffic}
        group = None
        if os.geteuid() == 0 and Path(output) == SUMMARY:
            import grp
            group = grp.getgrnam('mykosweb').gr_gid
        atomic_json(Path(output), result, group)
        return result


def text_report(value):
    lines = ['MYKOS RPM download statistics', 'Repository tracking since: ' + dt.datetime.fromtimestamp(value['tracking_since'],REPORT_ZONE).strftime('%Y-%m-%d %H:%M %Z'), NOTICE, '', 'REPOSITORY: audience/category requests bytes']
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
