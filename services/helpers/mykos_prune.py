"""Explicit host-wide container reset. Never invoked by installation or upgrades."""
import contextlib
import fcntl
import grp
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time

import mykos_vm as vm
from mykos_runtime import config, NATIVE_OS_NAMES

STATE = Path('/var/lib/mykos/purge')
WEB = Path('/opt/mykos-web')
TOOLKITS = [Path('/usr/share/mykos'), Path('/usr/local/share/mykos'), Path('/home/mgreen/mykos')]
BUILD = Path('/var/tmp/containers')
RUNTIME = [Path('/var/lib/mykos/image-builds'), Path('/var/lib/mykos/image-admin'), Path('/var/lib/mykos/jobs'), Path('/var/lib/mykos/commands'), Path('/var/cache/mykos/image-os')]
STAGING = Path('/var/tmp')
EXPORTS = [Path('/home/mgreen')]
TIMERS = ['mykos-collector.timer', 'mykos-details.timer', 'mykos-extended.timer', 'mykos-command-collector.timer']
SERVICES = ['mykos-web.service', 'mykos-container-admin.socket', 'mykos-terminal.socket',
            'mykos-collector.service', 'mykos-details.service', 'mykos-extended.service',
            'mykos-command-collector.service', 'mykos-image-remove-*.service', 'mykos-image-reset-*.service', 'mykos-image-build@*.service', 'mykos-stage@*.service', 'mykos-command@*.service',
            'mykos-container-admin@*.service', 'mykos-terminal@*.service']


def run(args, timeout=300):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or result.stdout[-4000:] or 'Command failed: ' + args[0])
    return result.stdout.strip()


def checked(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsafe purge path: ' + str(path))
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('Symlink purge path rejected: ' + str(path))
    return path


def protected():
    return [Path('/etc'), WEB, *TOOLKITS, Path('/backups').resolve(), Path('/var/www/html').resolve(),
            Path('/home/html'), Path('/var/backups'), Path('/var/lib/mykos-installer'), STATE]


def storage_roots():
    roots = {checked(vm.storage_root()), checked(vm.STORAGE)}
    for root in roots:
        if len(root.parts) < 3 or root in (Path('/var/tmp'), Path('/var/lib'), Path('/home/mgreen')):
            raise ValueError('Storage root is too broad for purge: ' + str(root))
        if any(root == p or root in p.parents or p in root.parents for p in protected()):
            raise ValueError('Storage overlaps preserved installation, identity, backup or repo data: ' + str(root))
        if root.exists() and (root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o022):
            raise ValueError('Storage root must be root-owned and not group/world writable: ' + str(root))
    return sorted(roots)


def targets():
    result = []
    for root in storage_roots():
        if root.exists():
            # Keep the root itself: it can be a dedicated mounted filesystem.
            result.extend(root.iterdir())
    for toolkit in TOOLKITS:
        checked(toolkit)
        pos = toolkit / 'pos'
        if pos.exists():
            for path in pos.iterdir():
                if path.name in ('.vm', '.linux', '.shopcode', '.container') or re.fullmatch(r'\.[0-9]{8}(?:\.[A-Za-z0-9_-]+)?', path.name):
                    result.append(path)
    result.extend([BUILD, *RUNTIME])
    for name in NATIVE_OS_NAMES:
        result.append(STAGING / (name + '-docker.tar.xz'))
        result.append(STAGING / (name + '.ks'))
    # Only explicit generated archives, never source/RPM/repo content.
    for root in [*EXPORTS, *TOOLKITS]:
        result.extend(root.glob('mykos-vpn-[0-9]*.tar'))
    return sorted(set(p for p in result if p.exists() or p.is_symlink()))


def mounts():
    # mountinfo escapes spaces, tabs, newlines and backslashes with octal codes.
    return [Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]))
            for line in Path('/proc/self/mountinfo').read_text().splitlines()]


def validate_targets(paths):
    for path in paths:
        checked(path.parent)
        if any(path == p or path in p.parents or p in path.parents for p in protected()):
            # Runtime files under a toolkit are deliberately selected above.
            if not any(path.parent == t/'pos' for t in TOOLKITS):
                raise ValueError('Purge target overlaps preserved data: ' + str(path))
        if not path.is_symlink() and path.is_dir():
            if any(m == path or path in m.parents for m in mounts()):
                raise ValueError('Unmount nested storage before purge: ' + str(path))


def process_table():
    result = {}
    for path in Path('/proc').glob('[0-9]*'):
        try:
            fields = (path/'stat').read_text().rsplit(')', 1)[1].split()
            args = (path/'cmdline').read_bytes().decode('utf-8', 'replace').split('\0')
            result[int(path.name)] = {'parent': int(fields[1]), 'started': fields[19], 'args': args}
        except (OSError, ValueError, IndexError): continue
    return result


def builders():
    table = process_table()
    selected = set()
    for pid, value in table.items():
        args = value['args']
        executable = any(Path(a).name in ('livemedia-creator', 'anaconda') for a in args[:2] if a)
        staged_ks = any(re.fullmatch(r'(?:--ks=|--kickstart=)?/var/tmp/(?:rh(?:7|8|9|10)|(?:rocky|alma|oracle)(?:8|9|10)|centos(?:8|9|10)|fedora(?:43|44)|mssql(?:2022|2025))\.ks', a) for a in args)
        if executable and staged_ks: selected.add(pid)
    while True:
        children = {pid for pid, value in table.items() if value['parent'] in selected}
        if children <= selected: break
        selected.update(children)
    return {pid: table[pid] for pid in selected}


def cancel_builders():
    selected = builders()
    for signum in (signal.SIGTERM, signal.SIGKILL):
        current = process_table()
        for pid, value in selected.items():
            if current.get(pid, {}).get('started') == value['started']:
                try: os.kill(pid, signum)
                except ProcessLookupError: pass
        if signum == signal.SIGTERM and selected:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                current = process_table()
                if not any(current.get(pid, {}).get('started') == value['started'] for pid, value in selected.items()): break
                time.sleep(.2)


def podman_info():
    info = json.loads(run([vm.PODMAN, 'info', '--format', 'json']))
    store = info.get('store') or info.get('Store') or {}
    for field in ('graphRoot', 'runRoot', 'volumePath'):
        value = store.get(field)
        if not value:
            if field == 'volumePath': continue
            raise ValueError('Cannot verify Podman ' + field)
        path = checked(value)
        if len(path.parts) < 3 or any(path == p or path in p.parents or p in path.parents for p in protected()):
            raise ValueError('Podman storage overlaps preserved data: ' + value)
    return store


def plan():
    paths = targets(); validate_targets(paths)
    store = podman_info()
    for path in (WEB, WEB/'data', WEB/'data/auth.db', WEB/'data/api_keys.db', WEB/'data/api-token', WEB/'inventory.json'):
        checked(path)
    containers = json.loads(run([vm.PODMAN, 'ps', '-a', '--format', 'json']))
    images = json.loads(run([vm.PODMAN, 'images', '--format', 'json']))
    return {'hostname': socket.gethostname(), 'confirmation': 'PURGE ' + socket.gethostname(),
            'containers': len(containers), 'images': len(images),
            'image_build_processes': sorted(builders()),
            'scope': 'All rootful Podman resources, including non-MYKOS containers and images',
            'remove_paths': [str(p) for p in paths],
            'podman_storage': {k: store.get(k) for k in ('graphRoot', 'runRoot', 'volumePath')},
            'preserve': ['MYKOS users, password hashes, roles, sessions and API keys',
                         'rsync backup accounts/data', 'synced repositories', 'application/configuration/docs/RPMs'],
            'irreversible': True}


@contextlib.contextmanager
def locked():
    vm.safe_dir(STATE)
    lock = STATE / 'lock'; checked(lock)
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def save(value):
    checked(STATE / 'job.json')
    fd, name = tempfile.mkstemp(dir=STATE, prefix='.purge-')
    try:
        with os.fdopen(fd, 'w') as handle: json.dump(value, handle, indent=2)
        os.replace(name, STATE / 'job.json')
    finally:
        if os.path.exists(name): os.unlink(name)


def status():
    checked(STATE / 'job.json')
    return json.loads((STATE/'job.json').read_text()) if (STATE/'job.json').exists() else {'state': 'idle'}


def queue(confirmation):
    with locked():
        preview = plan()
        if confirmation != preview['confirmation']:
            raise ValueError('Type the exact confirmation: ' + preview['confirmation'])
        if status()['state'] in ('queued', 'running'):
            raise ValueError('Purge already queued/running; inspect mykos-prune status')
        value = {'state': 'queued', 'confirmation': confirmation, 'plan': preview, 'updated_at': int(time.time())}
        save(value)
        try: run(['/usr/bin/systemctl', 'start', '--no-block', 'mykos-purge.service'])
        except Exception:
            value.update(state='failed', error='Could not start purge worker'); save(value); raise
        return value


def active_units():
    units = run(['/usr/bin/systemctl', 'list-units', '--state=active,activating', '--plain', '--no-legend', *TIMERS, *SERVICES])
    return [line.split()[0] for line in units.splitlines() if line.split()]


def clear_web_state():
    # Identity databases are never replaced or removed. Notes share auth.db.
    auth = WEB/'data/auth.db'
    checked(auth)
    if auth.exists():
        with sqlite3.connect(auth, timeout=30) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'pane_notes' in tables: db.execute('DELETE FROM pane_notes')
    data = WEB/'data'; checked(data)
    if data.exists():
        for path in data.iterdir():
            if path.name in ('auth.db', 'api_keys.db', 'api-token') or path.name.startswith(('auth.db-', 'api_keys.db-')): continue
            remove_tree(path)
    gid = grp.getgrnam('mykosweb').gr_gid
    inventory = WEB/'inventory.json'; checked(inventory)
    fd, name = tempfile.mkstemp(dir=WEB, prefix='.purged-inventory-')
    try:
        with os.fdopen(fd, 'w') as handle: handle.write('{"nodes": []}\n')
        os.chmod(name, 0o640); os.chown(name, 0, gid); os.replace(name, inventory)
    finally:
        if os.path.exists(name): os.unlink(name)


def remove_tree(path):
    # Unlink symlinks; never follow links out of a runtime tree.
    path = Path(path)
    if path.is_symlink(): path.unlink()
    elif path.is_dir(): shutil.rmtree(path)
    elif path.exists(): path.unlink()


def execute():
    with locked():
        value = status()
        if value.get('state') != 'queued' or value.get('confirmation') != 'PURGE ' + socket.gethostname():
            raise ValueError('No confirmed purge queued for this host')
        value.update(state='running', updated_at=int(time.time())); save(value)
        resume = []; stopped = False
        try:
            plan()  # Revalidate all storage boundaries before stopping anything.
            resume = active_units()
            stopped = True
            run(['/usr/bin/systemctl', 'stop', *TIMERS, *SERVICES], timeout=300)
            cancel_builders()
            with vm.locked():
                preview = plan(); paths = [Path(p) for p in preview['remove_paths']]
                # Official reset removes all containers, images, volumes, networks,
                # build cache and Podman graph/run roots. Never manually rm graphRoot.
                run([vm.PODMAN, 'system', 'reset', '--force'], timeout=1800)
                if json.loads(run([vm.PODMAN, 'ps', '-a', '--format', 'json'])) or json.loads(run([vm.PODMAN, 'images', '--format', 'json'])):
                    raise RuntimeError('Podman still contains resources; saved disks left intact')
                validate_targets(paths)
                for path in paths: remove_tree(path)
                clear_web_state()
            value.update(state='done', updated_at=int(time.time()), removed_paths=preview['remove_paths'])
        except Exception as error:
            value.update(state='failed', error=str(error), updated_at=int(time.time()))
            raise
        finally:
            if stopped and resume:
                # Resume infrastructure only, never cancelled build/command/terminal workers.
                infrastructure = [u for u in resume if u in TIMERS or u in ('mykos-web.service', 'mykos-container-admin.socket', 'mykos-terminal.socket')]
                try:
                    if infrastructure: run(['/usr/bin/systemctl', 'start', '--no-block', *infrastructure])
                except Exception as error:
                    value.update(state='failed', resume_error=str(error))
            save(value)
        return value
