"""Fixed repository operations for the authenticated MYKOS socket bridge."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import shlex
import subprocess
import tempfile
import time
from mykos_runtime import run, shopcode

REPOS = Path('/var/www/html')
LOCK = Path('/run/mykos-admin.lock')
REPO_SYNC_LOCK = Path('/run/lock/mykos-repo-sync.lock')
REPOSITORY_SOURCE = Path('/etc/mykos/repository-source.json')


def trusted_storage_alias(alias, label):
    """Allow a root-controlled storage alias, with no writable path components."""
    owners = {0, os.geteuid()}
    if alias.lstat().st_uid not in owners:
        raise ValueError(label + ' storage symlink must be root-owned')
    target = Path(os.readlink(alias))
    if not target.is_absolute(): target = alias.parent / target
    target = Path(os.path.abspath(target))
    for path in (target, *target.parents, alias.parent, *alias.parent.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners:
            raise ValueError(label + ' storage target must use trusted directories without symlinks')
        # Root-owned sticky parents cannot replace another owner's directory.
        if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise ValueError(label + ' storage target must not be group/world writable')
    return target




def directory(path):
    # Only configured storage roots may traverse a trusted symlink.
    path = Path(path)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            if current == REPOS or current in REPOS.parents:
                trusted_storage_alias(current, 'Repository')
            else:
                raise ValueError('Symlink paths are not managed')
    if not path.is_dir():
        raise ValueError('Directory unavailable: ' + str(path))
    return path












def storage(path):
    try:
        usage = shutil.disk_usage(directory(path))
        return {'total_bytes': usage.total, 'used_bytes': usage.used, 'free_bytes': usage.free}
    except (OSError, ValueError):
        return None


SIZE_CACHE = Path('/var/cache/mykos/storage-sizes')


def sizes(path):
    """Bound expensive mirror walks and retain completed readings across requests."""
    root = directory(path)
    try:
        SIZE_CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        return {}
    key = hashlib.sha256(str(root).encode()).hexdigest()
    cache = SIZE_CACHE / (key + '.json')
    values = {}
    collected = 0
    try:
        saved = json.loads(cache.read_text())
        values = {name: size for name, size in saved['sizes'].items()
                  if isinstance(name, str) and type(size) is int and size >= 0}
        collected = float(saved['collected_at'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if 0 <= time.time() - collected < 600:
        return values
    with (SIZE_CACHE / (key + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return values
        try:
            output = run(['/usr/bin/du', '-H', '-x', '-B1', '--max-depth=1', '--', str(root)], timeout=120)
            measured = {Path(name).name: int(size) for line in output.splitlines()
                        for size, name in [line.split('\t', 1)]}
            if any(size < 0 for size in measured.values()):
                return values
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
            return values
        saved = {'collected_at': time.time(), 'sizes': measured}
        with tempfile.NamedTemporaryFile(mode='w', dir=SIZE_CACHE, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                json.dump(saved, stream)
                stream.flush()
                temporary.replace(cache)
            finally:
                if temporary.exists(): temporary.unlink()
        return measured




def repo_path(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name):
        raise ValueError('Invalid repository name')
    if name in {'lost+found', 'GPG', 'updateos', 'ostools', 'support'} or name.startswith('RPM-GPG'):
        raise ValueError('Not a managed repository')
    directory(REPOS)
    return directory(REPOS/name)


def service_state(unit):
    try:
        return run(['/usr/bin/systemctl', 'show', unit, '--property=ActiveState', '--value'], timeout=10)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return 'unknown'


def repo_sync_state():
    state = service_state('mykos-repo-sync.service')
    if state in ('active', 'activating', 'reloading'):
        return state
    cli = service_state('mykos-repo-sync-cli.service')
    return cli if cli in ('active', 'activating', 'reloading') else state


def latest_rpm(root):
    latest=None
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:]=[name for name in dirs if not (Path(directory)/name).is_symlink()]
        for name in files:
            if not name.endswith('.rpm'): continue
            path=Path(directory)/name
            if path.is_symlink() or not path.is_file(): continue
            candidate=(path.stat().st_mtime_ns,str(path.relative_to(root)))
            if latest is None or candidate>latest: latest=candidate
    return latest[1] if latest else None


def repository_source():
    if not REPOSITORY_SOURCE.exists(): return None
    info=REPOSITORY_SOURCE.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
        raise ValueError('Repository source configuration must be root-owned and protected')
    config=json.loads(REPOSITORY_SOURCE.read_text())
    from urllib.parse import urlsplit
    for key in ('inventory_url','public_base_url'):
        url=urlsplit(config[key])
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.fragment or url.query:
            raise ValueError('Invalid repository source URL')
    return config


def remote_repo_status(config):
    from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    try:
        with build_opener(ProxyHandler({}),NoRedirect()).open(config['inventory_url'],timeout=8) as response:
            raw=response.read(1048577)
        if len(raw)>1048576: raise ValueError('Repository inventory is too large')
        value=json.loads(raw)
        if not isinstance(value.get('repos'),list) or not isinstance(value.get('storage'),dict) or type(value.get('collected_at')) is not int:
            raise ValueError('Invalid repository inventory')
        if not 0 <= time.time()-value['collected_at'] < 900:
            raise ValueError('Repository inventory is stale')
        for item in value['repos']:
            if not isinstance(item,dict) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',item.get('name','')):
                raise ValueError('Invalid repository inventory name')
        value.update(source_host=config['host'],public_base_url=config['public_base_url'],read_only=True)
        return value
    except (OSError,ValueError,KeyError,TypeError) as error:
        raise ValueError('Repository server inventory unavailable: '+str(error)) from error


def repo_status():
    config=repository_source()
    if config: return remote_repo_status(config)
    usage = sizes(REPOS)
    rows = []
    root = directory(REPOS)
    for path in sorted(root.iterdir()):
        try:
            # updateos is a LAN download tree, visible without granting destructive management.
            readonly = path.name == 'updateos'
            if readonly: directory(path)
            else: repo_path(path.name)
            package=None if readonly else latest_rpm(path)
            row={'name': path.name, 'used_bytes': usage.get(path.name),
                 'latest_rpm': Path(package).name if package else None, 'latest_rpm_path':package, 'modified_at': int(path.stat().st_mtime)}
            if readonly: row.update(read_only=True, access='LAN-only')
            rows.append(row)
        except (OSError, ValueError):
            continue
    return {'repos': rows, 'storage': storage(REPOS), 'service': service_state('httpd'),
            'sync': repo_sync_state(), 'collected_at': int(time.time())}










def dispatch(args):
    if not isinstance(args, list) or not args or not all(isinstance(x, str) for x in args):
        raise ValueError('Invalid arguments')
    if args == ['repo-list']: return repo_status()
    if len(args) != 4: raise ValueError('Wrong argument count')
    import json
    kind, action, target, raw = args
    payload = json.loads(raw)
    if not isinstance(payload, dict): raise ValueError('Invalid payload')
    if kind != 'repo-action': raise ValueError('Unsupported action')
    if kind == 'repo-action' and repository_source():
        raise ValueError('Remote repositories are read-only here; manage them on the repository server')
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == 'delete':
            if repo_sync_state() in ('active', 'activating', 'reloading'):
                raise ValueError('Cannot delete while repository sync is running')
            with os.fdopen(os.open(REPO_SYNC_LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644), 'a') as sync_lock:
                try:
                    fcntl.flock(sync_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError('Cannot delete while repository sync is running')
                shutil.rmtree(repo_path(target))
        elif action in ('enable', 'disable') and target == 'all':
            run(['/usr/bin/systemctl', 'start' if action == 'enable' else 'stop', 'httpd'])
        elif action == 'sync' and target == 'all':
            if repo_sync_state() in ('active', 'activating', 'reloading'):
                raise ValueError('Repository sync is already running')
            run(['/usr/bin/systemctl', 'start', '--no-block', 'mykos-repo-sync.service'])
        else:
            raise ValueError('Unsupported repository action')
    return {'ok': True, 'action': action, 'target': target}
