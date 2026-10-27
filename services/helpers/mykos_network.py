"""Podman native networking: LAN DHCP or a private bridge with host NAT."""
import hashlib
import ipaddress
import json
import re
from pathlib import Path
from mykos_runtime import PODMAN, config, network_device, run


def name(settings):
    value = settings.get('native_network', 'mykos-lan')
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', value) or value in ('host','none','bridge','podman'):
        raise ValueError('Choose a dedicated native_network name')
    return value


def ensure(settings=None):
    settings = config() if settings is None else settings
    selected = name(settings)
    driver = settings.get('native_network_driver', 'bridge')
    if driver not in ('macvlan', 'bridge'):
        raise ValueError('native_network_driver must be macvlan or bridge')
    backend = run([PODMAN, 'info', '--format', '{{.Host.NetworkBackend}}']).strip()
    if backend not in ('cni', 'netavark'):
        raise ValueError('Unsupported Podman networking backend: ' + backend)
    parent = None
    unit = None
    create = [PODMAN, 'network', 'create', '--ignore', '--driver', driver]
    if driver == 'macvlan':
        parent = network_device(settings)
        if (Path('/sys/class/net') / parent / 'wireless').exists():
            raise ValueError('LAN macvlan requires Ethernet; choose native_network_driver=bridge for Wi-Fi NAT')
        unit = 'cni-dhcp.socket' if backend == 'cni' else 'netavark-dhcp-proxy.socket'
        # Verify the host DHCP proxy exists before creating network state.
        run(['systemctl', 'cat', unit])
        create += ['--opt', 'parent=' + parent, '--ipam-driver', 'dhcp']
    else:
        subnet = address_pool(settings)
        create += ['--ipam-driver', 'host-local', '--subnet', str(subnet),
                   '--gateway', str(subnet.network_address + 1)]
    exists = run([PODMAN, 'network', 'ls', '--format', '{{.Name}}']).splitlines()
    if selected not in exists:
        if driver == 'bridge':
            check_routes(subnet)
        run(create + [selected], timeout=30)
    value = json.loads(run([PODMAN, 'network', 'inspect', selected]))[0]
    actual_parent = value.get('network_interface') or value.get('options', {}).get('parent')
    ipam = 'dhcp' if driver == 'macvlan' else 'host-local'
    if (value.get('driver') != driver or (driver == 'macvlan' and actual_parent != parent)
            or value.get('ipam_options', {}).get('driver') != ipam or value.get('internal')):
        raise ValueError('Existing network does not match native networking configuration; it was left unchanged')
    if driver == 'bridge':
        subnets = value.get('subnets', [])
        if len(subnets) != 1 or subnets[0].get('subnet') != str(subnet) or subnets[0].get('gateway') != str(subnet.network_address + 1):
            raise ValueError('Existing network subnet/gateway mismatch; it was left unchanged')
    if unit:
        run(['systemctl', 'enable', '--now', unit], timeout=30)
    return selected, backend


def arguments(settings, container_name):
    selected, backend = ensure(settings)
    mac = '02:' + ':'.join('%02x' % b for b in hashlib.sha256(container_name.encode()).digest()[:5])
    # CNI assigns eth0 to a container attached to exactly one network. Netavark
    # also supports explicitly selecting the interface name.
    network = selected + (':interface_name=eth0' if backend == 'netavark' else '')
    args = ['--network', network, '--mac-address', mac,
            '--label', 'io.mykos.network.interface=eth0', '--label', 'io.mykos.network.name=' + selected]
    if settings.get('native_network_driver', 'bridge') == 'bridge':
        address = allocate_ip(settings, container_name)
        args += ['--ip', address, '--label', 'io.mykos.network.ip=' + address]
    return args


def address_pool(settings):
    subnet = ipaddress.IPv4Network(settings.get('native_network_subnet', '192.168.22.0/24'), strict=True)
    if not subnet.is_private or not 24 <= subnet.prefixlen <= 28:
        raise ValueError('Choose a private IPv4 subnet between /24 and /28')
    return subnet


def check_routes(subnet):
    for route in json.loads(run(['ip', '-j', 'route'])):
        destination = route.get('dst', 'default')
        if destination == 'default': continue
        other = ipaddress.ip_network(destination, strict=False)
        if other.version == 4 and subnet.overlaps(other):
            raise ValueError('Private NAT subnet overlaps an existing host route: ' + destination)


def allocate_ip(settings, container_name):
    """Called under the shared lifecycle lock; stopped containers retain claims."""
    selected = name(settings)
    subnet = address_pool(settings)
    ids = run([PODMAN, 'ps', '-a', '--format', '{{.ID}}']).splitlines()
    infos = json.loads(run([PODMAN, 'container', 'inspect', *ids])) if ids else []
    used = set()
    for info in infos:
        nic = info.get('NetworkSettings', {}).get('Networks', {}).get(selected)
        if nic is None: continue
        labels = info.get('Config', {}).get('Labels') or {}
        for address in (labels.get('io.mykos.network.ip'), nic.get('IPAddress'),
                        (nic.get('IPAMConfig') or {}).get('IPv4Address')):
            if not address: continue
            parsed = ipaddress.IPv4Address(address.split('/')[0])
            used.add(parsed)
            same = info.get('Name', '').lstrip('/') == container_name
            recovery = (info.get('Name', '').lstrip('/').startswith('mykos-recovery-')
                        and not info.get('State', {}).get('Running')
                        and info.get('Config', {}).get('Hostname') == container_name)
            if (same or recovery) and parsed in subnet:
                return str(parsed)
    # Reserve .0, .1 (gateway) and the broadcast address. Collision probing
    # allows every other address while remaining deterministic for an ID/name.
    count = subnet.num_addresses - 3
    offset = int.from_bytes(hashlib.sha256(container_name.encode()).digest()[:4], 'big') % count
    for index in range(count):
        address = subnet.network_address + 2 + (offset + index) % count
        if address not in used: return str(address)
    raise ValueError('Private NAT address pool is full; no container was created')
