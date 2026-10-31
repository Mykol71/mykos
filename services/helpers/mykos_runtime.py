"""Root helper primitives. Scheduled commands use the separate private admin-only command queue."""
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys

CONFIG = Path('/etc/mykos/config.json')
JOBS = Path('/var/lib/mykos/jobs')
PODMAN = '/usr/bin/podman'
SHOP = re.compile(r'[0-9]{8}\Z')
NATIVE_OS_NAMES = ('rh7','rh8','rh9','rh10','mssql2022','mssql2025','rocky8','rocky9','rocky10','alma8','alma9','alma10','oracle8','oracle9','oracle10','centos8','centos9','centos10','fedora43','fedora44')
STAGE_OS_NAMES = ('mssql2022', 'mssql2025', 'rh7', 'rh8', 'rh9', 'rh10', 'rocky8', 'rocky9', 'rocky10', 'alma8', 'alma9', 'alma10','oracle8','oracle9','oracle10','centos8','centos9','centos10','fedora43','fedora44', 'win10', 'wns2025', 'wns2022', 'wns2019', 'wns2016', 'win11', 'win8e', 'win7u', 'winxp', 'winme', 'win2k', 'win98', 'mac15', 'mac14', 'mac13', 'mac12')

def shopcode(value):
    if not isinstance(value, str) or not SHOP.fullmatch(value):
        raise ValueError('ID must be exactly eight ASCII digits')
    return value

def config():
    st = CONFIG.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        raise ValueError('Configuration must be a regular root-owned file, not group/world writable')
    return json.loads(CONFIG.read_text())

def podman_scope(args):
    """Keep persistent container monitors outside short-lived worker services."""
    spawning = args[:2] == [PODMAN, 'start'] or args[:3] == [PODMAN, 'container', 'restore']
    spawning = spawning or (args[:2] == [PODMAN, 'run'] and any(flag in args for flag in ('-d', '--detach')))
    if spawning and os.geteuid() == 0 and Path('/run/systemd/system').is_dir():
        building = 'MYKOS_BUILD_NICE=1' in args
        properties = ['--property=CPUWeight=10', '--property=IOWeight=10'] if building else []
        # Detached runtime guests must not inherit their builder worker's nice/IO priority.
        command = args
        if not building and os.getpriority(os.PRIO_PROCESS, 0) > 0:
            command = ['/usr/bin/nice', '-n', str(-os.getpriority(os.PRIO_PROCESS, 0)),
                       '/usr/bin/ionice', '-c', '2', '-n', '4', *args]
        return ['/usr/bin/systemd-run', '--scope', '--quiet', '--collect',
                '--property=Delegate=yes', *properties, '--unit=mykos-podman-' + os.urandom(12).hex(), '--', *command]
    return args


def build_priority():
    """Background builders yield CPU and disk bandwidth to running workloads."""
    os.setpriority(os.PRIO_PROCESS, 0, 15)
    subprocess.run(['/usr/bin/ionice', '-c', '2', '-n', '7', '-p', str(os.getpid())],
                   check=True, timeout=5)


def inventory_read(key, callback, cache_root=Path('/var/cache/mykos/dashboard'), deadline=5):
    """Bound read-only dashboard queries; a Podman lock must not erase the UI."""
    import signal
    import tempfile
    if key not in ('list', 'stage-images'):
        raise ValueError('Unsupported inventory cache')
    cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = cache_root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('Unsafe inventory cache directory')
    path = cache_root / (key + '.json')
    def expired(signum, frame):
        raise TimeoutError('Podman inventory is busy')
    previous = signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, deadline)
        try:
            value = callback()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except (TimeoutError, subprocess.TimeoutExpired):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe inventory cache file')
        cached = json.loads(path.read_text())
        value = cached['value']
        if isinstance(value, dict):
            value = {**value, 'inventory_stale': True, 'inventory_collected_at': cached['collected_at']}
        print('Podman busy; serving last successful dashboard inventory', file=sys.stderr)
        return value
    finally:
        signal.signal(signal.SIGALRM, previous)
    import time
    fd, temporary = tempfile.mkstemp(prefix='.inventory-', dir=cache_root)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump({'collected_at': int(time.time()), 'value': value}, handle)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return value


OUTPUT_LIMIT = 16 * 1024 * 1024

def run(args, timeout=75):
    # Use disk-backed output to avoid unbounded memory consumption.
    import tempfile
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        p = subprocess.run(podman_scope(args), stdout=out, stderr=err, timeout=timeout, check=False,
                           env={**os.environ, 'PATH':'/usr/sbin:/usr/bin:/sbin:/bin'})
        out.seek(0)
        data = out.read(OUTPUT_LIMIT + 1)
        if len(data) > OUTPUT_LIMIT:
            raise RuntimeError('Command output exceeds the safe read limit: ' + args[0])
        text = data.decode('utf-8', 'replace')
        err.seek(0)
        warnings = err.read(512 * 1024).decode('utf-8', 'replace')
    if p.returncode:
        raise RuntimeError((text+'\n'+warnings).strip()[-4000:] or 'Command failed: ' + args[0])
    if warnings:print(warnings,end='' if warnings.endswith('\n') else '\n',file=sys.stderr)
    return text.strip()

def network_device(settings):
    """Use an explicit override, or the interface selected by the host route."""
    device = settings.get('network_device')
    if not device:
        try:
            route = run(['ip', 'route', 'get', '8.8.8.8'], timeout=10).split()
            device = route[route.index('dev') + 1]
        except (RuntimeError, ValueError, IndexError, OSError, subprocess.SubprocessError) as exc:
            raise ValueError('Cannot detect network device from ip route get 8.8.8.8; configure network_device in /etc/mykos/config.json') from exc
    if not isinstance(device, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.:-]{0,31}', device):
        raise ValueError('Invalid network_device; configure a valid host interface')
    return device


def containers():
    items = json.loads(run([PODMAN, 'ps', '-a', '--format', 'json']))
    result = []
    for item in items:
        names = item.get('Names', [])
        if isinstance(names, str): names = [names]
        for name in names:
            if re.fullmatch(r'[0-9]{8}(?:\.[A-Za-z0-9_.-]+)?', name):
                result.append({'shop':name[:8], 'name':name, 'state':item.get('State','unknown')})
    return result

def stage_images():
    """Captured native images remain usable while their base sandbox is stopped."""
    items = json.loads(run([PODMAN, 'ps', '-a', '--format', 'json']))
    images = {}
    for item in items:
        names = item.get('Names', [])
        if isinstance(names, str): names = [names]
        image = item.get('Image') or item.get('ImageID')
        if not isinstance(image, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./_:@-]*', image):
            continue
        for name in names:
            if name in STAGE_OS_NAMES or re.fullmatch(r'ubuntu(?:[0-9]{2}(?:\.[0-9]{2})?)?', name):
                labels = item.get('Labels') or {}
                if isinstance(labels, dict) and labels.get('io.mykos.vm.os'):
                    from mykos_vm import read
                    try:
                        value = read(name)
                        if not value['ready'] or value.get('role') != 'template': continue
                        image = value.get('image_name') or value['image']
                    except (ValueError, OSError): continue
                elif item.get('State', 'running') != 'running':
                    # A stopped base still pins a captured image. Do not require
                    # opening its sandbox before creating an ID-specific guest.
                    if (name not in NATIVE_OS_NAMES or not isinstance(labels,dict) or
                        labels.get('io.mykos.os')!=name or image!='localhost/'+name+':latest'):
                        continue
                images[name] = {'os': name, 'image': image}
    from mykos_vm import prepared_templates
    for item in prepared_templates():
        images[item['os']] = item
    ordered = [name for name in STAGE_OS_NAMES if name in images]
    ordered += sorted(name for name in images if name.startswith('ubuntu'))
    return [images[name] for name in ordered]


def target(shop):
    shopcode(shop)
    matches = [x['name'] for x in containers() if x['shop'] == shop]
    if len(matches) != 1:
        raise ValueError('Expected exactly one managed container for shop; found %d' % len(matches))
    return matches[0]

def connection():
    import pwd
    import struct
    conn = socket.socket(fileno=os.dup(0))
    pid, uid, gid = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid not in (0, pwd.getpwnam('mykosweb').pw_uid):
        raise PermissionError('Unauthorized socket peer')
    conn.settimeout(10)
    return conn

def request(conn):
    value = bytearray()
    while not value.endswith(b'\n'):
        b = conn.recv(1)
        if not b: raise ValueError('Incomplete request')
        value.extend(b)
        if len(value) > 131072: raise ValueError('Request too large')
    return json.loads(value)

def save_job(shop, value):
    import tempfile
    JOBS.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=JOBS, prefix='.job-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f);f.flush();os.fsync(f.fileno())
        os.replace(name, JOBS / (shopcode(shop) + '.json'))
    finally:
        if os.path.exists(name): os.unlink(name)

def job(shop):
    path = JOBS / (shopcode(shop)+'.json')
    return json.loads(path.read_text()) if path.exists() else {'shop':shop,'state':'idle','output':''}
