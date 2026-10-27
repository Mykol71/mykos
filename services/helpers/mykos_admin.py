"""Fixed backup/repository operations for the authenticated MYKOS socket bridge."""
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

BACKUPS = Path('/backups')
REPOS = Path('/var/www/html')
TOOLKIT = Path('/usr/local/share/mykos')
LOCK = Path('/run/mykos-admin.lock')


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


def trusted_backup_alias():
    return trusted_storage_alias(BACKUPS, 'Backup')


def directory(path):
    # Only configured storage roots may traverse a trusted symlink.
    path = Path(path)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            if current == BACKUPS:
                trusted_backup_alias()
            elif current == REPOS or current in REPOS.parents:
                trusted_storage_alias(current, 'Repository')
            else:
                raise ValueError('Symlink paths are not managed')
    if not path.is_dir():
        raise ValueError('Directory unavailable: ' + str(path))
    return path


def account_path(code):
    return BACKUPS / ('tfrsync-' + shopcode(code))


def account(code):
    path = directory(account_path(code))
    user = pwd.getpwnam(path.name)
    if Path(user.pw_dir) != path:
        raise ValueError('Backup account home does not match')
    return path, user


def regular_file_at(root, relative):
    """Open a regular backup file without traversing backup-supplied symlinks."""
    fd = os.open(directory(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(relative).parts
        for part in parts[:-1]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        if not stat.S_ISREG(os.fstat(file).st_mode):
            os.close(file)
            raise ValueError('Expected a regular file')
        return file
    finally:
        os.close(fd)


def read_at(root, relative):
    """Read bounded regular files without following backup-supplied symlinks."""
    with os.fdopen(regular_file_at(root, relative)) as stream:
        return stream.read(16384)


def backup_updated_at(path):
    timestamps = []
    for relative in ('usr2/bbx/log/tfrsync-summary.log', 'd/daisy/log/tfrsync-summary.log'):
        try:
            with os.fdopen(regular_file_at(path, relative)) as stream:
                timestamps.append(int(os.fstat(stream.fileno()).st_mtime))
        except (OSError, ValueError):
            continue
    return max(timestamps) if timestamps else None


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


def backup_status():
    rows = []
    usage = sizes(BACKUPS)
    for user in pwd.getpwall():
        if not re.fullmatch(r'tfrsync-[0-9]{8}', user.pw_name):
            continue
        path = account_path(user.pw_name[-8:])
        try:
            account(user.pw_name[-8:])
            try:
                os_name = read_at(path, 'etc/redhat-release').strip()
            except (OSError, ValueError):
                os_name = 'Unknown'
                try:
                    release={}
                    for line in read_at(path,'etc/os-release').splitlines():
                        if '=' not in line or line.startswith('#'): continue
                        key,value=line.split('=',1);parts=shlex.split(value)
                        if len(parts)==1:release[key]=parts[0]
                    os_name=release.get('PRETTY_NAME') or release.get('NAME') or 'Unknown'
                except (OSError,ValueError):pass
            rows.append({'code': user.pw_name[-8:], 'account': user.pw_name,
                         'type': __import__('mykos_profiles').backup_type(path),
                         'os': os_name, 'used_bytes': usage.get(path.name),
                         'updated_at': backup_updated_at(path),
                         'subscriber': (path/'paying_customer').is_file(), 'available': True})
        except (OSError, ValueError, KeyError):
            rows.append({'code': user.pw_name[-8:], 'account': user.pw_name, 'available': False})
    return {'accounts': sorted(rows, key=lambda x: x['code']), 'storage': storage(BACKUPS),
            'collected_at': int(time.time())}


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


def repo_status():
    usage = sizes(REPOS)
    rows = []
    root = directory(REPOS)
    for path in sorted(root.iterdir()):
        try:
            repo_path(path.name)
            package=latest_rpm(path)
            rows.append({'name': path.name, 'used_bytes': usage.get(path.name),
                         'latest_rpm': Path(package).name if package else None, 'latest_rpm_path':package, 'modified_at': int(path.stat().st_mtime)})
        except (OSError, ValueError):
            continue
    return {'repos': rows, 'storage': storage(REPOS), 'service': service_state('httpd'),
            'sync': service_state('mykos-repo-sync.service'), 'collected_at': int(time.time())}


def validate_key(key):
    if not isinstance(key, str) or len(key) > 3000 or '\n' in key or '\r' in key:
        raise ValueError('Enter one SSH public key (maximum 3000 characters)')
    fields = key.split()
    if len(fields) < 2 or not re.fullmatch(r'(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521))', fields[0]):
        raise ValueError('Unsupported SSH public key type')
    try:
        base64.b64decode(fields[1], validate=True)
    except ValueError:
        raise ValueError('Invalid SSH public key')
    with tempfile.NamedTemporaryFile(mode='w') as file:
        file.write(key.strip() + '\n'); file.flush()
        run(['/usr/bin/ssh-keygen', '-l', '-f', file.name], timeout=10)
    return key.strip()


def write_key(path, user, key):
    ssh = path/'.ssh'
    ssh.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(directory(ssh), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(fd, 0o700); os.fchown(fd, user.pw_uid, user.pw_gid)
        keyfd = os.open('authorized_keys', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                        0o600, dir_fd=fd)
        with os.fdopen(keyfd, 'w') as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Authorized keys must be a single-link regular file')
            os.ftruncate(file.fileno(), 0)
            os.fchmod(file.fileno(), 0o600)
            os.fchown(file.fileno(), user.pw_uid, user.pw_gid)
            file.write('restrict,command="' + str(path/'tfrsync') + '" ' + key + '\n')
    finally:
        os.close(fd)


def set_subscriber(path, enabled):
    fd = os.open(directory(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if enabled:
            marker = os.open('paying_customer', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
            try:
                if not stat.S_ISREG(os.fstat(marker).st_mode):
                    raise ValueError('Subscriber marker must be a regular file')
            finally:
                os.close(marker)
        else:
            try: os.unlink('paying_customer', dir_fd=fd)
            except FileNotFoundError: pass
    finally:
        os.close(fd)


def backup_action(action, code, payload):
    path = account_path(code)
    if action == 'create':
        key = validate_key(payload.get('key'))
        directory(BACKUPS)
        if path.exists() or path.is_symlink():
            raise ValueError('Backup directory already exists')
        try: pwd.getpwnam(path.name)
        except KeyError: pass
        else: raise ValueError('Account already exists')
        source = TOOLKIT/'back/bin/tfrsync'
        if not source.is_file(): raise ValueError('Backup command is not installed')
        run(['/usr/sbin/useradd', '-m', '-U', '-d', str(path), path.name])
        try:
            path, user = account(code)
            shutil.copyfile(source, path/'tfrsync', follow_symlinks=False)
            os.chmod(path/'tfrsync', 0o500)
            os.chown(path/'tfrsync', user.pw_uid, user.pw_gid)
            write_key(path, user, key)
            set_subscriber(path, payload.get('subscriber') is True)
        except Exception:
            run(['/usr/sbin/userdel', path.name])
            shutil.rmtree(path)
            raise
    else:
        path, user = account(code)
        if action == 'key':
            write_key(path, user, validate_key(payload.get('key')))
        elif action == 'subscriber':
            if not isinstance(payload.get('subscriber'), bool): raise ValueError('Subscriber must be boolean')
            set_subscriber(path, payload['subscriber'])
        elif action == 'delete':
            if not isinstance(payload.get('remove_data'), bool): raise ValueError('Choose whether to remove data')
            run(['/usr/sbin/userdel', path.name])
            if payload['remove_data']:
                shutil.rmtree(path)
            elif (path/'.ssh').is_symlink():
                (path/'.ssh').unlink()
            elif (path/'.ssh').exists():
                shutil.rmtree(path/'.ssh')
        elif action == 'keys':
            prefix = 'home/' + path.name + '/.ssh/'
            return {'public_key': read_at(path, prefix+'id_rsa.pub'),
                    'private_key': read_at(path, prefix+'id_rsa')}
        else:
            raise ValueError('Unsupported backup action')
    return {'ok': True, 'code': code, 'action': action}


def dispatch(args):
    if not isinstance(args, list) or not args or not all(isinstance(x, str) for x in args):
        raise ValueError('Invalid arguments')
    if args == ['backup-list']: return backup_status()
    if args == ['repo-list']: return repo_status()
    if len(args) != 4: raise ValueError('Wrong argument count')
    import json
    kind, action, target, raw = args
    payload = json.loads(raw)
    if not isinstance(payload, dict): raise ValueError('Invalid payload')
    if kind not in ('backup-action', 'repo-action'): raise ValueError('Unsupported action')
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if kind == 'backup-action': return backup_action(action, target, payload)
        if action == 'delete':
            if service_state('mykos-repo-sync.service') in ('active', 'activating', 'reloading'):
                raise ValueError('Cannot delete while repository sync is running')
            shutil.rmtree(repo_path(target))
        elif action in ('enable', 'disable') and target == 'all':
            run(['/usr/bin/systemctl', 'start' if action == 'enable' else 'stop', 'httpd'])
        elif action == 'sync' and target == 'all':
            if service_state('mykos-repo-sync.service') in ('active', 'activating', 'reloading'):
                raise ValueError('Repository sync is already running')
            run(['/usr/bin/systemctl', 'start', '--no-block', 'mykos-repo-sync.service'])
        else:
            raise ValueError('Unsupported repository action')
    return {'ok': True, 'action': action, 'target': target}
