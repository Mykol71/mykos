"""Narrow root operations for independent MYKOS host-to-host IKEv2 peers."""
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

STATE = Path('/etc/mykos/peers')
IPSEC = Path('/etc/ipsec.d')


def selection(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Invalid peer connection')
    return 'mykos-peer-' + value


def address(value):
    ip = ipaddress.IPv4Address(value)
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local or int(ip) == 0xffffffff:
        raise ValueError('A reachable unicast IPv4 address is required')
    return str(ip)


def command(args, timeout=15, required=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if required and result.returncode:
        # Do not return configuration, PSKs or unrelated VPN status to the caller.
        raise ValueError('Peer VPN operation failed: ' + args[0].rsplit('/', 1)[-1] + ' ' + ' '.join(args[1:3]))
    return result


def status(identifier):
    name = selection(identifier)
    if not (STATE / (identifier + '.json')).is_file():
        return {'configured': False, 'connected': False}
    output = command(['/usr/sbin/ipsec', 'trafficstatus'], required=False).stdout
    connected = any('"' + name + '"' in line and 'ESP' in line.upper() for line in output.splitlines())
    return {'configured': True, 'connected': connected}


def write_new(path, data, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def firewall_plan(remote):
    if command(['/usr/bin/firewall-cmd', '--state'], required=False).returncode:
        return []
    route = json.loads(command(['/usr/sbin/ip', '-j', 'route', 'get', remote]).stdout)
    interface = route[0].get('dev', '') if route else ''
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', interface):
        raise ValueError('Cannot determine peer network interface')
    zone = command(['/usr/bin/firewall-cmd', '--get-zone-of-interface=' + interface], required=False).stdout.strip()
    if not zone or zone == 'no zone':
        zone = command(['/usr/bin/firewall-cmd', '--get-default-zone']).stdout.strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', zone):
        raise ValueError('Cannot determine peer firewall zone')
    rule = f'rule family="ipv4" source address="{remote}/32" service name="ipsec" accept'
    planned = []
    for permanent in (False, True):
        args = ['/usr/bin/firewall-cmd', '--zone=' + zone] + (['--permanent'] if permanent else [])
        if command(args + ['--query-rich-rule=' + rule], required=False).returncode:
            planned.append({'zone': zone, 'rule': rule, 'permanent': permanent})
    return planned


def firewall_change(items, add):
    for item in items:
        args = ['/usr/bin/firewall-cmd', '--zone=' + item['zone']]
        if item['permanent']: args.append('--permanent')
        if not add:
            queried = command(args + ['--query-rich-rule=' + item['rule']], required=False)
            if queried.returncode == 1: continue
            if queried.returncode != 0: raise ValueError('Peer firewall cleanup unavailable; ownership retained')
        command(args + [('--add-rich-rule=' if add else '--remove-rich-rule=') + item['rule']])


def configure(identifier, settings):
    name = selection(identifier)
    if set(settings) != {'local', 'remote', 'secret'}:
        raise ValueError('Invalid peer VPN settings')
    local, remote = address(settings['local']), address(settings['remote'])
    secret = settings['secret']
    if local == remote or not isinstance(secret, str) or not re.fullmatch('[0-9a-f]{64}', secret):
        raise ValueError('Invalid peer VPN settings')
    addresses = json.loads(command(['/usr/sbin/ip', '-j', '-4', 'address', 'show']).stdout)
    if local not in {x['local'] for dev in addresses for x in dev.get('addr_info', [])}:
        raise ValueError('Local peer address is not assigned to this host')
    record = STATE / (identifier + '.json')
    if record.exists():
        raise ValueError('Peer VPN already exists')
    for existing in STATE.glob('*.json'):
        if json.loads(existing.read_text()).get('remote') == remote:
            raise ValueError('A peer VPN already manages this address')
    # Standard RHEL includes are required; never rewrite unrelated VPN configuration.
    if 'include /etc/ipsec.d/*.conf' not in Path('/etc/ipsec.conf').read_text():
        raise ValueError('Standard IPsec configuration include is required')
    if 'include /etc/ipsec.d/*.secrets' not in Path('/etc/ipsec.secrets').read_text():
        raise ValueError('Standard IPsec secrets include is required')
    conf = IPSEC / (name + '.conf')
    key = IPSEC / (name + '.secrets')
    if any(path.exists() or path.is_symlink() for path in (conf, key)):
        raise ValueError('Peer configuration path already exists')
    created = []
    planned = firewall_plan(remote)
    try:
        # Record ownership before firewall mutations so interruption is recoverable.
        write_new(record, json.dumps({'local': local, 'remote': remote, 'firewall': planned}) + '\n', 0o600)
        created.append(record)
        write_new(conf, f'''# Managed MYKOS peer {identifier}; host addresses only.
conn {name}
    type=tunnel
    left={local}
    leftid={local}
    leftsubnet={local}/32
    right={remote}
    rightid={remote}
    rightsubnet={remote}/32
    authby=secret
    ikev2=insist
    pfs=yes
    dpddelay=30
    dpdtimeout=120
    dpdaction=hold
    auto=start
''', 0o600)
        created.append(conf)
        write_new(key, f'{local} {remote} : PSK "{secret}"\n', 0o600)
        created.append(key)
        firewall_change(planned, True)
        command(['/usr/bin/systemctl', 'enable', '--now', 'ipsec'], timeout=40)
        command(['/usr/sbin/ipsec', 'auto', '--rereadsecrets'])
        command(['/usr/sbin/ipsec', 'auto', '--replace', name])
    except BaseException:
        if created:
            command(['/usr/sbin/ipsec', 'auto', '--delete', name], required=False)
        if created:
            firewall_change(planned, False)
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return status(identifier)


def remove(identifier):
    name = selection(identifier)
    record = STATE / (identifier + '.json')
    if not record.is_file():
        return {'configured': False, 'connected': False}
    command(['/usr/sbin/ipsec', 'auto', '--delete', name], required=False)
    if status(identifier)['connected']:
        raise ValueError('Peer VPN teardown failed; ownership retained')
    firewall_change(json.loads(record.read_text()).get('firewall', []), False)
    for path in (IPSEC / (name + '.conf'), IPSEC / (name + '.secrets'), record):
        path.unlink(missing_ok=True)
    command(['/usr/sbin/ipsec', 'auto', '--rereadsecrets'], required=False)
    return {'configured': False, 'connected': False}


def dispatch(args):
    if len(args) not in (2, 3):
        raise ValueError('Invalid peer VPN arguments')
    action, identifier = args[:2]
    selection(identifier)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == 'peer-vpn-status' and len(args) == 2:
            return status(identifier)
        if action == 'peer-vpn-create' and len(args) == 3:
            return configure(identifier, json.loads(args[2]))
        if action == 'peer-vpn-connect' and len(args) == 2:
            if not status(identifier)['configured']:
                raise ValueError('Peer VPN is not configured')
            command(['/usr/sbin/ipsec', 'auto', '--up', selection(identifier)], timeout=60)
            return status(identifier)
        if action == 'peer-vpn-remove' and len(args) == 2:
            return remove(identifier)
        raise ValueError('Unknown peer VPN operation')
