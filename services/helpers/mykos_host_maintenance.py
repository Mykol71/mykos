"""Local, journaled reboot preparation. No automatic cold-start fallback."""
import contextlib
import contextvars
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import time
import uuid

import mykos_vm as vm

ROOT = Path('/var/lib/mykos/host-maintenance')
BYPASS = contextvars.ContextVar('maintenance_worker', default=False)
TERMINAL = ('recovered', 'cancelled')


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def write(value):
    from mykos_snapshots import durable_json
    vm.safe_dir(ROOT)
    value['updated_at'] = int(time.time())
    durable_json(ROOT/'status.json', value)


def read():
    path = ROOT/'status.json'
    if not path.exists() and not path.is_symlink():
        return {'state': 'idle', 'instances': []}
    st = path.lstat()
    if path.is_symlink() or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise ValueError('Unsafe maintenance journal')
    value = json.loads(path.read_text())
    if value.get('format') != 1:
        raise ValueError('Unsupported maintenance journal')
    return value


def admission():
    if not BYPASS.get() and read()['state'] not in ('idle', *TERMINAL):
        raise ValueError('Host maintenance is active; resume saved instances before changing workloads')


@contextlib.contextmanager
def lock():
    vm.safe_dir(ROOT)
    path = ROOT/'operation.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        token = BYPASS.set(True)
        try:
            yield
        finally:
            BYPASS.reset(token)
    finally:
        os.close(fd)


def run(args, timeout=30):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()


def inventory():
    ids = run([vm.PODMAN, 'ps', '-q', '--no-trunc']).split()
    return json.loads(run([vm.PODMAN, 'inspect', *ids])) if ids else []


def blockers():
    reasons = []
    units = run(['systemctl', 'list-units', '--state=active,activating,deactivating', '--no-legend', '--plain', '--type=service',
                 'mykos-stage@*', 'mykos-image-build@*', 'mykos-terminal@*', 'mykos-peer-copy-*',
                 'mykos-repo-sync.service', 'mykos-image-reset-*', 'mykos-image-remove-*',
                 'mykos-snapshot-preview-[0-9a-f]*.service', 'mykos-purge.service'])
    if units:
        reasons.append('Active MYKOS work: ' + ', '.join(x.split()[0] for x in units.splitlines()))
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if (path/'comm').read_text().strip() in ('dnf', 'yum', 'rpm', 'packagekitd'):
                reasons.append('Package manager is active (PID ' + path.name + ')')
        except OSError:
            pass
    # Transfers may be waiting between transient worker invocations.
    for path in Path('/var/lib/mykos/instance-transfers').glob('*/status.json'):
        value = json.loads(path.read_text())
        if value.get('state') not in ('ready', 'failed', 'cancelled', 'expired'):
            reasons.append('Instance transfer has unfinished work')
            break
    return reasons


def _plan():
    import mykos_linux as linux
    import mykos_snapshots as snap
    reasons = blockers()
    try:
        default_kernel = Path(run(['/usr/sbin/grubby', '--default-kernel'])).name.removeprefix('vmlinuz-')
    except (OSError, subprocess.SubprocessError):
        default_kernel = None
    guests = []
    needed = 5 * 1024**3
    host_ram = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    for state in inventory():
        name = state['Name'].lstrip('/')
        code = name[:8]
        guest = {'name': name, 'container_id': state['Id'], 'code': code, 'mode': 'memory',
                 'pid': state['State']['Pid'], 'started_at': state['State']['StartedAt'], 'issues': []}
        try:
            if not re.fullmatch(r'[0-9]{8}(?:\.[A-Za-z0-9_.-]+)?', name):
                raise ValueError('Unmanaged, base, or temporary container must be handled separately')
            if vm.record_path(code).is_file():
                record = vm.read(code)
                if record.get('name') != name or record.get('role') != 'instance' or not record.get('ready'):
                    raise ValueError('VM is not a ready persistent instance')
                guest['kind'] = 'vm'
            else:
                actual, checked = linux.inspect(code)
                if actual != name or checked['Id'] != state['Id']:
                    raise ValueError('Instance identity is ambiguous')
                linux.require_persistent(state)
                guest['kind'] = 'native'
                try:
                    restriction = getattr(snap, 'require_native_ram_snapshots', None)
                    if restriction:
                        restriction(state)
                    snap.criu_binary()
                    snap.check_native_compatibility(state)
                    snap.native_data_mounts(state)
                    if default_kernel != os.uname().release:
                        raise ValueError('Next boot kernel differs or cannot be verified; native RAM compatibility is unverified')
                except (OSError, ValueError, RuntimeError) as exc:
                    guest['mode'] = 'restart'
                    guest['issues'].append('RAM unavailable: ' + str(exc))
            if guest['mode'] == 'memory':
                limit = int(state.get('HostConfig', {}).get('Memory') or host_ram)
                if guest['kind'] == 'vm':
                    limit = max(int(record.get('ram_mib', 2048)) * 1024**2 + 256 * 1024**2,
                                int(state.get('HostConfig', {}).get('Memory') or 0))
                    sources = [vm.storage(code)/'storage']
                else:
                    if state.get('GraphDriver', {}).get('Name') != 'overlay':
                        raise ValueError('Native preservation requires overlay storage')
                    sources = [Path(state['GraphDriver']['Data']['UpperDir'])]
                    sources += [Path(x['source']) for x in snap.native_data_mounts(state)]
                filesystem_bytes = 0
                for source in set(sources):
                    if source.is_symlink() or not source.is_dir():
                        raise ValueError('Cannot verify preservation storage')
                    try:
                        size = run(['/usr/bin/ionice','-c','3','/usr/bin/nice','-n','15',
                                    '/usr/bin/du','-sx','-B1','--count-links','--',str(source)], timeout=8)
                    except subprocess.SubprocessError:
                        raise ValueError('Storage estimate timed out or failed; retry readiness after disk activity settles')
                    filesystem_bytes += int(size.split()[0])
                guest['preservation_estimate_bytes'] = filesystem_bytes + limit * 2
                needed += guest['preservation_estimate_bytes']
        except (OSError, ValueError, RuntimeError) as exc:
            guest['mode'] = 'blocked'
            guest['issues'].append(str(exc))
            reasons.append(name + ': ' + str(exc))
        guests.append(guest)
    free = shutil.disk_usage(vm.storage_root()).free
    if free < needed:
        reasons.append('Insufficient preservation headroom: need at least ' + str(needed) + ' bytes')
    value = {'hostname': socket.gethostname(), 'boot_id': boot_id(), 'kernel': os.uname().release,
            'blockers': reasons, 'instances': guests, 'required_free_bytes': needed, 'free_bytes': free,
            'warning': 'Preparation stops instances. Native RAM recovery across kernel changes is not guaranteed. '
                       'Unsupported RAM requires explicit restart consent. Recovery snapshots are retained.'}
    value['plan_token'] = fingerprint({**{k:value[k] for k in ('hostname','boot_id','kernel')},
        'instances':[{k:item.get(k) for k in ('name','container_id','code','kind','mode','pid','started_at','issues')}
                     for item in guests]})
    return value


class ReadinessTimeout(Exception):
    pass


READINESS_SECONDS = 60


def plan():
    # Inspection is bounded before a prepare request can publish stop intent.
    def expired(signum, frame):
        raise ReadinessTimeout('Readiness inspection timed out')
    handler = signal.signal(signal.SIGALRM, expired)
    previous = signal.setitimer(signal.ITIMER_REAL, READINESS_SECONDS)
    started = time.monotonic()
    try:
        return _plan()
    except ReadinessTimeout:
        raise ValueError('Readiness inspection exceeded its time limit; no preparation was started. Retry after host activity settles')
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, handler)
        if previous[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, max(.001, previous[0] - (time.monotonic() - started)), previous[1])


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def schedule(action):
    # Fixed unit names and executable: the browser supplies no command or unit name.
    run(['systemctl', 'start', '--no-block', 'mykos-host-maintenance-' + action + '.service'])


def prepare(hostname, accepted, plan_token):
    if hostname != socket.gethostname():
        raise ValueError('Confirm the exact hostname')
    if not isinstance(accepted, list) or not all(isinstance(x, str) and re.fullmatch(r'[0-9]{8}', x) for x in accepted):
        raise ValueError('Invalid restart consent')
    with lock():
        if read()['state'] not in ('idle', *TERMINAL):
            raise ValueError('A maintenance session already exists; inspect or resume it')
        # Serialize admission with all lifecycle operations.
        with vm.locked():
            value = plan()
            if value['plan_token'] != plan_token:
                raise ValueError('Readiness plan changed; review it again before stopping instances')
            if value['blockers']:
                raise ValueError('; '.join(value['blockers']))
            required = {x['code'] for x in value['instances'] if x['mode'] == 'restart'}
            available = {x['code'] for x in value['instances'] if x['mode'] in ('memory','restart')}
            if len(accepted) != len(set(accepted)) or not required.issubset(accepted) or not set(accepted).issubset(available):
                raise ValueError('Explicit restart consent must include every unsupported instance and only reviewed instances')
            for item in value['instances']:
                if item['code'] in accepted:
                    item['mode'] = 'restart'
                    item['restart_accepted'] = True
            value.update(format=1, id=uuid.uuid4().hex, state='queued', created_at=int(time.time()))
            for item in value['instances']:
                item['phase'] = 'pending'
            write(value)
        try:
            schedule('prepare')
        except BaseException:
            value['state'] = 'cancelled'
            write(value)
            raise
        return value


def verify_snapshot(item):
    import mykos_snapshots as snap
    folder, metadata = snap.read_snapshot(item['code'], item['snapshot'])
    if metadata['capture_mode'] == 'memory':
        vm.restore_manifest(metadata['configuration'], folder/'storage')
        archive = folder/'storage/.mykos-checkpoint/memory.gz'
    else:
        archive = folder/'checkpoint.tar.gz'
    h = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
    with gzip.open(archive, 'rb') as stream:
        while stream.read(4 * 1024**2):
            pass
    result = {'archive_sha256': h.hexdigest(), 'metadata_sha256': hashlib.sha256((folder/'metadata.json').read_bytes()).hexdigest()}
    if item.get('verification') and item['verification'] != result:
        raise ValueError('Preservation snapshot changed; recovery stopped')
    return result


def same_original(item, running=True):
    state = vm.inspect(item['name'])
    if state['Id'] != item['container_id']:
        raise ValueError('Instance identity changed; refusing to affect replacement')
    if running and (not state['State']['Running'] or state['State']['Pid'] != item['pid'] or state['State']['StartedAt'] != item['started_at']):
        raise ValueError('Instance runtime changed during preparation')
    return state


def prepare_worker():
    import mykos_snapshots as snap
    import mykos_linux as linux
    with lock():
        value = read()
        if value['state'] != 'queued' or value['boot_id'] != boot_id():
            return
        try:
            value['state'] = 'saving'
            write(value)
            if blockers():
                raise ValueError('; '.join(blockers()))
            if {x['Id'] for x in inventory()} != {x['container_id'] for x in value['instances']}:
                raise ValueError('Running workload inventory changed')
            for item in value['instances']:
                same_original(item)
                item['phase'] = 'capturing'
                write(value)
                if item['mode'] == 'memory':
                    metadata = snap.capture(item['code'])
                    item['snapshot'] = metadata['id']
                    item['phase'] = 'verifying'
                    write(value)
                    item['verification'] = verify_snapshot(item)
                # Journal intent before stop; a crash never loses ownership.
                item['phase'] = 'stopping'
                write(value)
                same_original(item)
                (vm.stop if item['kind'] == 'vm' else linux.stop)(item['code'])
                if same_original(item, False)['State']['Running']:
                    raise ValueError('Instance did not stop')
                item['phase'] = 'saved'
                write(value)
            if inventory() or blockers():
                raise ValueError('New work appeared; reboot is blocked')
            value['state'] = 'ready'
            write(value)
            os.sync()
        except BaseException as exc:
            value.update(state='attention', error=str(exc))
            write(value)
            raise


def request_resume(hostname=None, restart=None):
    with lock():
        value = read()
        if value['state'] not in ('ready', 'attention', 'queued', 'saving', 'recovering', 'resume-requested', 'verifying-reboot'):
            raise ValueError('No saved maintenance session to resume')
        restart = restart or []
        if not isinstance(restart, list) or len(restart) != len(set(restart)):
            raise ValueError('Invalid restart consent')
        if restart:
            if hostname != socket.gethostname() or value['state'] != 'attention':
                raise ValueError('Confirm the exact hostname to accept restart after a failed recovery')
            selected = [x for x in value['instances'] if x['code'] in restart]
            if len(selected) != len(restart) or any(x['phase'] != 'restore-failed' or x['mode'] != 'memory' for x in selected):
                raise ValueError('Select only failed RAM recoveries')
            for item in selected:
                if same_original(item, False)['State']['Running']:
                    raise ValueError('Instance is running; refusing a cold restart')
            for item in selected:
                item['mode'] = 'restart'
                item['restart_after_failure_accepted'] = True
        # Cannot enter while a preparation worker holds the operation lock.
        previous = value['state']
        value['state'] = 'resume-requested'
        write(value)
        try:
            schedule('recover')
        except BaseException:
            value['state'] = previous
            write(value)
            raise
        return value


def recover_worker():
    import mykos_snapshots as snap
    import mykos_linux as linux
    with lock():
        value = read()
        if value['state'] in ('idle', *TERMINAL):
            return
        value['state'] = 'recovering'
        write(value)
        errors = []
        for item in value['instances']:
            if item['phase'] in ('recovered', 'untouched'):
                continue
            try:
                state = same_original(item, False)
                if item['phase'] in ('pending', 'capturing', 'verifying') and state['State']['Running']:
                    if value['boot_id'] != boot_id() or state['State']['Pid'] != item['pid'] or state['State']['StartedAt'] != item['started_at']:
                        raise ValueError('Unexpected running instance after interruption; inspect before recovery')
                    item['phase'] = 'untouched'
                    write(value)
                    continue
                if state['State']['Running']:
                    raise ValueError('Instance already running; automatic overwrite refused')
                if item['phase'] not in ('saved', 'stopping', 'restore-failed', 'restoring'):
                    raise ValueError('Incomplete preservation; manual inspection required')
                item['phase'] = 'restoring'
                write(value)
                if item['mode'] == 'memory':
                    verify_snapshot(item)
                    if item['kind'] == 'native' and value['kernel'] != os.uname().release:
                        raise ValueError('Kernel changed; native RAM recovery needs operator review. No cold start performed')
                    snap.restore(item['code'], item['snapshot'])
                else:
                    (vm.start if item['kind'] == 'vm' else linux.start)(item['code'])
                if not vm.inspect(item['name'])['State']['Running']:
                    raise ValueError('Instance did not become running')
                item['phase'] = 'recovered'
                item.pop('error', None)
            except BaseException as exc:
                item.update(phase='restore-failed', error=str(exc))
                errors.append(item['code'])
            write(value)
        value.update(state='attention' if errors else 'recovered', recovery_boot_id=boot_id())
        if not errors:
            value.pop('error', None)
        write(value)


def reboot(hostname):
    if hostname != socket.gethostname():
        raise ValueError('Confirm the exact hostname')
    with lock():
        value = read()
        if value['state'] != 'ready' or value['boot_id'] != boot_id():
            raise ValueError('Complete preparation before rebooting')
        if inventory() or blockers():
            raise ValueError('New work appeared; reboot blocked')
        for item in value['instances']:
            same_original(item, False)
        value['state'] = 'verifying-reboot'
        write(value)
        try:
            schedule('reboot')
        except BaseException:
            value['state'] = 'ready'
            write(value)
            raise
        return value


def reboot_worker():
    with lock():
        value = read()
        if value['state'] != 'verifying-reboot' or value['boot_id'] != boot_id():
            return
        try:
            if inventory() or blockers():
                raise ValueError('New work appeared; reboot blocked')
            for item in value['instances']:
                same_original(item, False)
                if item['mode'] == 'memory':
                    verify_snapshot(item)
            # Long checksum reads never keep an HTTP request open, and work
            # started by an operator during those reads still blocks reboot.
            if inventory() or blockers():
                raise ValueError('New work appeared during verification; reboot blocked')
            value['state'] = 'rebooting'
            write(value)
            run(['/usr/bin/systemctl', '--no-block', 'reboot'])
        except BaseException as exc:
            value.update(state='attention', error=str(exc))
            write(value)
            raise


def dispatch(args):
    action = args[0]
    if action == 'maintenance-plan' and len(args) == 1:
        return plan()
    if action == 'maintenance-status' and len(args) == 1:
        return read()
    if action == 'maintenance-prepare' and len(args) == 4:
        return prepare(args[1], json.loads(args[2]), args[3])
    if action == 'maintenance-resume' and len(args) == 2:
        data = json.loads(args[1])
        if not isinstance(data, dict) or set(data) - {'hostname','restart'}:
            raise ValueError('Invalid recovery selection')
        return request_resume(data.get('hostname'), data.get('restart'))
    if action == 'maintenance-reboot' and len(args) == 2:
        return reboot(args[1])
    raise ValueError('Invalid host maintenance operation')
