"""Durable, authenticated peer copies. Never import a peer's host paths or OCI spec."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import time

import mykos_vm as vm
from mykos_runtime import shopcode, target

STATE = Path('/var/lib/mykos/instance-transfers')
INBOX = Path('/opt/mykos-web/data/instance-transfer-inbox')
CHUNK = 4 * 1024 * 1024
MAX_BYTES = 1024 ** 4
LIFETIME = 86400
TERMINAL = ('ready', 'failed', 'cancelled', 'expired')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Invalid transfer identifier')
    return value


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Invalid transfer checksum')
    return value


def write(path, value):
    import tempfile
    descriptor, temporary = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(value, handle, sort_keys=True); handle.write('\n')
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def folder(token):
    identifier(token)
    vm.safe_dir(STATE)
    result = STATE / token
    if result.is_symlink():
        raise ValueError('Unsafe transfer directory')
    return result


def spool(token):
    root = vm.storage_root() / '.peer-transfers'
    vm.safe_dir(root)
    result = root / identifier(token)
    if result.is_symlink():
        raise ValueError('Unsafe transfer storage')
    return result


@contextlib.contextmanager
def lock(token=None):
    root = folder(token) if token else STATE
    if token and not root.is_dir():raise ValueError('Transfer not found')
    vm.safe_dir(root)
    with (root / 'operation.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def read(token, owner=None):
    path = folder(token) / 'status.json'
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('Unsafe transfer metadata')
    data = json.loads(path.read_text())
    if data['token'] != token or (owner is not None and not secrets.compare_digest(data['owner'], owner)):
        raise ValueError('Transfer not found')
    return data


def public(data):
    keys = ('token', 'code', 'destination', 'peer', 'mode', 'state', 'created', 'updated', 'offset', 'size', 'error', 'network_isolated', 'destination_outcome_unknown')
    return {key: data[key] for key in keys if key in data}


def update(data, **changes):
    data.update(changes, updated=int(time.time()))
    write(folder(data['token']) / 'status.json', data)
    return public(data)


def checksum(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b''):
            value.update(chunk)
    return value.hexdigest()


def available(code):
    shopcode(code)
    paths=[vm.record_path(code),vm.storage(code),vm.POS/('.'+code),vm.POS/'.linux'/(code+'.json')]
    paths.extend(vm.POS.glob('.'+code+'.*'))
    if any(path.exists() or path.is_symlink() for path in paths):
        raise ValueError('Destination ID already has saved data; choose another ID')
    from mykos_containers import all_containers
    if any(re.match(re.escape(code) + r'(?:\.|$)', c.get('Name', '').lstrip('/')) for c in all_containers()):
        raise ValueError('Destination ID already exists; no container was overwritten')


def cpu():
    fields = {}
    for line in Path('/proc/cpuinfo').read_text().splitlines():
        key, separator, value = line.partition(':')
        if separator and key.strip() not in fields:
            fields[key.strip()] = value.strip()
    return {'architecture': os.uname().machine, 'vendor': fields.get('vendor_id', ''),
            'model': fields.get('model name', ''), 'flags': sorted(fields.get('flags', '').split())}


def compatible(source, destination):
    return (source.get('architecture') == destination.get('architecture')
            and bool(source.get('vendor')) and source.get('vendor') == destination.get('vendor')
            and source.get('model') == destination.get('model')
            and bool(source.get('flags')) and set(source['flags']) <= set(destination.get('flags', [])))


def memory_available():
    match = re.search(r'^MemAvailable:\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M)
    if not match:raise ValueError('Cannot determine available host memory')
    return int(match.group(1)) * 1024


def capabilities(code):
    available(code)
    from mykos_runtime import NATIVE_OS_NAMES, require_container_capacity
    require_container_capacity()
    return {'protocol': 1, 'memory_available': memory_available(), 'cpu': cpu(), 'vm': sorted(vm.VERSIONS), 'native': sorted(NATIVE_OS_NAMES),
            'free_bytes': shutil.disk_usage(vm.storage_root()).free}


def validate_manifest(value):
    if not isinstance(value, dict) or value.get('protocol') != 1:
        raise ValueError('Unsupported transfer protocol')
    identifier(value.get('job')); shopcode(value.get('destination', '')); digest(value.get('sha256'))
    if type(value.get('size')) is not int or not 0 < value['size'] <= MAX_BYTES:
        raise ValueError('Invalid transfer size')
    if value.get('mode') not in ('memory', 'restart') or value.get('kind') not in ('vm', 'native'):
        raise ValueError('Invalid copy mode')
    if value['kind'] == 'native' and value['mode'] != 'restart':
        raise ValueError('Native memory copies are not supported')
    return value


def begin(peer, manifest):
    identifier(peer); validate_manifest(manifest)
    with lock():
        active = []
        for entry in STATE.glob('*/status.json'):
            data = read(entry.parent.name)
            if data.get('direction') == 'receive' and data.get('peer') == peer and data.get('job') == manifest['job']:
                if data['manifest'] != manifest:
                    raise ValueError('Transfer identity was reused with different contents')
                return public(data)
            if data['state'] not in TERMINAL:
                active.append(data)
        if len(active) >= 2:
            raise ValueError('Two peer copies are already active on this host')
        if any(item.get('destination') == manifest['destination'] and item.get('direction') == 'receive' for item in active):
            raise ValueError('Destination ID is reserved by another transfer')
        available(manifest['destination'])
        reserved=sum((d.get('size') or d.get('estimated_bytes',0))*3 for d in active)
        if shutil.disk_usage(vm.storage_root()).free < reserved + manifest['size'] * 3 + 2 * 1024 ** 3:
            raise ValueError('Insufficient free space for transfer, unpacking and rollback')
        token = secrets.token_hex(16)
        vm.safe_dir(folder(token)); vm.safe_dir(spool(token))
        data = {'token': token, 'owner': peer, 'peer': peer, 'job': manifest['job'], 'direction': 'receive',
                'destination': manifest['destination'], 'mode': manifest['mode'], 'state': 'receiving',
                'manifest': manifest, 'offset': 0, 'size': manifest['size'], 'created': int(time.time()),
                'network_isolated': True}
        (spool(token) / 'capsule.tar').touch(mode=0o600, exist_ok=False)
        return update(data)


def live_receive(token, peer):
    data = read(token, identifier(peer))
    if data.get('direction') != 'receive' or data['state'] != 'receiving':
        raise ValueError('Transfer is not accepting data')
    if time.time() - data['created'] > LIFETIME:
        raise ValueError('Transfer expired')
    return data


def receive_chunk(token, peer, upload, offset, expected):
    identifier(upload); digest(expected)
    if not isinstance(offset, int) or offset < 0:
        raise ValueError('Invalid upload offset')
    # A single flat inbox avoids attacker-controlled parent directories. Open
    # without following symlinks and validate the descriptor, not a raced path.
    import pwd
    uid = pwd.getpwnam('mykosweb').pw_uid
    inbox_descriptor = os.open(INBOX, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(inbox_descriptor)
        if info.st_uid != uid or info.st_mode & 0o077:
            raise ValueError('Unsafe transfer inbox')
        descriptor = os.open(upload + '.chunk', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=inbox_descriptor)
    finally:
        os.close(inbox_descriptor)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1 or not 0 < info.st_size <= CHUNK or info.st_mode & 0o077:
            raise ValueError('Unsafe transfer chunk')
        with os.fdopen(descriptor, 'rb', closefd=False) as handle:
            payload = handle.read(CHUNK + 1)
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError('Chunk checksum mismatch')
        with lock(token):
            data = live_receive(token, peer)
            if offset + len(payload) > data['size']:
                raise ValueError('Chunk exceeds declared transfer size')
            capsule = spool(token) / 'capsule.tar'
            with capsule.open('r+b') as handle:
                if offset < data['offset']:
                    handle.seek(offset)
                    if offset + len(payload) > data['offset'] or handle.read(len(payload)) != payload:
                        raise ValueError('Conflicting retried chunk')
                    return public(data)
                if offset != data['offset']:
                    raise ValueError('Unexpected upload offset')
                if shutil.disk_usage(capsule.parent).free < len(payload) + 2*1024**3:
                    raise ValueError('Transfer paused by low storage space; source remains intact')
                handle.truncate(offset); handle.seek(offset); handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            return update(data, offset=offset + len(payload))
    finally:
        os.close(descriptor)
        # Web also unlinks in its finally, including calls rejected before open.


def validated_members(archive):
    members = archive.getmembers()
    if len(members) > 1000000:
        raise ValueError('Too many archive entries')
    names = {}; total = 0
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not path.parts or '\\' in member.name or '\x00' in member.name or '\x00' in member.linkname:
            raise ValueError('Unsafe archive path')
        name = str(path)
        if name in names or not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
            raise ValueError('Unsupported or duplicate archive member')
        names[name] = member; total += member.size
        if member.size < 0 or total > MAX_BYTES:
            raise ValueError('Archive exceeds extraction budget')
    for name, member in names.items():
        if any(str(parent) in names and not names[str(parent)].isdir() for parent in PurePosixPath(name).parents if str(parent) != '.'):
            raise ValueError('Archive writes through a non-directory ancestor')
        if member.islnk():
            linked = names.get(member.linkname)
            if not linked or not linked.isfile():
                raise ValueError('Unsafe archive hard link')
    return members, total


def extract(capsule, destination):
    with tarfile.open(capsule, 'r:') as archive:
        members, total = validated_members(archive)
        if shutil.disk_usage(destination.parent).free < total + 1024 ** 3:
            raise ValueError('Insufficient extraction space')
        destination.mkdir(mode=0o700)
    # GNU tar preserves sparse extents, hardlinks, ACLs and guest xattrs. The
    # complete member/ancestor/link validation above prevents host traversal.
    vm.run(['/usr/bin/tar', '--extract', '--sparse', '--xattrs', '--acls', '--numeric-owner',
            '--same-owner', '--same-permissions', '--file', str(capsule), '--directory', str(destination)], timeout=1800)
    return destination


def schedule(token):
    vm.run(['/usr/bin/systemd-run', '--unit=mykos-peer-copy-' + identifier(token), '--collect',
            '--property=Type=exec', '--property=RuntimeMaxSec=14400', '--property=TimeoutStopSec=60',
            '--property=Nice=15', '--property=IOSchedulingClass=idle', '--property=CPUWeight=10',
            '--property=IOWeight=10', '--property=UMask=0077', '/usr/local/sbin/mykos-instance-transfer', 'worker', token], timeout=20)


def finish_receive(token, peer):
    with lock(token):
        data = read(token, identifier(peer))
        if data['state'] in ('importing', 'ready', 'failed'):
            return public(data)
        data = live_receive(token, peer)
        if data['offset'] != data['size']:
            raise ValueError('Transfer is incomplete')
        if checksum(spool(token) / 'capsule.tar') != data['manifest']['sha256']:
            raise ValueError('Transfer checksum mismatch')
        update(data, state='importing')
        try:
            schedule(token)
        except BaseException:
            update(data, state='receiving')
            raise
        return public(data)


def abort_receive(token, peer):
    with lock(token):
        data=read(token,identifier(peer))
        if data.get('direction')!='receive':raise ValueError('Not an incoming copy')
        # A lost response must never delete a copy that is importing or ready.
        if data['state']!='receiving':return public(data)
        if spool(token).exists():shutil.rmtree(spool(token))
        return update(data,state='cancelled',error='Sender cancelled incomplete upload')


def peer_request(peer, method, path, value=None, payload=None):
    # Workers use the app venv and its existing pinned TLS/paired-VPN transport.
    import sys
    if '/opt/mykos-web' not in sys.path:
        sys.path.insert(0, '/opt/mykos-web')
    import mykos_peers as peers
    from fastapi import HTTPException
    body = payload if payload is not None else json.dumps(value).encode() if value is not None else b''
    for attempt in range(3):
        current = peers.get(identifier(peer))  # Recheck revocation on every attempt.
        try:
            status, _, raw = peers.exchange(current, method, '/api/peers/instance-transfers' + path, body,
                                            'application/octet-stream' if payload is not None else 'application/json')
            if status == 200:return json.loads(raw)
            if status == 404:raise ValueError('Both peers need a version supporting instance copies')
            if status not in (502,503,504) or attempt == 2:
                try:detail=json.loads(raw).get('detail','')
                except ValueError:detail=''
                raise RuntimeError('Peer rejected copy (HTTP '+str(status)+'): '+(detail[:500] if isinstance(detail,str) else ''))
        except HTTPException as error:
            if error.status_code not in (502,503,504) or attempt == 2:raise
        except (OSError,TimeoutError):
            if attempt == 2:raise
        time.sleep(.5 * 2**attempt)
    raise RuntimeError('Peer transport unavailable')


def select_source(code, expected_id=None):
    shopcode(code)
    value = vm.read(code) if vm.record_path(code).exists() else None
    if value and (value.get('role') != 'instance' or not value.get('ready') or value.get('os') not in vm.VERSIONS):
        raise ValueError('Select a ready persistent instance')
    name = value['name'] if value else target(code)
    if not re.fullmatch(re.escape(code) + r'(?:\.[A-Za-z0-9_.-]+)?', name):
        raise ValueError('Source container identity mismatch')
    state = vm.inspect(name)
    cid = state.get('Id', '')
    if not re.fullmatch(r'[0-9a-f]{64}', cid) or (expected_id and cid != expected_id) or state.get('Name', '').lstrip('/') != name:
        raise ValueError('Source container changed; review a new copy plan')
    from mykos_linux import require_persistent
    from mykos_containers import unshared, all_containers
    require_persistent(state)
    labels = state.get('Config', {}).get('Labels') or {}
    if value:
        if labels.get('io.mykos.vm.role') != 'instance' or labels.get('io.mykos.vm.os') != value['os']:
            raise ValueError('Source VM metadata does not match its container')
        if state.get('Image','').removeprefix('sha256:') != str(value.get('image','')).removeprefix('sha256:'):
            raise ValueError('Source VM image no longer matches its record')
        storage = vm.storage(code) / 'storage'
        if storage.is_symlink() or not storage.is_dir() or not any(m.get('Destination') == '/storage' and m.get('Source') == str(storage) for m in state.get('Mounts', [])):
            raise ValueError('Source VM storage is not privately managed')
        for mount in state.get('Mounts',[]):
            destination=mount.get('Destination','')
            if destination in ('/storage','/sys/fs/cgroup'):continue
            if not mount.get('RW') and (destination=='/oem' or destination.startswith('/run/mykos-')):continue
            raise ValueError('VM has additional mounted data; copying was blocked to prevent data loss')
        unshared([storage], all_containers(), cid)
    elif labels.get('io.mykos.vm.os'):
        raise ValueError('Source VM metadata is missing')
    return {'state': state}


def estimate_capture(code, state, kind, ram):
    from mykos_snapshots import allocated_bytes
    image=json.loads(vm.run([vm.PODMAN,'image','inspect',state['Image']]))[0]
    total=int(image.get('Size',0))
    if kind=='vm':
        total += allocated_bytes(vm.storage(code)/'storage') + ram
    else:
        upper=state.get('GraphDriver',{}).get('Data',{}).get('UpperDir')
        if not upper:raise ValueError('Native copy requires inspectable overlay storage')
        total += allocated_bytes(Path(upper))
        for mount in native_mounts(code,state):
            path=Path(mount['source'])
            total += allocated_bytes(path) if path.is_dir() else path.stat().st_blocks*512
    return max(total,64*1024**2)


def source_plan(code, destination, peer):
    from mykos_linux import require_persistent
    from mykos_runtime import NATIVE_OS_NAMES
    identifier(peer); shopcode(destination)
    with vm.locked():
        selected = select_source(code)
        state = selected['state']; require_persistent(state)
        if state.get('State', {}).get('Paused'):
            raise ValueError('Resume this paused instance before copying')
        running = bool(state.get('State', {}).get('Running'))
        if vm.record_path(code).exists():
            value = vm.read(code)
            if not value.get('ready'):
                raise ValueError('Finish image setup before copying this instance')
            kind = 'vm'; os_name = value['os']
        else:
            kind = 'native'; os_name = (state.get('Config', {}).get('Labels') or {}).get('io.mykos.os')
            if os_name not in NATIVE_OS_NAMES:
                raise ValueError('This native image does not have a supported MYKOS OS label')
            native_mounts(code, state)  # Reject shared/unmanaged data before any pause.
        current = {'code': code, 'destination': destination, 'peer': peer, 'container_id': state['Id'],
                   'kind': kind, 'os': os_name, 'source_running': running, 'cpu': cpu(),
                   'memory_limit': value.get('ram_mib', 2048)*1024**2 if kind=='vm' else (state.get('HostConfig',{}).get('Memory') or 2*1024**3)}
        current['estimated_bytes']=estimate_capture(code,state,kind,current['memory_limit'])
    remote = peer_request(peer, 'GET', '/capabilities/' + destination)
    if remote.get('protocol') != 1 or os_name not in remote.get(kind, []):
        raise ValueError('The destination does not support this instance OS/copy protocol')
    if current['cpu']['architecture'] != remote['cpu']['architecture']:
        raise ValueError('Source and destination architectures differ')
    if remote.get('memory_available',0) < current['memory_limit'] + 1024**3:
        raise ValueError('Destination needs more available RAM before this instance can start safely')
    supported = kind == 'vm' and running and '-invtsc' in value.get('cpu_env',{}).get('CPU_FLAGS','')
    if supported:
        monitor = vm.QemuMonitor(value)
        try:
            if monitor.command('info status') != 'VM status: running':raise ValueError('Resume the guest before copying')
            supported = 'blocked' not in monitor.command('info migrate').lower()
        finally:monitor.close()
    current['mode'] = 'memory' if supported and compatible(current['cpu'], remote['cpu']) else 'restart'
    current['network_isolated'] = True
    current['explanation'] = ('Copy disks and running RAM; resume on the peer with guest networking isolated. The source pauses briefly during capture.'
                              if current['mode'] == 'memory' else
                              'Copy filesystem/disks and start on the peer with networking isolated. Running RAM will not be copied. The source may pause during capture (native capture is limited to two minutes).' )
    return current


def legacy_runtime_mount(mount, state):
    """Only host-managed read-only EL7 runtime inputs are recreated on a peer."""
    from mykos_cgroups import EL7_NAMES
    labels=state.get('Config',{}).get('Labels') or {}
    if labels.get('io.mykos.os') not in EL7_NAMES:return False
    inputs={
        '/run/mykos-legacy-systemd':'/run/mykos-legacy-cgroups/systemd',
        '/usr/local/libexec/mykos-rh7-init':'/usr/local/sbin/mykos-rh7-init',
    }
    return (mount.get('Type')=='bind' and mount.get('RW') is False
            and mount.get('Source')==inputs.get(mount.get('Destination'))
            and mount.get('Destination') in inputs)


def native_mounts(code, state):
    from mykos_containers import unshared, all_containers
    result = []
    from mykos_runtime import config
    backup_root=Path(config().get('backup_root') or '/backups')
    if not backup_root.is_absolute() or '..' in backup_root.parts:raise ValueError('Invalid managed backup root')
    allowed = [(vm.POS / ('.' + code)).resolve(), (backup_root / ('tfrsync-' + code)).resolve()]
    for mount in state.get('Mounts', []):
        source = Path(mount.get('Source', '/invalid'))
        destination = mount.get('Destination', '')
        if legacy_runtime_mount(mount,state) or destination == '/sys/fs/cgroup' or (not mount.get('RW') and destination == '/etc/NetworkManager/conf.d/90-mykos-podman.conf' and source == Path('/usr/local/sbin/mykos-podman-nm.conf')):
            continue
        if mount.get('Type') not in ('bind', 'volume') or not source.is_absolute() or source.is_symlink():
            raise ValueError('Unsupported instance mount; source was left unchanged')
        real = source.resolve()
        if mount.get('Type')=='volume':
            volume=mount.get('Name','')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',volume):raise ValueError('Unsupported volume identity')
            volume_info=json.loads(vm.run([vm.PODMAN,'volume','inspect',volume]))[0]
            if volume_info.get('Driver')!='local' or volume_info.get('Options') or Path(volume_info.get('Mountpoint','/invalid')).resolve()!=real:
                raise ValueError('Host-bound or remote volumes require explicit migration; source was left unchanged')
        if mount.get('Type') != 'volume' and not any(real == root or root in real.parents for root in allowed):
            raise ValueError('Instance has unmanaged mounted data; copy requires a managed private mount')
        path = PurePosixPath(destination)
        if not path.is_absolute() or '..' in path.parts or any(c in destination for c in (',', '\n', ':', '\\')) or destination in ('/', '/proc', '/sys', '/dev'):
            raise ValueError('Unsupported instance mount destination')
        if not real.exists() or not (real.is_file() or real.is_dir()):
            raise ValueError('Missing or special native mounted data')
        if real.is_file() and real.stat().st_nlink != 1:
            raise ValueError('Mounted file has external hardlinks; source was left unchanged')
        unshared([real], all_containers(), state['Id'])
        result.append({'source': str(real), 'destination': destination, 'rw': bool(mount.get('RW'))})
    return result


def create(plan, owner):
    digest(owner)
    if not isinstance(plan, dict) or not all(isinstance(plan.get(k), str) for k in ('code','destination','peer')):
        raise ValueError('Invalid copy plan')
    fresh = source_plan(plan['code'], plan['destination'], plan['peer'])
    if any(fresh.get(k) != plan.get(k) for k in ('container_id', 'kind', 'os', 'mode', 'source_running', 'memory_limit')):
        raise ValueError('Instance or compatibility changed; review the new copy plan')
    with lock():
        active = [read(p.parent.name) for p in STATE.glob('*/status.json')]
        if sum(d['state'] not in TERMINAL for d in active) >= 2:
            raise ValueError('Two peer copies are already active on this host')
        if any(d.get('code') == plan['code'] and d['state'] not in TERMINAL for d in active):
            raise ValueError('This instance already has a copy in progress')
        reserved=sum((d.get('size') or d.get('estimated_bytes',0))*3 for d in active if d['state'] not in TERMINAL)
        if shutil.disk_usage(vm.storage_root()).free < fresh['estimated_bytes']*3 + reserved + 2*1024**3:
            raise ValueError('Insufficient free space for a safe instance copy')
        token = secrets.token_hex(16)
        vm.safe_dir(folder(token)); vm.safe_dir(spool(token))
        data = {**fresh, 'token': token, 'owner': owner, 'direction': 'send', 'state': 'queued', 'created': int(time.time())}
        update(data)
        try:
            schedule(token)
        except BaseException:
            update(data, state='failed', error='Copy worker could not start')
            raise
        return public(data)


def image_exists(reference):
    result = subprocess.run([vm.PODMAN, 'image', 'exists', reference], capture_output=True, text=True, timeout=30)
    if result.returncode not in (0, 1):
        raise RuntimeError('Cannot verify transfer image inventory')
    return result.returncode == 0


def remove_unreferenced_image(image):
    if not image_exists(image):return False
    info=json.loads(vm.run([vm.PODMAN,'image','inspect',image]))[0]
    if info.get('RepoTags'):return False
    from mykos_containers import all_containers
    identity=image.removeprefix('sha256:')
    if any(str(c.get('Image','')).removeprefix('sha256:')==identity for c in all_containers()):return False
    vm.run([vm.PODMAN,'image','rm',image],timeout=60)
    return True


def cleanup_source_tag(data):
    if not data.get('source_tag_cleanup'):
        return
    reference = 'localhost/mykos-peer-copy/' + identifier(data['token']) + ':transfer'
    if data.get('source_tag') != reference:
        raise ValueError('Unexpected transfer tag ownership')
    if image_exists(reference):
        image = json.loads(vm.run([vm.PODMAN, 'image', 'inspect', reference]))[0]['Id']
        vm.run([vm.PODMAN, 'untag', image, reference], timeout=30)
        if image.removeprefix('sha256:') not in data['source_image_baseline']:
            # No force: a user/container reference always prevents removal.
            remove_unreferenced_image(image)
    update(data, source_tag_cleanup=False)


def capture_disks(data, value, source, destination):
    """Cold-copy mode never requires a portable RAM stream or changes its files."""
    monitor = vm.QemuMonitor(value)
    paused = False
    try:
        if monitor.command('info status') != 'VM status: running':
            raise ValueError('Resume the guest before copying')
        devices = vm.capture_devices(monitor, source)
        update(data, source_qemu_pause_intent=True)
        arm_pause_guard(data)
        monitor.command('stop'); paused = True
        if monitor.command('info status') != 'VM status: paused':
            raise ValueError('Guest did not pause for consistent disk copy')
        for device in devices:
            if monitor.command('qemu-io ' + device + ' "flush"'):
                raise ValueError('Guest disk flush failed')
        vm.tree_copy(source, destination, reflink='always')
    finally:
        try:
            if paused:
                monitor.command('cont')
                if monitor.command('info status') != 'VM status: running':
                    raise ValueError('Source guest did not resume after disk capture')
                update(data, source_qemu_pause_intent=False)
                disarm_pause_guard(data)
        finally:monitor.close()


def capture(data):
    if shutil.disk_usage(vm.storage_root()).free < data.get('estimated_bytes',0)*3 + 2*1024**3:
        raise ValueError('Insufficient capture space; source was left unchanged')
    work = spool(data['token']); content = work / 'content'; content.mkdir(mode=0o700)
    reference = 'localhost/mykos-peer-copy/' + data['token'] + ':transfer'
    baseline_images = {item.get('Id', item.get('ID', item.get('id', ''))).removeprefix('sha256:') for item in json.loads(vm.run([vm.PODMAN, 'images', '--no-trunc', '--format', 'json']))}
    if image_exists(reference):raise ValueError('Transfer image tag already exists')
    update(data, source_tag=reference, source_image_baseline=sorted(baseline_images), source_tag_cleanup=True)
    try:
        with vm.locked():
            selected = select_source(data['code'], data['container_id'])
            state = selected['state']
            if bool(state.get('State', {}).get('Running')) != data['source_running']:
                raise ValueError('Source running state changed; review a new copy plan')
        manifest = {k: data[k] for k in ('kind', 'os', 'mode', 'cpu', 'destination')}
        manifest.update(protocol=1, image_tag=reference, job=data['token'], memory_limit=data.get('memory_limit', 2*1024**3))
        if data['kind'] == 'vm':
            with vm.locked():
                selected = select_source(data['code'], data['container_id'])
                value = vm.read(data['code'])
                if value['os'] != data['os'] or bool(selected['state'].get('State', {}).get('Running')) != data['source_running']:
                    raise ValueError('Source changed before capture')
                keys = ('os', 'boot', 'ram_mib', 'runtime_ram_mib', 'cpu_env', 'guest_agent', 'installation_complete')
                manifest['configuration'] = {key: value[key] for key in keys if key in value}
                manifest['image'] = value['image']
                vm.run([vm.PODMAN, 'tag', value['image'], reference])
                if data['source_running'] and data['mode'] == 'restart':
                    capture_disks(data, value, vm.storage(data['code']) / 'storage', content / 'storage')
                elif data['source_running']:
                    checkpoint = vm.storage(data['code']) / 'storage/.mykos-checkpoint'
                    if checkpoint.exists():
                        vm.tree_copy(checkpoint, work / 'source-checkpoint-original', reflink='always')
                    update(data, source_checkpoint_restore=True, checkpoint_original_exists=checkpoint.exists())
                    arm_pause_guard(data, seconds=420)
                    try:
                        vm.capture_live(value, vm.storage(data['code']) / 'storage', content / 'storage')
                    finally:
                        restore_source_checkpoint(data)
                else:
                    vm.tree_copy(vm.storage(data['code']) / 'storage', content / 'storage', reflink='always')
            if data['mode'] != 'memory':
                shutil.rmtree(content / 'storage/.mykos-checkpoint', ignore_errors=True)
        else:
            with vm.locked():
                state = select_source(data['code'], data['container_id'])['state']
                if bool(state.get('State',{}).get('Running')) != data['source_running']:
                    raise ValueError('Source running state changed before capture')
                mounts = native_mounts(data['code'], state)
                paused = False
                try:
                    if state.get('State', {}).get('Running'):
                        update(data, source_pause_intent=True)
                        arm_pause_guard(data)
                        vm.run([vm.PODMAN, 'pause', state['Id']], timeout=20); paused = True
                    deadline = time.monotonic() + 120
                    (content / 'mounts').mkdir(mode=0o700)
                    for index, mount in enumerate(mounts):
                        validate_native_tree(Path(mount['source']))
                        source = Path(mount['source']); destination = content / 'mounts' / str(index)
                        vm.run(['/usr/bin/cp', '-a', '--reflink=auto', '--sparse=auto', str(source), str(destination)], timeout=max(1, deadline - time.monotonic()))
                    vm.run([vm.PODMAN, 'commit', '--pause=false', state['Id'], reference], timeout=max(1, deadline - time.monotonic()))
                    manifest['mounts'] = [{k: m[k] for k in ('destination', 'rw')} for m in mounts]
                    manifest['image'] = json.loads(vm.run([vm.PODMAN, 'image', 'inspect', reference]))[0]['Id']
                finally:
                    if paused:
                        vm.run([vm.PODMAN, 'unpause', state['Id']], timeout=30)
                        update(data, source_pause_intent=False)
                    if not data.get('source_pause_intent'):
                        disarm_pause_guard(data)
        vm.run([vm.PODMAN, 'save', '--format=docker-archive', '--output', str(content / 'image.tar'), reference], timeout=1800)
        write(content / 'manifest.json', manifest)
        # GNU tar retains sparse VM disks and numeric guest ownership. The
        # receiver validates every entry before extracting a single byte.
        names = ['manifest.json', 'image.tar', 'storage' if data['kind'] == 'vm' else 'mounts']
        vm.run(['/usr/bin/tar', '--sparse', '--xattrs', '--acls', '--numeric-owner', '-cf', str(work / 'capsule.tar'), '-C', str(content), *names], timeout=1800)
        size = (work / 'capsule.tar').stat().st_size
        if not 0 < size <= MAX_BYTES:
            raise ValueError('Copy exceeds transfer size limit')
        update(data, manifest={**manifest, 'sha256': checksum(work / 'capsule.tar'), 'size': size}, size=size, offset=0)
    finally:
        cleanup_source_tag(data)
        if content.exists():
            shutil.rmtree(content)


def send(data):
    if data['state'] == 'queued':
        update(data, state='capturing'); capture(data); update(data, state='sending')
    remote = peer_request(data['peer'], 'POST', '/begin', data['manifest'])
    update(data, receiver=remote['token'])
    if remote['state'] == 'receiving':
        with (spool(data['token']) / 'capsule.tar').open('rb') as handle:
            handle.seek(remote['offset'])
            while payload := handle.read(CHUNK):
                current = read(data['token'])
                if current.get('cancel_requested'):
                    raise ValueError('Copy cancelled')
                offset = handle.tell() - len(payload)
                suffix = '/' + remote['token'] + '/chunk/' + str(offset) + '/' + hashlib.sha256(payload).hexdigest()
                remote = peer_request(data['peer'], 'PUT', suffix, payload=payload)
                update(data, offset=remote['offset'])
        remote = peer_request(data['peer'], 'POST', '/' + remote['token'] + '/finish', {})
    update(data, state='waiting')
    deadline = time.monotonic() + 1800
    while remote['state'] not in TERMINAL and time.monotonic() < deadline:
        time.sleep(2)
        remote = peer_request(data['peer'], 'GET', '/' + remote['token'])
    if remote['state'] != 'ready':
        raise ValueError('Destination copy did not verify; check its transfer status')
    shutil.rmtree(spool(data['token']))
    update(data, state='ready', network_isolated=True)


def load_image(data, content, manifest):
    reference = 'localhost/mykos-peer-copy/' + data['job'] + ':transfer'
    if manifest.get('image_tag') != reference:
        raise ValueError('Unexpected transferred image tag')
    image = manifest.get('image', '').removeprefix('sha256:'); digest(image)
    with tarfile.open(content / 'image.tar', 'r:') as archive:
        members, _ = validated_members(archive)
        lookup = {m.name: m for m in members}
        member = lookup.get('manifest.json')
        if not member or not member.isfile() or member.size > 65536:
            raise ValueError('Invalid image archive manifest')
        value = json.load(archive.extractfile(member))
        if not isinstance(value, list) or len(value) != 1 or value[0].get('RepoTags') != [reference]:
            raise ValueError('Image archive contains unexpected tags')
        config = lookup.get(value[0].get('Config'))
        if not config or not config.isfile() or config.size > 4 * 1024 ** 2:
            raise ValueError('Invalid image configuration')
        if hashlib.sha256(archive.extractfile(config).read()).hexdigest() != image:
            raise ValueError('Image configuration checksum mismatch')
    if image_exists(reference):raise ValueError('Destination transfer image tag already exists; no tag was overwritten')
    # Record whether this exact immutable image already belongs to the user.
    existing = json.loads(vm.run([vm.PODMAN, 'images', '--no-trunc', '--format', 'json']))
    existed = any(item.get('Id', item.get('ID', item.get('id', ''))).removeprefix('sha256:') == image for item in existing)
    update(data, image='sha256:' + image, image_existed=existed, image_tag=reference)
    vm.run([vm.PODMAN, 'load', '--input', str(content / 'image.tar')], timeout=1800)
    loaded = json.loads(vm.run([vm.PODMAN, 'image', 'inspect', reference]))[0]['Id']
    if loaded.removeprefix('sha256:') != image:
        raise ValueError('Loaded image does not match the captured instance')
    return loaded


def validate_content(data, content):
    path = content / 'manifest.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('Missing copy manifest')
    manifest = json.loads(path.read_text())
    expected = {k: v for k, v in data['manifest'].items() if k not in ('size', 'sha256')}
    if manifest != expected:
        raise ValueError('Capsule metadata differs from the agreed copy')
    allowed = {'manifest.json', 'image.tar', 'storage' if manifest['kind'] == 'vm' else 'mounts'}
    if {p.name for p in content.iterdir()} != allowed:
        raise ValueError('Unexpected capsule contents')
    if (content / 'image.tar').is_symlink() or not (content / 'image.tar').is_file():
        raise ValueError('Missing image data')
    limit=manifest.get('memory_limit',2*1024**3)
    if type(limit) is not int or not 512*1024**2 <= limit <= 128*1024**3:
        raise ValueError('Invalid copied memory limit')
    if memory_available() < limit + 1024**3:
        raise ValueError('Destination no longer has enough available RAM')
    if manifest['kind'] == 'vm':
        if manifest.get('os') not in vm.VERSIONS:
            raise ValueError('Unsupported destination VM OS')
        configuration = manifest.get('configuration', {})
        keys = {'os', 'boot', 'ram_mib', 'runtime_ram_mib', 'cpu_env', 'guest_agent', 'installation_complete'}
        if not isinstance(configuration, dict) or set(configuration) - keys or configuration.get('os') != manifest['os']:
            raise ValueError('Invalid transferred VM profile')
        if type(configuration.get('ram_mib')) is not int or not 2048 <= configuration['ram_mib'] <= 65536:
            raise ValueError('Invalid transferred VM RAM size')
        if not isinstance(configuration.get('cpu_env',{}),dict):raise ValueError('Invalid CPU profile')
        if not isinstance(configuration.get('boot','none'),str) or len(configuration.get('boot','none'))>4096 or '\x00' in configuration.get('boot','none'):
            raise ValueError('Invalid boot profile')
        if any(k in configuration and type(configuration[k]) is not bool for k in ('guest_agent','installation_complete')):
            raise ValueError('Invalid guest runtime flags')
        for key, value in configuration.get('cpu_env', {}).items():
            if key not in ('CPU_MODEL', 'CPU_FLAGS', 'HV', 'VMX') or not isinstance(value, str) or len(value) > 4096 or '\x00' in value:
                raise ValueError('Invalid transferred CPU profile')
        if manifest['mode'] == 'memory' and not compatible(manifest['cpu'], cpu()):
            raise ValueError('Destination CPU changed; RAM cannot safely resume')
        # VM disks/checkpoints never contain symlinks, devices or host links.
        for root, dirs, files in os.walk(content / 'storage', followlinks=False):
            for name in dirs + files:
                item = Path(root) / name
                if item.is_symlink() or not (item.is_file() or item.is_dir()):
                    raise ValueError('Unsupported VM storage member')
    else:
        from mykos_runtime import NATIVE_OS_NAMES
        if manifest.get('os') not in NATIVE_OS_NAMES or not isinstance(manifest.get('mounts'), list) or len(manifest['mounts']) > 64:
            raise ValueError('Unsupported native copy profile')
        destinations = set()
        for index, mount in enumerate(manifest['mounts']):
            destination = mount.get('destination', ''); path = PurePosixPath(destination)
            if set(mount) != {'destination', 'rw'} or type(mount['rw']) is not bool or not path.is_absolute() or '..' in path.parts or any(x in destination for x in (',', '\n', ':', '\\')) or destination in ('/', '/proc', '/sys', '/dev') or destination in destinations:
                raise ValueError('Invalid transferred native mount')
            destinations.add(destination)
            source = content / 'mounts' / str(index)
            if source.is_symlink() or not source.exists():
                raise ValueError('Missing native mounted data')
    return manifest


def materialize(data, content, manifest, image):
    code = data['destination']; name = code + '.' + __import__('socket').gethostname(); private = vm.storage(code)
    with vm.locked():
        available(code)
        from mykos_runtime import require_container_capacity
        require_container_capacity()
        # Journal ownership before materialization; recovery only removes resources
        # carrying this token. No wildcard or force deletion is permitted.
        update(data, destination_name=name, destination_private=str(private), materializing=True)
        private.mkdir(mode=0o700)
        write(private / '.peer-copy-owner', {'token': data['token']})
        if manifest['kind'] == 'vm':
            configuration = manifest['configuration']
            vm.preflight(manifest['os'], configuration['ram_mib'])
            host, bind = vm.settings()
            value = {**configuration, 'name': name, 'role': 'instance', 'image': image,
                     'image_name': manifest['image_tag'], 'host': host, 'bind': bind, 'ready': True,
                     'peer_copy': data['token'], 'network_isolated': True, 'capture_mode': manifest['mode'],
                     'unattended': False, 'boot': configuration.get('boot', 'none')}
            value['console_port'], value['desktop_port'] = vm.allocate(bind)
            os.replace(content / 'storage', private / 'storage')
            if manifest['mode'] == 'memory':
                vm.restore_manifest(value, private / 'storage')
                (private / 'storage/.mykos-checkpoint/restore.request').write_text('restore once\n')
            else:
                shutil.rmtree(private / 'storage/.mykos-checkpoint', ignore_errors=True)
            if vm.VERSIONS[value['os']][0] == 'windows':
                (private / 'windows.env').write_text('USERNAME=\nPASSWORD=\n'); (private / 'windows.env').chmod(0o600)
            vm.save(code, value)
            vm.launch(code, value)
            update(data, destination_id=vm.inspect(name)['Id'])
            if manifest['mode'] == 'memory':
                vm.wait_for_restore(value, private / 'storage')
            else:
                vm.wait_for_copy_boot(value)
        else:
            os.replace(content / 'mounts', private / 'mounts')
            args = [vm.PODMAN, 'run', '-d', '--name', name, '--hostname', name, '--network=none',
                    '--privileged', '--memory=' + str(manifest.get('memory_limit',2*1024**3)), '--restart=unless-stopped', '--pull=never', '--image-volume=ignore',
                    '--label', 'io.mykos.persistent=true', '--label', 'io.mykos.os=' + manifest['os'],
                    '--label', 'io.mykos.peer-copy=' + data['token'], '--label', 'io.mykos.network.isolated=true',
                    '--label', 'io.mykos.network.interface=none', '--label', 'io.mykos.network.ip=', '--label', 'io.mykos.network.name=']
            args += ['--mount', 'type=bind,source=/usr/local/sbin/mykos-podman-nm.conf,destination=/etc/NetworkManager/conf.d/90-mykos-podman.conf,ro=true']
            from mykos_cgroups import arguments, host_mount_arguments
            args += host_mount_arguments(manifest['os'])
            args += arguments(manifest['os'])
            for index, mount in enumerate(manifest['mounts']):
                args += ['--mount', 'type=bind,source=' + str(private / 'mounts' / str(index)) + ',destination=' + mount['destination'] + (',ro=true' if not mount['rw'] else '')]
            from mykos_cgroups import init_command
            vm.run(args + [image,*init_command(manifest['os'])], timeout=120)
            state = vm.inspect(name)
            if not state.get('State', {}).get('Running'):
                raise ValueError('Copied native instance exited during startup')
            update(data, destination_id=state['Id'])
            from mykos_linux import save
            save(code, {'name': name, 'image': image, 'peer_copy': data['token'], 'network_isolated': True})
        # Check isolation from the actual wrapper/container, not just its record.
        state = vm.inspect(name)
        labels = state.get('Config', {}).get('Labels') or {}
        if labels.get('io.mykos.peer-copy') != data['token'] or not state.get('State', {}).get('Running'):
            raise ValueError('Destination identity or running verification failed')
        if manifest['kind'] == 'native' and state.get('HostConfig', {}).get('NetworkMode') != 'none':
            raise ValueError('Destination network isolation verification failed')
        update(data, state='verifying', network_isolated=True)


def cleanup_destination(data):
    if not data.get('materializing'):
        return
    with vm.locked():
        name = data['destination_name']
        from mykos_containers import all_containers
        matches = [c for c in all_containers() if c.get('Name', '').lstrip('/') == name]
        if matches:
            state = vm.inspect(name)
            if ((state.get('Config', {}).get('Labels') or {}).get('io.mykos.peer-copy') != data['token']
                    or (data.get('destination_id') and state['Id']!=data['destination_id'])):
                raise ValueError('Destination ownership changed; manual recovery required')
        else:
            state=None
        from mykos_containers import unshared
        private = vm.storage(data['destination'])
        unshared([private], all_containers(), state['Id'] if state else '')
        if state:
            if state.get('State', {}).get('Running'):
                vm.stop_container(name, grace_seconds=10)
            vm.run([vm.PODMAN, 'rm', state['Id']], timeout=60)
        private = vm.storage(data['destination'])
        if private.exists():
            marker = private / '.peer-copy-owner'
            if marker.is_symlink() or json.loads(marker.read_text()) != {'token': data['token']}:
                raise ValueError('Destination storage ownership changed')
            shutil.rmtree(private)
        record = vm.record_path(data['destination'])
        if record.exists():
            if vm.read(data['destination']).get('peer_copy') != data['token']:
                raise ValueError('Destination record ownership changed')
            record.unlink()
        from mykos_linux import directory, saved
        record = directory() / (data['destination'] + '.json')
        if record.exists():
            if saved(data['destination']).get('peer_copy') != data['token']:
                raise ValueError('Destination native record ownership changed')
            record.unlink()


def import_copy(data):
    content = spool(data['token']) / 'content'
    if content.exists():
        raise ValueError('Interrupted import requires cleanup before retry')
    extract(spool(data['token']) / 'capsule.tar', content)
    manifest = validate_content(data, content)
    image = load_image(data, content, manifest)
    materialize(data, content, manifest, image)
    if image_exists(data['image_tag']):vm.run([vm.PODMAN, 'untag', data['image'], data['image_tag']], timeout=30)
    shutil.rmtree(spool(data['token']))
    update(data, state='ready', materializing=False, network_isolated=True)


def restore_source_checkpoint(data):
    # Caller holds the lifecycle lock. Never remove files while a QEMU memory
    # writer may still be active, and retain the private backup on any failure.
    if not data.get('source_checkpoint_restore'):
        return
    select_source(data['code'], data['container_id'])
    value = vm.read(data['code'])
    monitor = vm.QemuMonitor(value)
    try:
        if monitor.command('info status') != 'VM status: running':
            raise ValueError('Source VM needs recovery; checkpoint backup retained')
        if vm.migration_status(monitor.command('info migrate')) in ('active', 'setup', 'cancelling'):
            raise ValueError('Source memory writer is still active; checkpoint backup retained')
    finally:
        monitor.close()
    checkpoint = vm.storage(data['code']) / 'storage/.mykos-checkpoint'
    backup = spool(data['token']) / 'source-checkpoint-original'
    if data.get('checkpoint_original_exists') and not backup.is_dir():
        raise ValueError('Original checkpoint backup is missing')
    if checkpoint.is_symlink():
        raise ValueError('Source checkpoint path changed')
    if checkpoint.exists():shutil.rmtree(checkpoint)
    if backup.exists():os.replace(backup, checkpoint)
    update(data, source_checkpoint_restore=False)
    disarm_pause_guard(data)


def arm_pause_guard(data, seconds=150):
    vm.run(['/usr/bin/systemd-run', '--unit=mykos-copy-resume-' + identifier(data['token']),
            '--on-active=' + str(seconds) + 's', '--timer-property=AccuracySec=1s', '--collect',
            '--property=Type=oneshot', '--property=TimeoutStartSec=120', '--property=UMask=0077',
            '/usr/local/sbin/mykos-instance-transfer', 'recover-source', data['token']], timeout=20)
    update(data, pause_guard=True)


def disarm_pause_guard(data):
    if not data.get('pause_guard'):return
    vm.run(['/usr/bin/systemctl','stop','mykos-copy-resume-'+identifier(data['token'])+'.timer'],timeout=20)
    update(data, pause_guard=False)


def guarded_source_recovery(token):
    data=read(identifier(token))
    if not any(data.get(k) for k in ('source_pause_intent','source_qemu_pause_intent','source_checkpoint_restore')):return
    # Stop the owning worker before touching a source it may still be copying.
    subprocess.run(['/usr/bin/systemctl','stop','mykos-peer-copy-'+token+'.service'],
                   capture_output=True,text=True,timeout=90)
    with lock(token):
        data=read(token)
        recover_source(data)
        if data['state'] not in TERMINAL:
            update(data,state='failed',error='Capture exceeded its pause budget; source resumed')


def recover_source(data):
    if not any(data.get(k) for k in ('source_pause_intent','source_qemu_pause_intent','source_checkpoint_restore')):
        disarm_pause_guard(data)
        return
    with vm.locked():
        if data.get('source_qemu_pause_intent') or data.get('source_checkpoint_restore'):
            select_source(data['code'], data['container_id'])
            value=vm.read(data['code']);monitor=vm.QemuMonitor(value)
            try:
                if vm.migration_status(monitor.command('info migrate')) in ('active','setup','cancelling'):
                    monitor.command('migrate_cancel');deadline=time.monotonic()+20
                    while vm.migration_status(monitor.command('info migrate')) in ('active','setup','cancelling'):
                        if time.monotonic()>deadline:raise ValueError('Source migration cancellation needs operator recovery')
                        time.sleep(.1)
                if monitor.command('info status').startswith('VM status: paused'):monitor.command('cont')
                if monitor.command('info status') != 'VM status: running':raise ValueError('Source VM needs operator recovery')
            finally:monitor.close()
            update(data,source_qemu_pause_intent=False)
        restore_source_checkpoint(data)
        if not data.get('source_pause_intent'):
            disarm_pause_guard(data)
            return
        state = vm.inspect(data['container_id'])
        if state['Id'] != data['container_id'] or state.get('Name', '').lstrip('/') != target(data['code']):
            raise ValueError('Source identity changed; pause recovery needs operator review')
        if state.get('State', {}).get('Paused'):
            vm.run([vm.PODMAN, 'unpause', state['Id']], timeout=30)
        update(data, source_pause_intent=False)
        disarm_pause_guard(data)


def worker(token):
    import signal
    def interrupted(*_):
        raise RuntimeError('Copy worker interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    # Web/helper can update status while a worker runs. A separate lease prevents
    # overlapping workers without holding the per-request status lock all day.
    with (folder(token) / 'worker.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        data = read(token)
        try:
            if data['direction'] == 'send':
                send(data)
            elif data['state'] == 'importing':
                import_copy(data)
            else:
                raise ValueError('Invalid worker state')
        except BaseException as error:
            if data['direction']=='send' and data.get('receiver'):
                try:
                    remote=peer_request(data['peer'],'POST','/'+data['receiver']+'/abort',{})
                    if remote['state']=='ready':
                        if spool(token).exists():shutil.rmtree(spool(token))
                        update(data,state='ready',destination_outcome_unknown=False)
                        return
                    update(data,remote_cleanup_pending=remote['state'] not in TERMINAL,
                           destination_outcome_unknown=remote['state'] not in TERMINAL)
                except Exception:
                    update(data,remote_cleanup_pending=True,destination_outcome_unknown=True)
            cleanup_error = None
            try:
                recover_source(data)
                cleanup_source_tag(data)
                cleanup_destination(data)
                if spool(token).exists():
                    shutil.rmtree(spool(token))
            except Exception as cleanup:
                cleanup_error = str(cleanup)
            update(data, state='failed', error=str(error)[:1000], cleanup_error=cleanup_error)
            raise
        finally:
            # Remove only our transfer tag. A successful destination container
            # retains its immutable image; preexisting images are never removed.
            if data.get('image_tag'):
                try:
                    if image_exists(data['image_tag']):vm.run([vm.PODMAN, 'untag', data['image'], data['image_tag']], timeout=30)
                    if data['state'] != 'ready' and not data.get('image_existed', True) and image_exists(data['image']):
                        remove_unreferenced_image(data['image'])
                except Exception as error:
                    update(data, cleanup_error=str(error)[:1000])


def dispatch(args):
    action, *values = args
    if action == 'instance-copy-list' and not values:
        jobs = [read(p.parent.name) for p in STATE.glob('*/status.json')] if STATE.exists() else []
        return [public(d) for d in sorted(jobs,key=lambda d:d['created'],reverse=True) if d.get('direction')=='send'][:20]
    if action == 'instance-copy-local-status' and len(values) == 1:
        data=read(values[0])
        if data.get('direction')!='send':raise ValueError('Not an outgoing copy')
        return public(data)
    if action == 'instance-copy-capabilities' and len(values) == 1:
        return capabilities(values[0])
    if action == 'instance-copy-plan' and len(values) == 3:
        return source_plan(*values)
    if action == 'instance-copy-create' and len(values) == 2:
        return create(json.loads(values[0]), values[1])
    if action == 'instance-copy-begin' and len(values) == 2:
        return begin(values[0], json.loads(values[1]))
    if action == 'instance-copy-chunk' and len(values) == 5:
        return receive_chunk(values[0], values[1], values[2], int(values[3]), values[4])
    if action == 'instance-copy-abort' and len(values) == 2:
        return abort_receive(*values)
    if action == 'instance-copy-finish' and len(values) == 2:
        return finish_receive(*values)
    if action == 'instance-copy-status' and len(values) == 2:
        return public(read(*values))
    raise ValueError('Invalid peer copy operation')


def validate_native_tree(root):
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                raise ValueError('Mounted data contains a socket/device/FIFO; source was left unchanged')


def cleanup_orphan_chunks():
    try:descriptor=os.open(INBOX,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    except FileNotFoundError:return 0
    import pwd
    uid=pwd.getpwnam('mykosweb').pw_uid;removed=0
    try:
        info=os.fstat(descriptor)
        if info.st_uid!=uid or info.st_mode&0o077:raise ValueError('Unsafe transfer inbox')
        for name in os.listdir(descriptor):
            if not re.fullmatch(r'[0-9a-f]{32}\.chunk',name):continue
            info=os.stat(name,dir_fd=descriptor,follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and info.st_uid==uid and info.st_nlink==1 and not info.st_mode&0o077 and time.time()-info.st_mtime>3600:
                os.unlink(name,dir_fd=descriptor);removed+=1
    finally:os.close(descriptor)
    return removed


def housekeeping():
    """Reap only expired/orphaned journals, never an active worker or ready copy."""
    vm.safe_dir(STATE)
    results = []
    removed=cleanup_orphan_chunks()
    if removed:results.append({'orphan_chunks_removed':removed})
    with lock():
        for path in STATE.glob('*/status.json'):
            token = path.parent.name
            try:
                identifier(token)
                with (folder(token) / 'worker.lock').open('a') as handle:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    with lock(token):
                        data = read(token)
                        # A queued systemd unit has not acquired its lease yet.
                        status = subprocess.run(['/usr/bin/systemctl','is-active','mykos-peer-copy-'+token+'.service'], capture_output=True, text=True, timeout=10)
                        if status.stdout.strip() in ('active','activating','reloading','deactivating'):
                            continue
                        expired = time.time() - data['created'] > LIFETIME
                        orphaned = data['state'] in ('capturing','sending','waiting','importing','verifying') or (data['state']=='queued' and time.time()-data['created']>60)
                        if not expired and not orphaned and not data.get('cleanup_error') and not data.get('source_tag_cleanup') and not data.get('remote_cleanup_pending'):
                            continue
                        if data['state'] == 'ready':
                            # Keep completion receipts and the user-owned copy.
                            continue
                        try:
                            if data.get('remote_cleanup_pending'):
                                remote=peer_request(data['peer'],'POST','/'+data['receiver']+'/abort',{})
                                if remote['state']=='ready':
                                    if spool(token).exists():shutil.rmtree(spool(token))
                                    update(data,state='ready',destination_outcome_unknown=False,remote_cleanup_pending=False)
                                    continue
                                update(data,remote_cleanup_pending=remote['state'] not in TERMINAL,
                                       destination_outcome_unknown=remote['state'] not in TERMINAL)
                            recover_source(data)
                            cleanup_source_tag(data)
                            cleanup_destination(data)
                            if spool(token).exists():shutil.rmtree(spool(token))
                            if data.get('image_tag') and image_exists(data['image_tag']):
                                vm.run([vm.PODMAN,'untag',data['image'],data['image_tag']],timeout=30)
                            if data.get('image') and not data.get('image_existed',True) and image_exists(data['image']):
                                remove_unreferenced_image(data['image'])
                            update(data,state='expired' if expired else 'failed',cleanup_error=None,
                                   error=data.get('error','Interrupted transfer cleaned; source retained'))
                        except Exception as error:
                            update(data,cleanup_error=str(error)[:1000])
                        results.append(public(data))
            except Exception:
                # Do not guess ownership from corrupt metadata; preserve evidence.
                continue
    return results
