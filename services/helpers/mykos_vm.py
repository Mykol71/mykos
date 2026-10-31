"""Persistent Dockur VM templates and instances for rootful Podman.

Capture guest RAM/devices and matching storage, then stop and commit the
template under a readable local image tag. Instances receive independent disks.
"""
import contextlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import mykos_network as network
from mykos_runtime import config as host_config

PODMAN = '/usr/bin/podman'
POS = Path('/usr/local/share/mykos/pos')
ROOT = POS / '.vm'
STORAGE = Path('/home/mykos-vms')
VERSIONS = {**{v: ('windows', v[3:]) for v in ('win11','win10','win8e','win7u','winxp','winme','win2k','win98')},
            **{v: ('windows', v[3:]) for v in ('wns2025','wns2022','wns2019','wns2016')},
            **{v: ('macos', v[3:]) for v in ('mac15','mac14','mac13','mac12')},
            **{v: ('ubuntu', v[6:]) for v in ('ubuntu20.04','ubuntu22.04','ubuntu24.04')}}


def run(args, timeout=3600, env=None):
    from mykos_runtime import podman_scope
    result = subprocess.run(podman_scope(args), capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or result.stdout[-2000:] or 'Command failed')
    return result.stdout.strip()


def safe_dir(path):
    path = Path(path)
    for parent in [*reversed(path.parents), path]:
        if parent.is_symlink(): raise ValueError('Symlink directory rejected: ' + str(parent))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir(): raise ValueError('Expected directory: ' + str(path))
    status = path.stat()
    if status.st_uid != os.geteuid() or status.st_mode & 0o022:
        raise ValueError('VM directories must be owned by the operator and not writable by other users')


@contextlib.contextmanager
def locked():
    safe_dir(ROOT)
    path = ROOT / 'registry.lock'
    if path.is_symlink(): raise ValueError('Symlink lock rejected')
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def record_path(key):
    if key not in VERSIONS and not re.fullmatch(r'(?:[0-9]{8}|preview-[0-9a-f]{32})', key):
        raise ValueError('Invalid VM selection')
    return ROOT / (key + '.json')


def read(key):
    path = record_path(key)
    if path.is_symlink(): raise ValueError('Symlink metadata rejected')
    if not path.exists(): raise ValueError('No MYKOS VM record for ' + key)
    if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o022:
        raise ValueError('Unsafe VM metadata')
    return json.loads(path.read_text())


def save(key, data):
    fd, temporary = tempfile.mkstemp(prefix='.record-', dir=str(ROOT))
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, indent=2); handle.write('\n')
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, record_path(key))
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def inspect(name):
    return json.loads(run([PODMAN, 'container', 'inspect', name], timeout=30))[0]


def stop_container(name, grace_seconds=120):
    """Accept a lost-monitor error only after verifying the guest has stopped."""
    before = inspect(name).get('State', {})
    try:
        run([PODMAN, 'stop', '--time', str(grace_seconds), name], timeout=grace_seconds + 30)
    except RuntimeError as error:
        if 'conmon exited prematurely' not in str(error) and 'conmon process killed' not in str(error):
            raise
        after = inspect(name).get('State', {})
        pid = before.get('Pid', 0)
        if after.get('Running') or after.get('Status') not in ('exited', 'stopped') or (pid and Path('/proc/' + str(pid)).exists()):
            raise RuntimeError('Container monitor was lost and the guest has not been verified stopped; storage was left intact') from error
    if inspect(name).get('State', {}).get('Running'):
        raise RuntimeError('Container did not stop; storage was left intact')


def settings():
    from mykos_runtime import config
    cfg = config()
    host = cfg.get('desktop_host')
    if not host:
        route = run(['/usr/sbin/ip', 'route', 'get', '8.8.8.8'], timeout=10).split()
        host = route[route.index('src') + 1]
    host = str(ipaddress.IPv4Address(host))
    bind = str(ipaddress.IPv4Address(cfg.get('desktop_bind') or host))
    return host, bind


def storage_root():
    from mykos_runtime import config
    path = Path(config().get('desktop_storage') or STORAGE)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('desktop_storage must be an absolute directory')
    return path


def storage(key):
    record_path(key)  # Validate the component before joining paths.
    return storage_root() / key


def preflight(os_name, ram_mib=2048):
    if os_name not in VERSIONS: raise ValueError('Unsupported desktop OS')
    for device in ('/dev/kvm', '/dev/net/tun'):
        if not Path(device).exists(): raise ValueError('Required device unavailable: ' + device)
    if VERSIONS[os_name][0] == 'macos' and 'avx2' not in Path('/proc/cpuinfo').read_text().lower():
        raise ValueError('macOS requires an AVX2-capable CPU')
    safe_dir(storage_root())
    available = re.search(r'^MemAvailable:\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M)
    required_gib = ram_mib // 1024 + 1
    if available and int(available.group(1)) < required_gib * 1024**2:
        raise ValueError('At least %s GiB of available host memory is required' % required_gib)
    if shutil.disk_usage(storage_root()).free < 32 * 1024**3:
        raise ValueError('At least 32 GiB of free VM storage is required')


def ubuntu_iso(version, unattended=False):
    """Resolve a version-matched desktop installer from Canonical's index."""
    import urllib.request
    bases = ['https://releases.ubuntu.com/' + version + '/']
    if version == '20.04': bases.append('https://old-releases.ubuntu.com/releases/20.04.6/')
    for base in bases:
        try:
            with urllib.request.urlopen(base, timeout=30) as response:
                text = response.read(2 * 1024**2).decode('utf-8')
            edition = 'live-server' if unattended else 'desktop'
            names = set(re.findall(r'ubuntu-' + re.escape(version) + r'(?:\.\d+)?-' + edition + r'-amd64\.iso(?=["\s<])', text))
            if names:
                name = max(names, key=lambda n: tuple(int(p) for p in re.search(r'(\d+(?:\.\d+)+)', n).group(1).split('.')))
                return base + name
        except (OSError, ValueError): pass
    raise ValueError('No matching official Ubuntu installer ISO found for ' + version)


def allocate(bind):
    used = set()
    for path in ROOT.glob('*.json'):
        value = read(path.stem)
        used.update((value['console_port'], value['desktop_port']))
    # Check actual Podman mappings as well as sockets; stopped MYKOS VMs retain
    # their reservations in the registry so restarting never changes the URL.
    for item in json.loads(run([PODMAN, 'ps', '-a', '--format', 'json'], timeout=30)):
        for port in item.get('Ports') or []:
            if isinstance(port, dict) and port.get('host_port'): used.add(int(port['host_port']))
    def free(start, end):
        for port in range(start, end):
            if port in used: continue
            try:
                with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                    tcp.bind((bind, port)); udp.bind((bind, port))
                used.add(port); return port
            except OSError: continue
        raise ValueError('No free desktop port in configured range')
    return free(18006, 19006), free(23000, 24000)


def tree_copy(source, destination, reflink='auto'):
    if source.is_symlink() or not source.is_dir(): raise ValueError('Expected regular storage directory')
    if destination.exists() or destination.is_symlink(): raise ValueError('Storage already exists')
    for directory, dirs, files in os.walk(source, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise ValueError('Unsupported storage entry: ' + str(path))
    run(['/usr/bin/cp', '-a', '--reflink=' + reflink, '--sparse=auto', str(source), str(destination)],
        timeout=60 if reflink == 'always' else 3600)


def template_image(os_name):
    if os_name not in VERSIONS: raise ValueError('Select a template OS')
    return 'localhost/' + ('macos' + os_name[3:] if os_name.startswith('mac') else os_name) + ':latest'


def launch_image(value):
    """Keep readable Podman references while pinning each checkpoint's image."""
    name = value.get('image_name') or template_image(value['os'])
    try:
        current = json.loads(run([PODMAN, 'image', 'inspect', name]))[0]['Id']
    except RuntimeError:
        current = None
    if current != value['image']:
        # A later recapture may move :latest; retain this checkpoint's exact image.
        name = template_image(value['os']).removesuffix(':latest') + ':checkpoint-' + value['image'].removeprefix('sha256:')[:12]
        run([PODMAN, 'tag', value['image'], name])
    return name


def launch(key, value):
    kind, version = VERSIONS[value['os']]
    ram_mib = value.get('ram_mib', 4096)
    installing = value['role'] == 'template' and not value.get('ready') and not value.get('installation_complete')
    allowed = (2048, 4096) if installing and kind == 'macos' else (2048, 8192) if installing and value['os'].startswith('win') else (2048,)
    if type(ram_mib) is not int or ram_mib not in allowed:
        raise ValueError('Extra RAM is limited to unfinished image installation; runtime containers require 2048 MiB')
    desktop = 3389 if kind == 'windows' else 5900
    args = [PODMAN, 'run', '-d', '--name', value['name'], '--hostname', value['name'],
            '--device=/dev/kvm', '--device=/dev/net/tun', '--cap-add=NET_ADMIN',
            '--security-opt=label=disable', '--stop-timeout=120', '--restart=' + ('no' if value['role'] in ('template','preview') else 'unless-stopped'), '--pull=never',
            '--label', 'io.mykos.vm.os=' + value['os'], '--label', 'io.mykos.vm.role=' + value['role'],
            '--label', 'io.mykos.vm.host=' + value['host'],
            '-e', ('BOOT=' + value['boot']) if kind == 'ubuntu' else 'VERSION=' + version,
            '-e', 'RAM_SIZE=' + str(ram_mib) + 'M', '-e', 'RAM_MINIMUM=2G', '-e', 'CPU_CORES=2', '-e', 'DISK_SIZE=64G',
            '-v', str(storage(key) / 'storage') + ':/storage:rw',
            '-p', '%s:%s:8006/tcp' % (value['bind'], value['console_port']),
            '-p', '%s:%s:%s/tcp' % (value['bind'], value['desktop_port'], desktop),
            '-p', '%s:%s:%s/udp' % (value['bind'], value['desktop_port'], desktop)]
    if value['role']=='preview':args+=['--label','io.mykos.snapshot.preview='+value['preview_token']]
    building_image = value['role'] == 'template' and not value.get('ready')
    # Explicitly reset the environment when launching instances from a committed
    # build image, so build priority cannot leak into ordinary guests.
    args += ['-e', 'MYKOS_BUILD_NICE=' + ('1' if building_image else '0')]
    if building_image and kind == 'windows':
        args += ['--cap-add=SYS_NICE']
    if kind == 'ubuntu':
        args += ['-e', 'SERIAL=unix:/dev/shm/mykos-serial.sock,server=on,wait=off']
    # Never inherit an installation entry point from a committed build image.
    args += ['-e', 'MYKOS_UNATTENDED=']
    if value.get('unattended'):
        from mykos_image_build import launch_arguments
        args += launch_arguments(value, key)
    args += network.arguments(host_config(), value['name'])
    credentials = storage(key if value['role']=='preview' else value['os']) / 'windows.env'
    if kind == 'windows':
        preserve = value['role'] == 'instance' or value.get('ready') or value.get('installation_complete')
        args += ['-e', 'MYKOS_PRESERVE_WINDOWS_DISK=' + ('Y' if preserve else 'N')]
        if value['role'] == 'instance' or value.get('installation_complete'):
            args += ['-e', 'MANUAL=Y', '-e', 'SHUTDOWN=N']
        if credentials.is_symlink() or not credentials.is_file(): raise ValueError('Missing template credentials')
        args += ['--env-file', str(credentials)]
        oem = storage(key if value['role']=='preview' else value['os']) / 'oem'
        if oem.is_dir() and not oem.is_symlink():
            args += ['-v', str(oem) + ':/oem:ro']
    if value.get('guest_agent') and kind in ('windows','ubuntu'):
        args += ['-e', 'QGA=/dev/shm/mykos-qga.sock']
    for key,setting in value.get('cpu_env', {}).items():
        if key not in ('CPU_MODEL','CPU_FLAGS','HV','VMX'): raise ValueError('Unsupported CPU profile setting')
        args += ['-e', key + '=' + setting]
    loader = Path(__file__).with_name('mykos-checkpoint-entry')
    args += ['-v', str(loader) + ':/run/mykos-checkpoint-entry:ro', '--entrypoint', '/usr/bin/tini']
    args += [launch_image(value), '-s', '--', '/run/mykos-checkpoint-entry']
    cid = run(args, timeout=120)
    if not inspect(value['name']).get('State', {}).get('Running'):
        raise RuntimeError('Desktop container exited during startup; inspect podman logs ' + value['name'])
    return cid


def console_path(value):
    key = value['os'] if value['role'] == 'template' else value['name'][:8]
    record_path(key)
    return '/mykos/console/' + key + '/'


def console_target(key):
    record_path(key)
    value = read(key)
    expected_role = 'template' if key in VERSIONS else 'instance'
    if value.get('role') != expected_role or value.get('os') not in VERSIONS:
        raise ValueError('Not a managed VM console')
    if expected_role == 'template' and value['name'] != key:
        raise ValueError('Image console identity mismatch')
    if expected_role == 'instance' and not re.fullmatch(re.escape(key) + r'(?:\.[A-Za-z0-9_.-]+)?', value['name']):
        raise ValueError('Container console identity mismatch')
    state = inspect(value['name'])
    labels = state.get('Config', {}).get('Labels') or {}
    if not state.get('State', {}).get('Running') or labels.get('io.mykos.vm.role') != expected_role or labels.get('io.mykos.vm.os') != value['os']:
        raise ValueError('Managed console is not running')
    host, bind = settings()
    mapped = state.get('NetworkSettings', {}).get('Ports', {}).get('8006/tcp') or []
    for item in mapped:
        address = str(ipaddress.IPv4Address(item['HostIp']))
        port = int(item['HostPort'])
        if address in (host, bind, '127.0.0.1', '0.0.0.0') and port == value['console_port'] and 18006 <= port <= 19005:
            return {'host':'127.0.0.1' if address == '0.0.0.0' else address, 'port':port, 'container_id':state.get('Id')}
    raise ValueError('Console port does not match the managed container')


def links(value):
    return {k: value[k] for k in ('os','name','role','host','console_port','desktop_port','ready')} | {
        'ram_mib': value.get('ram_mib', 2048),
        'unattended': value.get('unattended', False),
        'capture_mode': value.get('capture_mode', 'disk'),
        'last_start': value.get('last_start'),
        'terminal_transport': 'serial' if value['os'].startswith('ubuntu') else 'powershell' if VERSIONS[value['os']][0] == 'windows' else 'ssh' if value['os'].startswith('mac') else None,
        'root_terminal_url': '/mykos/api/v2/terminal/' + value['name'][:8] if value['os'].startswith('ubuntu') and value['role'] == 'instance' else None,
        'console_url': console_path(value),
        'direct_console_url': 'http://%s:%s/' % (value['host'], value['console_port']),
        'login_supported': value['os'] not in ('win98','winme','win2k'),
        'protocol': 'rdp' if VERSIONS[value['os']][0] == 'windows' else 'vnc',
        'login_url': ('rdp' if VERSIONS[value['os']][0] == 'windows' else 'vnc') + '://%s:%s' % (value['host'], value['desktop_port'])}


def checkpoint_cpu_profile(os_name):
    kind = VERSIONS[os_name][0]
    if kind == 'macos': return {'CPU_FLAGS':'-invtsc'}
    if kind not in ('windows', 'ubuntu'): return {}
    profile = {'CPU_MODEL':'host', 'CPU_FLAGS':'migratable=on,-invtsc'}
    if kind == 'windows': profile['HV'] = 'N'
    return profile


def image_memory(os_name, requested=None):
    if os_name not in VERSIONS:
        raise ValueError('Unsupported OS')
    if requested is not None and requested != 2048:
        raise ValueError('VM image builds and runtime guests require 2048 MiB')
    return 2048


def create(os_name, username=None, password=None, ram_mib=None, unattended=False):
    ram_mib = image_memory(os_name, ram_mib)
    # Apple's second-stage installer mounts a 2.5 GiB temporary filesystem.
    # boot_installed lowers this installation-only allowance before capture.
    if unattended and VERSIONS[os_name][0] == 'macos': ram_mib = 4096
    with locked():
        preflight(os_name, ram_mib)
        if record_path(os_name).exists(): raise ValueError('Template exists; resume or prepare it instead')
        # Never replace an unrelated container with a template's name.
        names = json.loads(run([PODMAN, 'ps', '-a', '--format', 'json'], timeout=30))
        if any(os_name in ([item.get('Names')] if isinstance(item.get('Names'), str) else item.get('Names', [])) for item in names):
            raise ValueError('A container already uses the template name ' + os_name)
        kind, _ = VERSIONS[os_name]
        if kind == 'windows' and (not username or not password or any(c in username + password for c in '\r\n\x00')):
            raise ValueError('Supply a Windows username and nonempty single-line password')
        image = 'docker.io/qemux/qemu:latest' if kind == 'ubuntu' else 'docker.io/dockurr/' + kind + ':latest'
        boot = (ubuntu_iso(VERSIONS[os_name][1], unattended=True) if unattended else ubuntu_iso(VERSIONS[os_name][1])) if kind == 'ubuntu' else None
        run([PODMAN, 'pull', image])
        image_id = json.loads(run([PODMAN, 'image', 'inspect', image]))[0]['Id']
        image_name = template_image(os_name)
        run([PODMAN, 'tag', image_id, image_name])
        host, bind = settings(); console, desktop = allocate(bind)
        safe_dir(storage(os_name) / 'storage')
        if kind == 'windows':
            path = storage(os_name) / 'windows.env'
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as handle: handle.write('USERNAME=' + username + '\nPASSWORD=' + password + '\n')
        value = dict(os=os_name, name=os_name, image=image_id, image_name=image_name, role='template', host=host, bind=bind,
                     console_port=console, desktop_port=desktop, ready=False, boot=boot)
        if kind in ('windows','ubuntu'):
            value['guest_agent'] = True
        if kind == 'windows' and os_name not in ('win98','winme','win2k','winxp','win7u'):
            oem = storage(os_name) / 'oem'
            safe_dir(oem)
            shutil.copyfile(Path(__file__).with_name('mykos-windows-pagefile.ps1'), oem / 'mykos-pagefile.ps1')
            pagefile_mib = 12288
            if os_name.startswith('win'): value['build_pagefile_mib'] = pagefile_mib
            (oem / 'install.bat').write_text('@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\\OEM\\mykos-pagefile.ps1 -SizeMiB ' + str(pagefile_mib) + ' > C:\\OEM\\mykos-pagefile.log 2>&1\r\nexit /b %errorlevel%\r\n')
        if unattended and kind == 'windows':
            shutil.copyfile(Path(__file__).with_name('mykos-windows-agent.ps1'), oem/'mykos-agent.ps1')
            shutil.copyfile(Path(__file__).with_name('mykos-windows-finish.ps1'), oem/'mykos-finish.ps1')
            commands=['@echo off',
                r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\OEM\mykos-agent.ps1 > C:\OEM\mykos-agent.log 2>&1',
                'if errorlevel 1 exit /b %errorlevel%',
                r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\OEM\mykos-pagefile.ps1 -SizeMiB 12288 > C:\OEM\mykos-pagefile.log 2>&1',
                'if errorlevel 1 exit /b %errorlevel%',
                r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\OEM\mykos-finish.ps1 -UserName '+username+r' > C:\OEM\mykos-finish.log 2>&1',
                'exit /b %errorlevel%']
            (oem/'install.bat').write_bytes(('\r\n'.join(commands)+'\r\n').encode())
        if unattended:
            from mykos_image_build import stage
            stage(os_name, {'username':username,'password':password}, image_id)
            value['unattended'] = True
        value['ram_mib'] = ram_mib
        value['cpu_env'] = checkpoint_cpu_profile(os_name)
        save(os_name, value)  # Reserve ports even if launch fails; resume can retry.
        launch(os_name, value)
        return links(value)


class QemuMonitor:
    """Bounded HMP connection to the verified container's private monitor."""
    def __init__(self, value):
        state = inspect(value['name'])
        labels = state.get('Config', {}).get('Labels', {})
        pid = state.get('State', {}).get('Pid', 0)
        if not state.get('State', {}).get('Running') or not isinstance(pid, int) or pid <= 0:
            raise ValueError('Resume the template before live capture')
        if labels.get('io.mykos.vm.os') != value['os'] or labels.get('io.mykos.vm.role') != value['role']:
            raise ValueError('Container is not the selected MYKOS VM')
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(20)
        try:
            # /run/shm is an absolute symlink inside these images; use dev/shm
            # through the process root so it resolves to the container's tmpfs.
            self.socket.connect('/proc/%d/root/dev/shm/monitor.sock' % pid)
            self._receive()
        except BaseException:
            self.socket.close()
            raise

    def _receive(self):
        data = b''
        while not data.endswith(b'(qemu) '):
            chunk = self.socket.recv(65536)
            if not chunk: raise RuntimeError('QEMU monitor disconnected')
            data += chunk
            if len(data) > 1024 * 1024: raise RuntimeError('QEMU monitor response too large')
        return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', data.decode('utf-8', 'replace'))

    def command(self, command):
        if any(char in command for char in '\r\n\x00'): raise ValueError('Invalid monitor command')
        self.socket.sendall(command.encode('ascii') + b'\n')
        response = self._receive()
        # HMP echoes edited command lines. Command output starts after its CRLF.
        output = response.split('\n', 1)[-1].replace('\r', '').removesuffix('(qemu) ').strip()
        if re.search(r'(?im)^(?:error|unknown command|could not|cannot|failed|device .*not found)', output):
            raise RuntimeError('QEMU capture command failed: ' + output)
        return output

    def close(self):
        self.socket.close()


def capture_devices(monitor, source):
    if monitor.command('info block-jobs') != 'No active jobs':
        raise ValueError('Wait for QEMU disk jobs to complete before capture')
    blocks = monitor.command('info block')
    devices = []
    for block in re.split(r'\n\s*\n', blocks):
        header = block.splitlines()[0] if block.strip() else ''
        match = re.fullmatch(r'([\w.-]+) \([^\n]+\): (/[^\n]+) \(([^\n]+)\)', header)
        if not match:
            if header and 'not inserted' not in header:
                raise ValueError('Unrecognized QEMU disk; capture aborted: ' + header)
            continue
        device, filename, options = match.groups()
        if 'read-only' in options: continue
        try:
            relative = Path(filename).relative_to('/storage')
        except ValueError:
            raise ValueError('Writable guest disk is outside template storage: ' + filename)
        if not relative.parts or '..' in relative.parts:
            raise ValueError('Writable guest disk is outside template storage: ' + filename)
        # macOS keeps its disks in version subdirectories, such as /storage/15.
        # Validate every component so a directory symlink cannot escape storage.
        disk = source
        for part in relative.parts:
            disk = disk/part
            if disk.is_symlink():
                raise ValueError('Symlink guest disk path is not supported: ' + filename)
        if not disk.is_file():
            raise ValueError('Writable guest disk is missing from template storage: ' + filename)
        devices.append(device)
    if not devices: raise ValueError('No writable guest disk found')
    return devices


def migration_status(output):
    match = re.search(r'(?mi)^(?:Migration status|Status):\s*(\S+)', output)
    return match.group(1).lower() if match else None


def cpu_checkpoint_registers(output):
    registers = {}
    for key in ('RIP', 'EIP', 'CR3'):
        match = re.search(r'\b' + key + r'=([0-9a-fA-F]+)', output)
        if match: registers[key] = match.group(1).lower()
    if 'CR3' not in registers or not any(key in registers for key in ('RIP','EIP')):
        raise RuntimeError('Cannot verify captured CPU execution state')
    return registers


def capture_live(value, source, destination, quiesce=False):
    """Save RAM/devices live, snapshot the matching disks, then resume source."""
    monitor = QemuMonitor(value)
    migrating = False
    running = False
    checkpoint = source/'.mykos-checkpoint'
    try:
        if monitor.command('info status') != 'VM status: running':
            raise ValueError('Template must be running normally before capture')
        running = True
        details = monitor.command('info migrate')
        if 'blocked' in details.lower():
            raise ValueError('Memory capture is blocked. Run ./vm checkpoint-enable ' + value['os'] +
                             ' to apply compatible CPU settings (restarts this template). Details: ' + details)
        if migration_status(details) in ('active','setup','pre-switchover','device','postcopy-active','cancelling'):
            raise ValueError('Another QEMU migration is active')
        devices = capture_devices(monitor, source)
        safe_dir(checkpoint)
        for name in ('memory.gz','manifest.json','restore.request','restore.consumed'):
            if (checkpoint/name).is_symlink(): raise ValueError('Symlink checkpoint file rejected')
        token = '.memory-' + os.urandom(8).hex() + '.tmp'
        # Image builders can dirty RAM faster than gzip can drain live precopy.
        # Pause only this builder for a bounded, consistent startup capture.
        # Ordinary live snapshots retain their existing precopy behavior.
        if quiesce:
            monitor.command('stop')
            if monitor.command('info status') != 'VM status: paused':
                raise RuntimeError('Image builder did not pause for startup capture')
        # gzip can exit successfully even when QEMU cancels its stream. Publish
        # only after BOTH QEMU and gzip have completed, never from the shell.
        uri = 'exec:gzip -1 > /storage/.mykos-checkpoint/' + token + \
              ' && touch /storage/.mykos-checkpoint/' + token + '.done'
        monitor.command('migrate_set_parameter max-bandwidth 4096')  # HMP uses MiB/s.
        migrating = True
        monitor.command('migrate -d "' + uri + '"')
        deadline = time.monotonic() + 300
        progress = time.monotonic()
        while True:
            details = monitor.command('info migrate')
            status = migration_status(details)
            if status == 'completed': break
            if status in ('failed','cancelled'): raise RuntimeError('Memory checkpoint failed: ' + details)
            if time.monotonic() > deadline: raise RuntimeError('Memory checkpoint timed out')
            if time.monotonic() - progress >= 5:
                print('Saving VM memory (' + str(status or 'starting') + '); ' +
                      monitor.command('info status'), flush=True)
                progress = time.monotonic()
            time.sleep(.2)
        migrating = False
        if not monitor.command('info status').startswith('VM status: paused'):
            raise RuntimeError('Source VM did not pause at checkpoint completion')
        registers = cpu_checkpoint_registers(monitor.command('info registers'))
        for device in devices:
            result = monitor.command('qemu-io ' + device + ' "flush"')
            if result: raise RuntimeError('QEMU disk flush failed: ' + result)
        # Wait for gzip's atomic completion marker before publishing matching disks.
        deadline = time.monotonic() + 20
        while not (checkpoint/(token+'.done')).is_file() or not (checkpoint/token).is_file():
            if time.monotonic() > deadline: raise RuntimeError('Compressed checkpoint did not finalize')
            time.sleep(.1)
        if (checkpoint/token).stat().st_size <= 0: raise RuntimeError('Empty memory checkpoint')
        (checkpoint/token).chmod(0o600)
        os.replace(checkpoint/token, checkpoint/'memory.gz')
        manifest = {'format':1, 'os':value['os'], 'image':value['image'],
                    'cpu_env':value.get('cpu_env', {}), 'registers':registers,
                    'qemu_version':monitor.command('info version'),
                    'memory_bytes':(checkpoint/'memory.gz').stat().st_size, 'ram_mib':value.get('ram_mib', 2048)}
        if manifest['memory_bytes'] <= 0: raise RuntimeError('Empty memory checkpoint')
        (checkpoint/'memory.gz').chmod(0o600)
        with (checkpoint/'manifest.json').open('w') as handle:
            json.dump(manifest, handle, indent=2)
        (checkpoint/'manifest.json').chmod(0o600)
        for name in ('restore.request','restore.consumed'):
            (checkpoint/name).unlink(missing_ok=True)
        tree_copy(source, destination, reflink='always')
    finally:
        try:
            if migrating:
                monitor.command('migrate_cancel')
                deadline = time.monotonic() + 15
                while migration_status(monitor.command('info migrate')) in ('active','setup','cancelling'):
                    if time.monotonic() > deadline: raise RuntimeError('QEMU migration cancellation did not finish')
                    time.sleep(.1)
                migrating = False
            if running and monitor.command('info status').startswith('VM status: paused'):
                monitor.command('cont')
            if running and monitor.command('info status') != 'VM status: running':
                raise RuntimeError('Template did not resume; inspect its QEMU monitor')
        finally:
            monitor.close()
            # Remove only our incomplete stream, after its writer has been cancelled.
            if 'token' in locals() and not migrating:
                (checkpoint/token).unlink(missing_ok=True)
                (checkpoint/(token+'.done')).unlink(missing_ok=True)


def stop_for_restore(value, disk):
    """Quiesce old VM disk writes, then replace it without an OS shutdown wait."""
    monitor = QemuMonitor(value)
    was_running = False
    try:
        devices = capture_devices(monitor, disk)
        status = monitor.command('info status')
        was_running = status == 'VM status: running'
        if was_running: monitor.command('stop')
        if monitor.command('info status') != 'VM status: paused':
            raise RuntimeError('Cannot pause current VM; storage was left intact')
        for device in devices:
            result = monitor.command('qemu-io ' + device + ' "flush"')
            if result: raise RuntimeError('Cannot flush current VM disk: ' + result)
        stop_container(value['name'], grace_seconds=2)
    except Exception:
        if was_running and inspect(value['name']).get('State', {}).get('Running'):
            monitor.command('cont')
        raise
    finally:
        monitor.close()


def restore_manifest(value, disk):
    checkpoint = disk/'.mykos-checkpoint'
    for path in (checkpoint, checkpoint/'memory.gz', checkpoint/'manifest.json'):
        if path.is_symlink(): raise ValueError('Symlink checkpoint rejected')
    manifest = json.loads((checkpoint/'manifest.json').read_text())
    if manifest.get('format') != 1 or any(manifest.get(key) != value.get(key, {}) for key in ('os','image','cpu_env')):
        raise ValueError('VM checkpoint does not match its pinned image and CPU profile')
    if manifest.get('ram_mib', value.get('ram_mib', 2048)) != value.get('ram_mib', 2048):
        raise ValueError('VM checkpoint RAM size does not match the configured guest')
    if (checkpoint/'memory.gz').stat().st_size != manifest.get('memory_bytes'):
        raise ValueError('VM checkpoint file size mismatch')
    return manifest


def wait_for_restore(value, disk):
    manifest = restore_manifest(value, disk)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        state = inspect(value['name']).get('State', {})
        if not state.get('Running'):
            raise RuntimeError('VM exited while loading its memory checkpoint; inspect its logs')
        monitor = None
        try:
            monitor = QemuMonitor(value)
            status = monitor.command('info status')
            if status.startswith('VM status: paused') and migration_status(monitor.command('info migrate')) == 'completed':
                registers = cpu_checkpoint_registers(monitor.command('info registers'))
                if registers != manifest['registers']:
                    raise RuntimeError('Restored CPU state does not match the captured running VM')
                monitor.command('cont')
                if monitor.command('info status') != 'VM status: running':
                    raise RuntimeError('Restored VM did not resume')
                return
        except (FileNotFoundError, ConnectionRefusedError):
            pass  # QEMU has not created its private monitor yet.
        finally:
            if monitor is not None: monitor.close()
        time.sleep(.25)
    raise RuntimeError('Timed out restoring VM memory; instance was not marked ready')


def checkpoint_enable(os_name):
    """Restart an image build with a migratable CPU; retain its old container."""
    if os_name not in VERSIONS: raise ValueError('Select a template OS')
    with locked():
        value = read(os_name)
        if VERSIONS[os_name][0] not in ('windows', 'ubuntu', 'macos'): return links(value)
        profile = checkpoint_cpu_profile(os_name)
        if value.get('cpu_env') == profile: return links(value)
        old_value = dict(value)
        original = value['name']
        backup = original + '.before-checkpoint-' + str(int(time.time()))
        stop_container(original)
        run([PODMAN,'rename',original,backup], timeout=30)
        try:
            value.update(cpu_env=profile, previous_container=backup)
            launch(os_name, value)
            save(os_name, value)
        except BaseException:
            # Only the newly created replacement is disposable; never delete its disks.
            try:
                failed = inspect(original)
            except RuntimeError: failed = None
            if failed is not None:
                labels = failed.get('Config', {}).get('Labels') or {}
                if labels.get('io.mykos.vm.os') != os_name or labels.get('io.mykos.vm.role') != 'template':
                    raise RuntimeError('Replacement name belongs to another container; retained backup: ' + backup)
                run([PODMAN,'rm','--force',original], timeout=30)
            run([PODMAN,'rename',backup,original], timeout=30)
            save(os_name, old_value)
            run([PODMAN,'start',original], timeout=120)
            raise
        return links(value)



def cleanup_unused_base_images():
    """Retire only unused MYKOS :base names, preserving live and saved references."""
    used=set()
    for path in ROOT.glob('*.json'):
        value=read(path.stem)
        if value.get('image'):used.add(value['image'].removeprefix('sha256:'))
    for item in json.loads(run([PODMAN,'ps','-a','--format','json'],timeout=30)):
        names=item.get('Names') or []
        if isinstance(names,str):names=[names]
        if names:
            image=inspect(names[0]).get('Image')
            if image:used.add(image.removeprefix('sha256:'))
    snapshots=storage_root()/'snapshots'
    if snapshots.exists():
        for path in snapshots.glob('*/*/metadata.json'):
            if path.is_symlink():raise ValueError('Symlink snapshot metadata rejected')
            image=json.loads(path.read_text()).get('image')
            if image:used.add(image.removeprefix('sha256:'))
    removed=[]
    for item in json.loads(run([PODMAN,'images','--format','json'],timeout=30)):
        for name in item.get('Names') or []:
            if not re.fullmatch(r'localhost/(?:rh[0-9]+|(?:rocky|alma|oracle)(?:8|9|10)|centos(?:8|9|10)|fedora(?:43|44)|macos[0-9]+|win[0-9a-z]+|wns[0-9]+|ubuntu[0-9.]+):base',name):continue
            image=json.loads(run([PODMAN,'image','inspect',name],timeout=30))[0]['Id']
            if image.removeprefix('sha256:') in used:continue
            try:run([PODMAN,'image','rm',name],timeout=120)
            except RuntimeError:run([PODMAN,'untag',image,name],timeout=30)
            removed.append(name)
    return removed


def installing_images():
    """Expose active, unfinished image consoles without making them startable."""
    result = []
    for os_name in VERSIONS:
        try:
            value = read(os_name)
            if value.get('ready') or value.get('role') != 'template': continue
            data = inspect(value['name'])
            labels = data.get('Config', {}).get('Labels') or {}
            if labels.get('io.mykos.vm.os') != os_name or labels.get('io.mykos.vm.role') != 'template': continue
            running=bool(data.get('State', {}).get('Running'))
            if not running:
                from mykos_image_build import status
                if not value.get('unattended') or status(os_name).get('state')!='failed': continue
            address = str(ipaddress.IPv4Address(value['host']))
            port = int(value['console_port'])
            if not 1 <= port <= 65535: continue
            item={'os': os_name, 'name': value['name'], 'unattended': value.get('unattended',False),
                  'console_url': console_path(value) if running else None,
                  'direct_console_url': 'http://%s:%s/' % (address, port)}
            if item['unattended']:
                try:
                    from mykos_image_build import status
                    progress=status(os_name)
                    item.update(build_state=progress['state'],build_message=progress.get('message',''))
                except (ValueError,OSError,KeyError):pass
            result.append(item)
        except (ValueError, RuntimeError, OSError, KeyError, TypeError):
            continue
    return result


def prepared_templates():
    """Published desktop templates remain selectable without a builder container."""
    result = []
    for os_name in VERSIONS:
        try:
            value = read(os_name)
            if not value.get('ready') or value.get('role') != 'template': continue
            image = json.loads(run([PODMAN, 'image', 'inspect', value['image']], timeout=30))[0]
            if image['Id'] != value['image']: continue
            name = value.get('image_name')
            if name not in (image.get('RepoTags') or []): name = value['image']
            result.append({'os':os_name, 'image':name})
        except (ValueError, RuntimeError, OSError, KeyError, IndexError):
            continue
    return result


def prune_checkpoints(key):
    """Keep only the current template checkpoint; drop consumed instance copies."""
    value = read(key)
    if not value.get('ready'): return
    safe_dir(storage_root()); safe_dir(storage(key))
    checkpoint = storage(key)/'storage/.mykos-checkpoint'
    if value.get('role') == 'template':
        if value.get('capture_mode') != 'memory': return
        # Do not discard a fallback until the published checkpoint validates.
        restore_manifest(value, storage(key)/'golden')
        for previous in storage(key).glob('.golden-previous-*'):
            if not re.fullmatch(r'\.golden-previous-[0-9a-f]{16}', previous.name): continue
            if previous.is_symlink(): raise ValueError('Symlink old checkpoint rejected')
            shutil.rmtree(previous)
        if checkpoint.exists() and not (checkpoint/'restore.request').exists():
            safe_dir(checkpoint)
            shutil.rmtree(checkpoint)
    elif value.get('role') == 'instance':
        if (checkpoint/'restore.consumed').is_file() and not (checkpoint/'restore.request').exists():
            safe_dir(checkpoint)
            shutil.rmtree(checkpoint)


def cleanup_template(value):
    """Remove only a completed, stopped MYKOS builder; never force image removal."""
    if value.get('role') != 'template' or not value.get('ready') or not value.get('committed'):
        raise ValueError('Capture and commit the template before cleaning its builder')
    try:
        state = inspect(value['name'])
    except RuntimeError:
        # Check absence explicitly: other inspection failures must not trigger cleanup.
        names = json.loads(run([PODMAN, 'ps', '-a', '--format', 'json'], timeout=30))
        if any(value['name'] in ([item.get('Names')] if isinstance(item.get('Names'),str) else item.get('Names',[])) for item in names):
            raise RuntimeError('Cannot inspect the template builder; cleanup deferred')
    else:
        labels = state.get('Config', {}).get('Labels') or {}
        if labels.get('io.mykos.vm.role') != 'template' or labels.get('io.mykos.vm.os') != value['os']:
            raise ValueError('Builder identity mismatch; cleanup refused')
        if state.get('State', {}).get('Running'):
            raise ValueError('Builder is running; cleanup deferred')
        run([PODMAN, 'rm', value['name']], timeout=30)
    prune_checkpoints(value['os'])
    cleanup_unused_base_images()
    kind = VERSIONS[value['os']][0]
    if kind not in ('windows', 'macos'): return
    upstream = 'docker.io/dockurr/' + kind + ':latest'
    try:
        image_id = json.loads(run([PODMAN,'image','inspect',upstream],timeout=30))[0]['Id']
        for path in ROOT.glob('*.json'):
            record = read(path.stem)
            if record.get('image') == image_id:
                print('Retaining upstream image pinned by a saved VM record: ' + upstream)
                return
        containers = json.loads(run([PODMAN,'ps','-a','--format','json'],timeout=30))
        for item in containers:
            names = item.get('Names') or []
            if isinstance(names,str): names=[names]
            if not names: continue
            if inspect(names[0]).get('Image') == image_id:
                print('Retaining upstream image still used by a container: ' + upstream)
                return
        # No --force, no prune, no volume removal: Podman also protects dependencies.
        try:
            run([PODMAN,'image','rm',upstream],timeout=120)
        except RuntimeError:
            # Shared base layers may be ancestors of our committed image.
            # Drop only the vendor name, retaining every MYKOS tag and layer.
            run([PODMAN,'untag',image_id,upstream],timeout=30)
    except (RuntimeError, OSError, ValueError, KeyError, IndexError) as error:
        print('Template saved; unused upstream image cleanup deferred: ' + str(error))


def cleanup(os_name):
    if os_name not in VERSIONS: raise ValueError('Select a template OS')
    with locked():
        value = read(os_name)
        cleanup_template(value)
        return links(value)


def finish_windows_build_memory(value):
    """Restart a completed desktop installation at runtime RAM before capturing.

    Called under the lifecycle lock after the operator confirms installation is
    finished. Never publish an 8 GiB capture for a 2 GiB runtime guest.
    """
    os_name = value['os']
    if not os_name.startswith('win') or value.get('role') != 'template' or value.get('ready'):
        raise ValueError('Select an unfinished Windows desktop image')
    original = value['name']
    state = inspect(original)
    labels = state.get('Config', {}).get('Labels') or {}
    if original != os_name or labels.get('io.mykos.vm.os') != os_name or labels.get('io.mykos.vm.role') != 'template':
        raise ValueError('Image builder identity mismatch')
    source = storage(os_name) / 'storage'
    if (source/'.mykos-checkpoint/restore.request').exists():
        raise ValueError('Finish the pending memory restore before changing build RAM')
    if state.get('State', {}).get('Running'):
        pid = state['State'].get('Pid', 0)
        if not pid: raise ValueError('Cannot verify live builder storage')
        actual = Path('/proc/%d/root/storage' % pid).stat()
        expected = source.stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError('Live builder storage differs from configured storage; refusing restart')
    if value.get('build_pagefile_mib'):
        if not state.get('State', {}).get('Running'):
            raise ValueError('Start the installed Windows image before enabling runtime paging')
        from mykos_commands import powershell
        result = powershell(value, """$ErrorActionPreference = 'Stop'
$computer = Get-CimInstance Win32_ComputerSystem
Set-CimInstance -InputObject $computer -Property @{AutomaticManagedPagefile=$true} | Out-Null
if (-not (Get-CimInstance Win32_ComputerSystem).AutomaticManagedPagefile) { throw 'Automatic pagefile setting did not apply' }
Write-Output 'Windows-managed paging enabled; restart required.'
""", 60, allow_image_builder=True)
        if result.get('timed_out') or result.get('exit_code') != 0:
            raise RuntimeError('Could not enable Windows-managed paging; image left uncaptured: ' + result.get('output', ''))
    old = dict(value)
    backup = 'mykos-recovery-' + original + '-runtime-' + os.urandom(6).hex()
    was_running = state.get('State', {}).get('Running', False)
    stop_container(original)
    run([PODMAN, 'rename', original, backup], timeout=30)
    try:
        value.update(ram_mib=2048, installation_complete=True, runtime_memory_prepared=True,
                     runtime_pagefile='automatic' if value.get('build_pagefile_mib') else 'unchanged')
        launch(os_name, value)
        save(os_name, value)
    except BaseException:
        try:
            replacement = inspect(original)
        except RuntimeError:
            replacement = None
        if replacement is not None:
            labels = replacement.get('Config', {}).get('Labels') or {}
            if labels.get('io.mykos.vm.os') != os_name or labels.get('io.mykos.vm.role') != 'template':
                raise RuntimeError('Replacement identity mismatch; old builder retained: ' + backup)
            run([PODMAN, 'rm', '--force', original], timeout=30)
        run([PODMAN, 'rename', backup, original], timeout=30)
        save(os_name, old)
        if was_running: run([PODMAN, 'start', original], timeout=120)
        raise
    # Only the stopped wrapper is removed. The same disk, name and ports remain.
    try: run([PODMAN, 'rm', backup], timeout=30)
    except RuntimeError as error:
        print('Stopped build wrapper cleanup deferred: ' + backup + ': ' + str(error), flush=True)
    return {**links(value), 'ram_mib':2048, 'capture_pending':True,
            'message':'Image restarted at 2 GiB. Wait for the installed Windows desktop, then run Finish again to capture. No image has been published yet.'}


def verify_windows_installation(value):
    """Explicit image finish must capture installed Windows, never WinPE/setup."""
    if VERSIONS[value['os']][0] != 'windows': return
    if value['os'] in ('win98','winme','win2k','winxp','win7u'):
        # Legacy installers have no modern provisioning/guest-agent callback.
        # Only the explicit Finish workflow may confirm the installed desktop.
        if value.get('legacy_installation_confirmed') is True:return
        marker = storage(value['os'])/'storage/windows.boot'
        if marker.is_symlink() or not marker.is_file():
            raise ValueError('Finish Windows installation and boot the installed system before capturing this image')
        return
    from mykos_commands import powershell
    result = powershell(value, r"""$ErrorActionPreference = 'Stop'
$setup = Get-ItemProperty 'HKLM:\SYSTEM\Setup'
if ($env:SystemDrive -ne 'C:' -or $setup.SystemSetupInProgress -ne 0 -or $setup.OOBEInProgress -eq 1) {
    throw 'Finish Windows installation and reach the installed desktop before capturing the image'
}
Write-Output 'Installed Windows verified'
""", 30, **({'allow_prepared_image':True} if value.get('ready') else {'allow_image_builder':True}))
    if result.get('timed_out') or result.get('exit_code') != 0:
        raise ValueError('Windows installation is not ready for capture: ' + result.get('output','Guest check failed'))


def pending_capture(value):
    """Validate the completed immutable capture reserved for a commit retry."""
    pending=value.get('pending_commit')
    if not isinstance(pending,dict) or not re.fullmatch(r'\.capture-[A-Za-z0-9_-]+',str(pending.get('directory',''))):
        raise ValueError('Invalid pending capture metadata')
    if pending.get('source_image')!=value.get('image'):
        raise ValueError('Pending capture belongs to another source image')
    captured=storage(value['os'])/pending['directory']/'golden'
    for path in (captured.parent,captured,captured/'.mykos-checkpoint',captured/'.mykos-checkpoint/manifest.json',captured/'.mykos-checkpoint/memory.gz'):
        info=path.lstat()
        if path.is_symlink() or info.st_uid!=os.geteuid() or info.st_mode & 0o022:
            raise ValueError('Unsafe pending capture path')
    manifest=json.loads((captured/'.mykos-checkpoint/manifest.json').read_text())
    if (manifest.get('os')!=value['os'] or manifest.get('ram_mib')!=2048 or
        manifest.get('memory_bytes',0)<=0 or manifest['memory_bytes']!=(captured/'.mykos-checkpoint/memory.gz').stat().st_size or
        manifest.get('image') not in (pending['source_image'],pending.get('image_id'))):
        raise ValueError('Pending capture is incomplete or belongs to another VM')
    return captured


def commit_capture(os_name,value,captured,keep_running=False,embed_disk=False):
    """Finish a verified capture without touching its saved disk/RAM pairing."""
    pending=value['pending_commit']
    state=inspect(value['name']);labels=state.get('Config',{}).get('Labels') or {}
    if (state.get('Id')!=pending['container_id'] or labels.get('io.mykos.vm.os')!=os_name or
        labels.get('io.mykos.vm.role')!='template'):
        raise ValueError('Pending capture builder identity changed; saved state left intact')
    if state.get('State',{}).get('Running'):stop_container(value['name'])
    if inspect(value['name']).get('State',{}).get('Running'):
        raise RuntimeError('Builder did not stop; completed capture retained')
    embed_disk=bool(embed_disk or pending['embed_disk'])
    if embed_disk and not pending.get('embedded'):
        from mykos_image_build import embed_capture
        print('Saving installed disk archive; completed RAM capture retained',flush=True)
        embed_capture(value,captured)
        pending.update(embed_disk=True,embedded=True);save(os_name,value)
    image_name=template_image(os_name)
    image_id=pending.get('image_id')
    if image_id:
        if json.loads(run([PODMAN,'image','inspect',image_name],timeout=30))[0]['Id']!=image_id:
            raise ValueError('Saved commit image reference changed; capture retained')
    else:
        commit=[PODMAN,'commit']
        if value.get('unattended'):
            commit+=['--change','ENV MYKOS_UNATTENDED=','--change','ENV MYKOS_UBUNTU_ISO_URL=','--change','ENV MYKOS_WINDOWS_ASSET=']
        if VERSIONS[os_name][0]=='windows':commit+=['--change','ENV USERNAME=','--change','ENV PASSWORD=']
        print('Committing installed image '+image_name,flush=True)
        with tempfile.TemporaryDirectory(prefix='.commit-',dir=str(storage(os_name))) as scratch:
            run(commit+[value['name'],image_name],env={**os.environ,'TMPDIR':scratch})
        image_id=json.loads(run([PODMAN,'image','inspect',image_name],timeout=30))[0]['Id']
        pending['image_id']=image_id;save(os_name,value)
    manifest_path=captured/'.mykos-checkpoint/manifest.json'
    manifest=json.loads(manifest_path.read_text());manifest['image']=image_id
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    golden=storage(os_name)/'golden';previous=None
    if golden.exists():
        previous=storage(os_name)/('.golden-previous-'+os.urandom(8).hex());os.replace(golden,previous)
    os.replace(captured,golden)
    try:
        value.update(image=image_id,image_name=image_name,ready=True,capture_mode='memory',committed=True)
        value.pop('pending_commit',None);save(os_name,value)
    except BaseException:
        os.replace(golden,captured)
        if previous is not None:os.replace(previous,golden)
        raise
    try:
        if keep_running:
            print('Starting generic base from the committed image',flush=True)
            run([PODMAN,'rm',value['name']],timeout=300);launch(os_name,value)
        else:cleanup_template(value)
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as error:
        if keep_running:raise RuntimeError('Image saved, but the generic base could not restart: '+str(error)) from error
        print('Template saved; builder cleanup deferred: '+str(error))
    return links(value)


def prepare(os_name, replace=False, keep_running=False, embed_disk=False, confirm_legacy=False):
    if os_name not in VERSIONS: raise ValueError('Select a template OS')
    with locked():
        value = read(os_name)
        value.setdefault('cpu_env', {})
        if value.get('pending_commit'):
            captured=pending_capture(value)
            try:return commit_capture(os_name,value,captured,keep_running,embed_disk)
            finally:
                if not captured.exists():captured.parent.rmdir()
        if confirm_legacy is True and os_name in ('win98','winme','win2k','winxp','win7u'):
            value['legacy_installation_confirmed']=True
        verify_windows_installation(value)
        if value['os'].startswith('ubuntu') and not value.get('ready') and value.get('guest_agent'):
            from mykos_ubuntu_vpn import execute
            verify = """import pathlib,subprocess
release=pathlib.Path('/etc/os-release').read_text()
root=subprocess.check_output(['findmnt','-n','-o','FSTYPE','/'],text=True).strip()
if ('ID=ubuntu' not in release and 'ID=\"ubuntu\"' not in release) or root not in ('ext4','xfs'):
 raise RuntimeError('Install Ubuntu to disk and boot the installed system before capture')
print('Installed Ubuntu verified')
"""
            execute(value,'/usr/bin/python3',('-c',verify),timeout=8)
        if value['os'].startswith('win') and not value.get('ready') and not value.get('runtime_memory_prepared') and (value.get('ram_mib', 2048) > 2048 or value.get('build_pagefile_mib')):
            return finish_windows_build_memory(value)
        if value.get('ram_mib', 2048) != 2048:
            raise ValueError('Capture requires 2048 MiB so the image can start in a 2 GiB container')
        if not value.get('ready') and value.get('runtime_memory_prepared') and value.get('runtime_pagefile') == 'automatic':
            from mykos_commands import powershell
            result = powershell(value, """$ErrorActionPreference = 'Stop'
$computer = Get-CimInstance Win32_ComputerSystem
if (-not $computer.AutomaticManagedPagefile) { throw 'Windows-managed paging is not enabled' }
if ($computer.TotalPhysicalMemory -gt 2304MB) { throw 'Wait for the 2 GiB Windows restart to finish' }
Write-Output 'Runtime RAM and Windows-managed paging verified.'
""", 60, allow_image_builder=True)
            if result.get('timed_out') or result.get('exit_code') != 0:
                raise RuntimeError('Runtime memory verification failed; no image captured: ' + result.get('output', ''))

        if value['ready'] and value.get('capture_mode') == 'memory' and not replace:
            raise ValueError('Memory template is already prepared; use --replace to recapture')
        golden = storage(os_name)/'golden'
        if golden.exists() and not value['ready']:
            raise ValueError('A prior capture exists; inspect it before retrying')
        temporary=Path(tempfile.mkdtemp(prefix='.capture-',dir=str(storage(os_name))))
        captured=temporary/'golden';completed=False
        try:
            capture_live(value,storage(os_name)/'storage',captured,True)
            completed=True
            state=inspect(value['name'])
            value['pending_commit']={'directory':temporary.name,'container_id':state['Id'],
                                     'source_image':value['image'],'embed_disk':bool(embed_disk),'embedded':False}
            save(os_name,value)
            pending_capture(value)
            return commit_capture(os_name,value,captured,keep_running,embed_disk)
        finally:
            if completed and captured.exists():
                print('Completed disk/RAM capture retained for commit retry: '+str(captured),flush=True)
            else:shutil.rmtree(temporary)


def publish_started(shop):
    # Podman is running before VM memory restore and ID hooks finish. Publish
    # this ID now so the browser need not wait for the entire startup job.
    from mykos_refresh import best_effort
    best_effort(shop,{})


def start(shop, os_name=None):
    if not re.fullmatch(r'[0-9]{8}', shop): raise ValueError('ID must be eight ASCII digits')
    with locked():
        if record_path(shop).exists():
            value = read(shop)
            if os_name and value['os'] != os_name: raise ValueError('ID belongs to a different OS')
            if not value['ready']: raise ValueError('Prior creation failed; inspect retained storage before resuming')
            data = inspect(value['name'])
            if (data.get('Config', {}).get('Labels') or {}).get('io.mykos.vm.os') != value['os']:
                raise ValueError('Container identity mismatch')
            run([PODMAN, 'start', value['name']], timeout=120)
            if not data.get('State', {}).get('Running'):
                value['last_start'] = 'boot'; save(shop, value)
                publish_started(shop)
        else:
            preflight(os_name)
            template = read(os_name)
            if template.get('ram_mib', 4096) != 2048:
                raise ValueError('Recapture this image at 2048 MiB before creating instances; its existing RAM state uses a different size')
            if not template['ready']: raise ValueError('Finish installing and prepare the template first')
            items = json.loads(run([PODMAN, 'ps', '-a', '--format', 'json'], timeout=30))
            for item in items:
                names = item.get('Names') or []
                if isinstance(names, str): names = [names]
                if any(re.fullmatch(re.escape(shop) + r'(?:\.[A-Za-z0-9_.-]+)?', name) for name in names):
                    raise ValueError('A container already uses this code')
            # A golden copy is immutable and was made with guest writes paused.
            host, bind = settings(); console, desktop = allocate(bind)
            safe_dir(storage(shop))
            value = {**template, 'name': shop + '.' + socket.gethostname(), 'role':'instance',
                     'host':host, 'bind':bind, 'console_port':console, 'desktop_port':desktop, 'ready':False}
            save(shop, value)
            try:
                tree_copy(storage(os_name) / 'golden', storage(shop) / 'storage')
                if value.get('capture_mode') == 'memory':
                    restore_manifest(value, storage(shop)/'storage')
                    request = storage(shop)/'storage/.mykos-checkpoint/restore.request'
                    if request.is_symlink(): raise ValueError('Symlink restore request rejected')
                    request.write_text('restore once\n'); request.chmod(0o600)
                launch(shop, value)
                publish_started(shop)
                if value.get('capture_mode') == 'memory':
                    wait_for_restore(value, storage(shop)/'storage')
                    value['last_start'] = 'memory-restore'
                else: value['last_start'] = 'boot'
                if value.get('unattended'):
                    from mykos_image_build import instance_setup
                    instance_setup(shop,value)
                value['ready'] = True; save(shop, value)
            except Exception:
                if value.get('capture_mode') == 'memory':
                    try:
                        failed = inspect(value['name'])
                        labels = failed.get('Config', {}).get('Labels') or {}
                        if labels.get('io.mykos.vm.role') == 'instance' and labels.get('io.mykos.vm.os') == value['os']:
                            run([PODMAN,'kill','--signal','KILL',value['name']],timeout=30)
                    except RuntimeError: pass
                # Keep disk data for diagnosis/recovery; never silently replace it.
                value['ready'] = False; save(shop, value)
                raise
        try:
            prune_checkpoints(shop)
        except (ValueError, OSError) as error:
            print('Instance started; consumed checkpoint cleanup deferred: ' + str(error))
        for suffix, text in (('container', value['name']), ('imagetype', value['os']), ('postype', value['os'])):
            path = POS / ('.' + shop + '.' + suffix)
            if path.is_symlink(): raise ValueError('Symlink shop metadata rejected')
            path.write_text(text + '\n')
        return links(value)


def stop(key):
    with locked():
        value = read(key)
        stop_container(value['name'])
        return links(value)


def ensure_prepared_base(os_name):
    """Retry only a saved image's base restart; never repeat installation/capture."""
    with locked():
        value=read(os_name)
        if value.get('role')!='template' or not value.get('ready') or not value.get('committed') or value.get('capture_mode')!='memory':
            raise ValueError('A committed memory image is required')
        if not (storage(os_name)/'golden/.mykos-checkpoint/manifest.json').is_file():
            raise ValueError('Saved image capture is missing; storage was left intact')
        try:
            state=inspect(value['name'])
        except RuntimeError:
            matches=json.loads(run([PODMAN,'ps','-a','--filter','name=^'+re.escape(value['name'])+'$','--format','json'],timeout=30))
            if matches:
                raise ValueError('Saved image is intact, but its unusable builder needs cleanup before retrying')
            launch(os_name,value)
        else:
            labels=state.get('Config',{}).get('Labels') or {}
            if labels.get('io.mykos.vm.os')!=os_name or labels.get('io.mykos.vm.role')!='template':
                raise ValueError('Base container identity mismatch; left intact')
            if state.get('State',{}).get('Paused'):
                raise ValueError('Base is paused; left intact')
            if not state.get('State',{}).get('Running'):
                run([PODMAN,'rm',value['name']],timeout=300)
                launch(os_name,value)
        return links(value)


def resume(key):
    with locked():
        value = read(key)
        try:
            info = inspect(value['name'])
        except RuntimeError:
            launch(key, value)
            value['last_start'] = 'boot'; save(key, value)
        else:
            labels = info.get('Config', {}).get('Labels') or {}
            if labels.get('io.mykos.vm.os') != value['os']: raise ValueError('Container identity mismatch')
            run([PODMAN, 'start', value['name']], timeout=120)
            if not info.get('State', {}).get('Running'):
                value['last_start'] = 'boot'; save(key, value)
        return links(value)


def info(key):
    value = read(key)
    try:
        data = inspect(value['name'])
    except RuntimeError:
        if value.get('role') == 'template' and value.get('ready') and value.get('committed'):
            return {**links(value), 'running':False}
        raise
    if (data.get('Config', {}).get('Labels') or {}).get('io.mykos.vm.os') != value['os']:
        raise ValueError('Container identity mismatch')
    return {**links(value), 'running': bool(data.get('State', {}).get('Running'))}


def balloon_status(monitor):
    actual = monitor.command('info balloon')
    match = re.search(r'actual=(\d+)', actual)
    if not match:
        raise ValueError('Guest balloon driver is not active')
    monitor.command('qom-set /machine/peripheral/balloon0 guest-stats-polling-interval 5')
    raw = json.loads(monitor.command('qom-get /machine/peripheral/balloon0 guest-stats'))
    stats = {key: value for key, value in raw.get('stats', {}).items()
             if isinstance(value, int) and 0 <= value < 2**63}
    updated = raw.get('last-update', 0)
    fresh = isinstance(updated, (int, float)) and 0 <= time.time() - updated <= 15
    available = stats.get('stat-available-memory')
    total = stats.get('stat-total-memory')
    supported = fresh and available is not None and total is not None and 0 <= available <= total
    return {'actual_mib': int(match.group(1)), 'supported': supported,
            'available_bytes': available, 'guest_total_bytes': total,
            'last_update': updated, 'stats': stats}


def memory(key, target_mib=None):
    """Inspect or cautiously adjust a verified guest balloon, never a host limit."""
    if target_mib is not None and (type(target_mib) is not int or target_mib != 2048):
        raise ValueError('Guest memory policy is 2048 MiB')
    with locked():
        value = read(key)
        monitor = QemuMonitor(value)
        try:
            if target_mib is None:
                try:
                    result = balloon_status(monitor)
                except (RuntimeError, ValueError) as error:
                    return {'key': key, 'os': value['os'], 'supported': False, 'configured_mib': value.get('ram_mib', 4096), 'reason': str(error)}
                return {'key': key, 'os': value['os'], 'configured_mib': value.get('ram_mib', 4096), **result}
            # A requested change requires current, meaningful driver statistics.
            result = balloon_status(monitor)
            if not result['supported']:
                raise ValueError('Guest balloon statistics unavailable or stale; memory left unchanged')
            if value['os'].startswith('mac'):
                raise ValueError('macOS balloon control is not verified; memory left unchanged')
            if monitor.command('info status') != 'VM status: running':
                raise ValueError('Guest must be running before adjusting memory')
            migration = monitor.command('info migrate')
            if migration_status(migration) in ('active', 'setup', 'postcopy-active', 'pre-switchover'):
                raise ValueError('Wait for VM capture/restore to finish before adjusting memory')
            current = result['actual_mib']
            previous = value.get('balloon_request', {})
            if previous and (result['last_update'] <= previous['at'] or abs(current - previous['target_mib']) > 8):
                raise ValueError('Wait for the previous balloon request and fresh guest statistics')
            reduction = current - target_mib
            if reduction > 128:
                raise ValueError('Reduce guest memory by at most 128 MiB per operation')
            if reduction > 0 and result['available_bytes'] - reduction * 1024**2 < 1024**3:
                raise ValueError('At least 1 GiB of available guest memory must remain')
            monitor.command('balloon ' + str(target_mib))
            value['balloon_request'] = {'at': time.time(), 'target_mib': target_mib}
            save(key, value)
            # Balloon requests are asynchronous; do not report the target as actual.
            return {'key': key, 'os': value['os'], 'requested_mib': target_mib,
                    'actual_mib': current, 'state': 'requested'}
        finally:
            monitor.close()


def configure_memory(key, target_mib, *, enable_guest_agent=False):
    """Cold-boot at the policy RAM size; retain explicitly stopped recovery config."""
    if not re.fullmatch(r'[0-9]{8}', key):
        raise ValueError('Select an instance ID')
    with locked():
        value = read(key)
        if type(target_mib) is not int or target_mib != 2048:
            raise ValueError('MYKOS VM RAM policy is 2048 MiB per guest')
        if enable_guest_agent and (value.get('role')!='instance' or not value.get('os','').startswith('ubuntu')):
            raise ValueError('Guest-agent enable requires an Ubuntu instance')
        old = dict(value)
        original = value['name']
        state = inspect(original)
        request = storage(key) / 'storage/.mykos-checkpoint/restore.request'
        if request.exists() or request.is_symlink():
            raise ValueError('Unconsumed RAM restore request; memory left unchanged')
        recovery = None
        if state.get('State', {}).get('Paused'):
            raise ValueError('Unpause or inspect this VM before resizing; its storage was left intact')
        if state.get('State', {}).get('Running'):
            monitor = QemuMonitor(value)
            try:
                if re.search(r'migration status:\s*(?:active|setup|postcopy-active)', monitor.command('info migrate'), re.I):
                    raise ValueError('Wait for capture/restore to finish')
                source = storage(key) / 'storage'
                guest_source = Path('/proc/%d/root/storage' % state['State']['Pid'])
                host_stat, guest_stat = source.stat(), guest_source.stat()
                if (host_stat.st_dev, host_stat.st_ino) != (guest_stat.st_dev, guest_stat.st_ino):
                    raise ValueError('Live VM storage differs from its configured host path; refusing to recreate it')
                devices = capture_devices(monitor, source)
                recovery = storage(key) / ('.memory-recovery-' + os.urandom(8).hex())
                safe_dir(recovery)
                was_running = monitor.command('info status') == 'VM status: running'
                try:
                    if was_running: monitor.command('stop')
                    if monitor.command('info status') != 'VM status: paused':
                        raise ValueError('Cannot quiesce VM for disk recovery copy')
                    for device in devices:
                        result = monitor.command('qemu-io ' + device + ' "flush"')
                        if result: raise ValueError('Cannot flush VM disk for recovery')
                    tree_copy(source, recovery / 'storage', reflink='always')
                    record = recovery / 'configuration.json'
                    record.write_text(json.dumps(old, indent=2)); record.chmod(0o600)
                finally:
                    if was_running and monitor.command('info status') == 'VM status: paused':
                        monitor.command('cont')
            finally:
                monitor.close()
        backup = 'mykos-recovery-' + key + '-memory-' + str(int(time.time()))
        renamed = False
        stop_container(original)
        try:
            run([PODMAN, 'rename', original, backup], timeout=30)
            renamed = True
            # Explicitly stopped unless-stopped containers stay stopped on reboot.
            # Podman 4.x cannot change this policy using `podman update`.
            if enable_guest_agent: value['guest_agent'] = True
            value.update(ram_mib=target_mib, capture_mode='disk', last_start='boot',
                         previous_container=backup, balloon_request={}, memory_recovery=str(recovery) if recovery else None)
            launch(key, value)
            save(key, value)
        except BaseException:
            if renamed:
                try:
                    replacement = inspect(original)
                except RuntimeError:
                    replacement = None
                if replacement is not None:
                    labels = replacement.get('Config', {}).get('Labels') or {}
                    if labels.get('io.mykos.vm.os') != value['os'] or labels.get('io.mykos.vm.role') != 'instance':
                        raise RuntimeError('Replacement identity mismatch; recovery container retained: ' + backup)
                    run([PODMAN, 'rm', '--force', original], timeout=30)
                run([PODMAN, 'rename', backup, original], timeout=30)
            save(key, old)
            if state.get('State', {}).get('Running'):
                run([PODMAN, 'start', original], timeout=120)
            raise
        return {**links(value), 'ram_mib': target_mib, 'recovery_container': backup}


def enable_guest_agent(key):
    value=read(key)
    if value.get('role')!='instance' or not value.get('os','').startswith('ubuntu'):
        raise ValueError('Select an Ubuntu instance')
    from mykos_commands import GuestAgent
    if value.get('guest_agent'):
        agent=GuestAgent(value,timeout=2);agent.close()
        return links(value)
    result=configure_memory(key,2048,enable_guest_agent=True)
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        try:
            agent=GuestAgent(read(key),timeout=2);agent.close()
            break
        except (ValueError,OSError): time.sleep(2)
    else: raise RuntimeError('Ubuntu restarted with its agent channel, but qemu-guest-agent did not respond; recovery configuration retained')
    with locked():
        current=read(key)
        backup=current.get('previous_container')
        if backup!=result['recovery_container']: raise RuntimeError('Recovery identity changed; cleanup deferred')
        state=inspect(backup)
        labels=state.get('Config',{}).get('Labels') or {}
        if state.get('State',{}).get('Running') or labels.get('io.mykos.vm.os')!=current['os'] or labels.get('io.mykos.vm.role')!='instance':
            raise RuntimeError('Recovery wrapper changed; cleanup deferred')
        run([PODMAN,'rm',backup],timeout=30)
        recovery=current.get('memory_recovery')
        if recovery:
            recovery=Path(recovery)
            if recovery.parent!=storage(key) or not recovery.name.startswith('.memory-recovery-'):
                raise ValueError('Unexpected recovery storage; left intact')
            safe_dir(recovery);shutil.rmtree(recovery)
        current.pop('previous_container',None);current.pop('memory_recovery',None);save(key,current)
    return {**links(current),'guest_agent':True}
