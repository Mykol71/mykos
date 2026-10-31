"""Read-only host monitoring. Never execute guest programs during polling."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import tempfile
import shlex
from datetime import datetime

IMAGE_OS_CACHE = Path('/var/cache/mykos/image-os')

SHOP = re.compile(r'^([0-9]{8})(?:\.|$)')


def run(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True).stdout


def containers(shop=None):
    args=['podman','ps','-a','--format','json']
    if shop:
        if not re.fullmatch(r'[0-9]{8}',shop): raise ValueError('Invalid ID')
        args += ['--filter','name=^'+shop+r'\.']
    rows=json.loads(run(args))
    names = []
    for row in rows:
        value = row.get('Names', [])
        name = value[0] if isinstance(value, list) and value else value
        if isinstance(name, str) and SHOP.match(name):
            names.append(name)
    if not names:
        return []
    # Explicit names prevent a no-argument inspect from changing its meaning.
    return json.loads(run(['podman', 'container', 'inspect', *names], timeout=30))


def desktop(info):
    return bool((info.get('Config', {}).get('Labels') or {}).get('io.mykos.vm.os'))


def name(info):
    return info.get('Name', '').lstrip('/')


def identity(info):
    state = info.get('State', {})
    return {'id': info.get('Id') or info.get('ID'), 'started_at': state.get('StartedAt')}


def process_token(pid):
    if not isinstance(pid, int) or pid <= 1:
        raise ValueError('Container is not running')
    # Start time and network namespace inode reject exited/reused init PIDs.
    fields = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()
    return fields[19], os.stat('/proc/%d/ns/net' % pid).st_ino


def network(info, interface=None):
    if interface is None:
        interface = (info.get('Config', {}).get('Labels') or {}).get('io.mykos.network.interface', 'eth1')
    if not info.get('State', {}).get('Running'):
        return {}
    if desktop(info):
        labels = info.get('Config', {}).get('Labels') or {}
        networks = info.get('NetworkSettings', {}).get('Networks', {})
        selected = labels.get('io.mykos.network.name')
        candidates = [networks[selected]] if selected in networks else list(networks.values())
        for nic in candidates:
            if nic.get('IPAddress'):
                return {'interface': labels.get('io.mykos.network.interface', 'eth0'),
                        'ipv4': nic['IPAddress'], 'mac': nic.get('MacAddress')}
        return {}
    pid = info['State'].get('Pid')
    try:
        before = process_token(pid)
        rows = json.loads(run(['/usr/bin/nsenter', '-t', str(pid), '-n',
                               '/usr/sbin/ip', '-j', 'addr', 'show', 'dev', interface], timeout=5))
        if process_token(pid) != before:
            return {}
        if not rows:
            return {}
        row = rows[0]
        return {'interface': row.get('ifname'), 'mac': row.get('address'),
                'ipv4': next((a.get('local') for a in row.get('addr_info', [])
                              if a.get('family') == 'inet' and a.get('scope') == 'global'), None)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def unknown_vpn():
    return {'software': 'Libreswan', 'status': 'unknown', 'loaded': None,
            'established': None, 'connections': []}


def parse_vpn(output):
    result = unknown_vpn()
    loaded = re.search(r'Total IPsec connections:\s*loaded\s+(\d+)', output)
    active = re.search(r'IPsec SAs:\s*total\((\d+)\)', output)
    if loaded and active:
        result.update(loaded=int(loaded[1]), established=int(active[1]))
        result['status'] = 'not_configured' if not result['loaded'] else 'up' if result['established'] else 'down'
    result['connections'] = sorted(set(re.findall(r'^\d+\s+"([^"]+)":', output, re.M)))
    result['established_connections'] = sorted(set(re.findall(r'^[^"\r\n]*"([^"\r\n]+)"[^"\r\n]*(?:STATE_V2_ESTABLISHED_CHILD_SA|STATE_QUICK_I2|STATE_QUICK_R2|IPsec SA established)',output,re.M)))
    return result


def vpn_without_daemon(root):
    """A stopped daemon with no connection config is unconfigured, not unknown."""
    result = unknown_vpn()
    directory = root/'etc/ipsec.d'
    for path in (root/'etc', directory, root/'etc/ipsec.conf'):
        if path.is_symlink(): return result
    if not directory.is_dir(): return result
    entries = list(directory.glob('*.conf'))
    if any(p.is_symlink() or not p.is_file() for p in entries): return result
    configured = bool(entries)
    main = root/'etc/ipsec.conf'
    if main.is_file():
        if main.stat().st_size > 65536: return result
        configured |= bool(re.search(r'^\s*conn\s+(?!%default(?:\s|$))\S+', main.read_text(), re.M))
    result.update(status='down' if configured else 'not_configured', loaded=None if configured else 0, established=0)
    return result


def query_vpn(info=None):
    result = unknown_vpn()
    if info is not None and desktop(info):
        if (info.get('Config', {}).get('Labels') or {}).get('io.mykos.vm.os', '').startswith('ubuntu'):
            from mykos_ubuntu_vpn import status
            return status(info)
        return result
    if info is not None and not info.get('State', {}).get('Running'):
        return result
    args = ['/usr/sbin/ipsec', 'status']
    try:
        if info is not None:
            pid = info['State'].get('Pid')
            before = process_token(pid)
            root = Path('/proc/%d/root' % pid)
            # Reject symlinks beneath the proc root magic link. Absolute symlinks
            # could otherwise reach the host daemon instead of the selected daemon.
            path = root
            for part in ('run', 'pluto', 'pluto.ctl'):
                path = path / part
                if path.is_symlink():
                    return result
            if not path.exists():
                fallback = vpn_without_daemon(root)
                return fallback if process_token(pid) == before else result
            if not stat.S_ISSOCK(path.stat().st_mode):
                return result
            args += ['--ctlsocket', str(path)]
        output = run(args, timeout=5)
        if info is not None and process_token(pid) != before:
            return result
        return parse_vpn(output)
    except (OSError, ValueError, subprocess.SubprocessError):
        return result


def cached_vpn(info, status_file, now=None):
    """Consumers never probe independently; expired or restarted samples are unknown."""
    try:
        payload = json.loads(Path(status_file).read_text())
        row = payload.get('vpn_samples', {}).get(name(info), {})
        age = (time.time() if now is None else now) - row.get('collected_at', payload['collected_at'])
        if 0 <= age <= 60 and row.get('identity') == identity(info):
            return row['vpn']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return unknown_vpn()


def image_catalog(infos):
    ids = sorted(set(info.get('Image') for info in infos if info.get('Image')))
    if not ids:
        return {}
    rows = json.loads(run(['podman', 'image', 'inspect', *ids], timeout=30))
    return {str(row.get('Id') or row.get('ID')).removeprefix('sha256:'): row for row in rows}


def image_info(info, catalog):
    return catalog.get(str(info.get('Image', '')).removeprefix('sha256:'), {})


def image_name(info, image):
    stored = info.get('ImageName') or 'Unknown'
    if not re.fullmatch(r'(?:sha256:)?[0-9a-f]{12,64}', stored):
        return stored
    tags = image.get('RepoTags') or []
    return next((tag for tag in tags if re.fullmatch(r'localhost/(?:mssql(?:2022|2025)|rh[0-9]+|(?:rocky|alma|oracle)(?:8|9|10)|centos(?:8|9|10)|fedora(?:43|44)|ubuntu[0-9.]*|win[^/:]+|wns[0-9]+|macos[0-9]+):[^/]+', tag)), next(iter(tags), stored))


def parse_os_release(text):
    values = {}
    for line in text.splitlines():
        if '=' not in line or line.lstrip().startswith('#'): continue
        key, raw = line.split('=', 1)
        try: parts = shlex.split(raw)
        except ValueError: continue
        if len(parts) == 1: values[key] = parts[0]
    version = values.get('VERSION_ID', '')
    return version if re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', version) else None


def image_release(image_id):
    """Read immutable image files once, with a bounded host-side cache."""
    key = str(image_id or '').removeprefix('sha256:')
    if not re.fullmatch(r'[0-9a-f]{64}', key): return None
    cache = IMAGE_OS_CACHE / (key + '.json')
    try:
        for part in (IMAGE_OS_CACHE, *IMAGE_OS_CACHE.parents):
            if part.is_symlink(): return None
        IMAGE_OS_CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
        status = IMAGE_OS_CACHE.stat()
        if status.st_uid != os.geteuid() or status.st_mode & 0o022: return None
        if cache.exists():
            status = cache.lstat()
            if not stat.S_ISREG(status.st_mode) or status.st_uid != os.geteuid() or status.st_mode & 0o022: return None
            value = json.loads(cache.read_text())
            if value.get('image_id') == key and (value.get('version') or time.time() - value.get('checked_at', 0) < 3600):
                version = value.get('version')
                return version if isinstance(version, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', version) else None
        version = None
        mounted = False
        try:
            mount = Path(run(['podman', 'image', 'mount', key], timeout=30).strip())
            mounted = True
            if not mount.is_absolute(): raise ValueError('Invalid image mount')
            root = mount.resolve()
            for rel in ('etc/os-release', 'usr/lib/os-release'):
                source = (root / rel).resolve()
                if source.is_relative_to(root) and source.is_file() and source.stat().st_size <= 65536:
                    version = parse_os_release(source.read_text())
                    if version: break
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        finally:
            if mounted:
                try: run(['podman', 'image', 'unmount', key], timeout=30)
                except (OSError, subprocess.SubprocessError): pass
        fd, temporary = tempfile.mkstemp(prefix='.version-', dir=str(IMAGE_OS_CACHE))
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump({'image_id':key, 'version':version, 'checked_at':time.time()}, out)
            os.replace(temporary, cache)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        return version
    except (OSError, ValueError, TypeError):
        return None


def release_name(os_type, version):
    major=str(version).split('.')[0]
    names={'Red Hat Enterprise Linux':{'7':'Maipo','8':'Ootpa','9':'Plow','10':'Coughlan'},
           'macOS':{'12':'Monterey','13':'Ventura','14':'Sonoma','15':'Sequoia'},
           'Ubuntu':{'20':'Focal Fossa','22':'Jammy Jellyfish','24':'Noble Numbat'}}
    return names.get(os_type,{}).get(major)


def image_os(info, image):
    labels = image.get('Labels') or image.get('Config', {}).get('Labels') or {}
    selected = (info.get('Config', {}).get('Labels') or {}).get('io.mykos.vm.os', '')
    ref = selected or labels.get('io.mykos.os') or image_name(info, image).split('/')[-1].split(':')[0]
    families = [('mssql', 'Microsoft SQL Server'), ('rh', 'Red Hat Enterprise Linux'), ('rocky', 'Rocky Linux'), ('alma', 'AlmaLinux'), ('oracle','Oracle Linux'), ('centos','CentOS Stream'), ('fedora','Fedora'), ('ubuntu', 'Ubuntu'),
                ('wns', 'Windows Server'), ('win', 'Windows'), ('macos', 'macOS'), ('mac', 'macOS')]
    for prefix, title in families:
        if ref.startswith(prefix) and re.fullmatch(r'[0-9][0-9.a-z]*', ref[len(prefix):]):
            version = ref[len(prefix):] if prefix == 'mssql' else labels.get('io.mykos.os.version') or labels.get('version') or ref[len(prefix):]
            if prefix in ('rh','rocky','alma','oracle') and not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', str(version)):
                version = image_release(info.get('Image')) or version
            return {'os_type': title, 'os_version': version, 'os_codename':release_name(title,version),
                    'os_source': 'image', 'image_id': info.get('Image')}
    return {'os_type': labels.get('name') or 'Unknown', 'os_version': labels.get('version') or 'Unknown',
            'os_source': 'image', 'image_id': info.get('Image')}


def statistics(infos):
    names = [name(info) for info in infos if info.get('State', {}).get('Running')]
    if not names:
        return {}
    try:
        output = run(['podman', 'stats', '--no-stream', '--format',
                      '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}', *names], timeout=25)
        return {fields[0]: dict(zip(('cpu', 'memory', 'memory_percent'), fields[1:]))
                for fields in (line.split('|') for line in output.splitlines()) if len(fields) == 4}
    except (OSError, subprocess.SubprocessError):
        return {}


def started_epoch(info):
    try:
        text = info.get('State', {}).get('StartedAt', '')
        if text.startswith('0001-'):
            return None
        text = re.sub(r'(\.\d{6})\d+', r'\1', text).replace('Z', '+00:00')
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


APACHE_CONFIG_DIRS = (Path('/etc/httpd/conf.d'), Path('/etc/apache2/sites-enabled'))
CERTBOT_LIVE = Path('/etc/letsencrypt/live')
WEB_ENV = Path('/etc/mykos/web.env')


def apache_certificate_domain():
    """Read only Apache's public Certbot certificate; no TLS probe or guest call."""
    import ssl
    from urllib.parse import urlsplit

    def domain(value, wildcard=False):
        value=value.lower().rstrip('.')
        if wildcard and value.startswith('*.'): value='*.'+value[2:]
        check=value[2:] if value.startswith('*.') and wildcard else value
        if len(check)>253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',check):return None
        if any(not part or len(part)>63 or part.startswith('-') or part.endswith('-') for part in check.split('.')):return None
        return value

    def matches(name, certified):
        return name==certified or (certified.startswith('*.') and name.endswith(certified[1:]) and name.count('.')==certified.count('.'))

    preferred=None
    try:
        for line in WEB_ENV.read_text().splitlines():
            if line.startswith('MYKOS_PUBLIC_ORIGIN='):
                values=shlex.split(line.partition('=')[2])
                if len(values)==1:preferred=domain(urlsplit(values[0]).hostname or '')
    except (OSError,ValueError):pass
    fallback=None
    for directory in APACHE_CONFIG_DIRS:
        for path in sorted(directory.glob('*.conf')):
            try:
                if path.stat().st_size>1024*1024:continue
                text=path.read_text()
            except (OSError,UnicodeError):continue
            blocks=re.findall(r'<VirtualHost\b[^>]*>(.*?)</VirtualHost\s*>',text,re.I|re.S) or [text]
            for block in blocks:
                sites=[];certificates=[]
                for line in block.splitlines():
                    try:words=shlex.split(line,comments=True)
                    except ValueError:continue
                    if len(words)<2:continue
                    if words[0].lower() in ('servername','serveralias'):
                        sites.extend(filter(None,(domain(item.split(':')[0]) for item in words[1:])))
                    elif words[0].lower()=='sslcertificatefile':certificates.append(Path(words[1]))
                for certificate in certificates:
                    # Certbot's symlinks into archive/ are intentional. Never read a key.
                    if certificate.name not in ('cert.pem','fullchain.pem') or '..' in certificate.parts:continue
                    try:
                        certificate.relative_to(CERTBOT_LIVE)
                        decoded=ssl._ssl._test_decode_cert(str(certificate))
                    except (ValueError,OSError,ssl.SSLError):continue
                    names=[domain(value,True) for kind,value in decoded.get('subjectAltName',()) if kind=='DNS']
                    if not any(names):names=[domain(value,True) for group in decoded.get('subject',()) for key,value in group if key=='commonName']
                    names=[name for name in names if name]
                    if not names:continue
                    if preferred and any(matches(preferred,name) for name in names):return preferred
                    candidate=next((site for site in sites if any(matches(site,name) for name in names)),None)
                    fallback=fallback or candidate or names[0].removeprefix('*.')
    return fallback


def hardware(previous=None):
    """Bounded SMBIOS inventory, reused for a day by the root collector."""
    now=int(time.time())
    if isinstance(previous,dict) and isinstance(previous.get('details'),dict):
        stamp=previous.get('collected_at')
        if type(stamp) is int and 0 <= now-stamp < (86400 if previous['details'] else 600):return previous
    details={};section=None
    fields={('System Information','Manufacturer'):'manufacturer',('System Information','Product Name'):'model',
            ('System Information','Serial Number'):'serial',('System Information','SKU Number'):'sku',
            ('Chassis Information','Asset Tag'):'asset_tag',('BIOS Information','Vendor'):'bios_vendor',
            ('BIOS Information','Version'):'bios_version',('BIOS Information','Release Date'):'bios_date'}
    try:
        output=run(['/usr/sbin/dmidecode','--type','0,1,3'],timeout=5)
        if len(output)>65536:raise ValueError('Oversized DMI inventory')
        for line in output.splitlines():
            if line and not line[0].isspace():section=line.strip()
            elif line.startswith('\t') and not line.startswith('\t\t') and ':' in line:
                key,value=line.strip().split(':',1);field=fields.get((section,key));value=' '.join(value.split())[:160]
                if field and value.lower() not in ('','not specified','not available','unknown','none','default string','to be filled by o.e.m.'):
                    details[field]=value
    except (OSError,ValueError,subprocess.SubprocessError):pass
    return {'collected_at':now,'details':details}
