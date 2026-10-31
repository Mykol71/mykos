"""Read-only, cached Meraki site metadata for configured VPN links, independent of tunnel state."""
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shlex
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

CONFIG = Path('/etc/mykos/pos.env')
CACHE = Path('/var/cache/mykos/meraki-sites.json')
TTL = 900
MAX_STALE = 7 * 86400
API = 'https://api.meraki.com/api/v1/organizations/'


def credentials(path=CONFIG):
    result = {}
    if not path.exists():
        return result
    if path.is_symlink() or path.stat().st_mode & 0o022:
        raise ValueError('Unsafe Meraki configuration permissions')
    for line in path.read_text().splitlines():
        parts = shlex.split(line, comments=True)
        if parts and parts[0] == 'export':
            parts = parts[1:]
        if len(parts) == 1 and '=' in parts[0]:
            key, value = parts[0].split('=', 1)
            if key in ('MERAKI_API_KEY', 'MERAKI_ORG_ID'):
                result[key] = value
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_pages(org, endpoint, key, opener=None, deadline=None):
    """Bounded GETs only; never send the API key to a pagination/redirect host."""
    opener = opener or build_opener(NoRedirect())
    path = '/api/v1/organizations/' + org + endpoint
    url = 'https://api.meraki.com' + path + '?perPage=1000'
    deadline = deadline or time.monotonic() + 20
    rows, visited = [], set()
    for _ in range(20):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc != 'api.meraki.com'
                or parsed.path != path or parsed.fragment or url in visited):
            raise ValueError('Unexpected Meraki pagination')
        visited.add(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Meraki inventory deadline exceeded')
        request = Request(url, headers={'X-Cisco-Meraki-API-Key': key, 'Accept': 'application/json'}, method='GET')
        with opener.open(request, timeout=min(5, remaining)) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError('Oversized Meraki inventory')
            page = json.loads(raw)
            if not isinstance(page, list) or not all(isinstance(row, dict) for row in page):
                raise ValueError('Invalid Meraki inventory')
            rows.extend(page)
            if len(rows) > 20000:
                raise ValueError('Meraki inventory exceeds limit')
            links = re.findall(r'<([^>]+)>;\s*rel="?next"?', response.headers.get('Link', ''), re.I)
            if len(links) > 1:
                raise ValueError('Ambiguous Meraki pagination')
            if not links:
                return rows
            url = links[0]
    raise ValueError('Too many Meraki inventory pages')


def text(value, limit=256):
    return value[:limit] if isinstance(value, str) else ''


def coordinates(device):
    lat, lon = device.get('lat'), device.get('lng')
    if (type(lat) in (int, float) and type(lon) in (int, float)
            and math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180):
        return {'lat': lat, 'lon': lon}
    return {}


def inventory(networks, devices, uplinks):
    """Keep only display metadata, not whole cloud responses or credentials."""
    names = {n.get('id'): text(n.get('name')) for n in networks if isinstance(n.get('id'), str)}
    hardware = {d.get('serial'): d for d in devices if isinstance(d.get('serial'), str)}
    result = []
    for row in uplinks:
        network = row.get('networkId')
        if not isinstance(network, str):
            continue
        device = hardware.get(row.get('serial'), {})
        if device.get('networkId') not in (None, network):
            continue
        for uplink in row.get('uplinks', []):
            if not isinstance(uplink, dict):
                continue
            for value in (uplink.get('publicIp'), uplink.get('ip')):
                try:
                    address = str(ipaddress.ip_address(value))
                except (ValueError, TypeError):
                    continue
                item = {'peer': address, 'network_id': network,
                        'shop_name': names.get(network, ''), 'router_name': text(device.get('name')),
                        'address': text(device.get('address'), 512), 'model': text(device.get('model'), 64),
                        'serial': text(device.get('serial'), 64), **coordinates(device)}
                if item not in result:
                    result.append(item)
    return result


def save_cache(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.meraki-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, allow_nan=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_sites(config=CONFIG, cache=CACHE, fetch=fetch_pages, now=None):
    now = time.time() if now is None else now
    settings = credentials(config)
    key, org = settings.get('MERAKI_API_KEY'), settings.get('MERAKI_ORG_ID')
    if not key or not org:
        return [], 'not_configured'
    if not re.fullmatch(r'[0-9]+', org) or any(ord(c) < 33 or ord(c) > 126 for c in key):
        return [], 'invalid_configuration'
    try:
        old = json.loads(cache.read_text())
        if not isinstance(old, dict):
            old = {}
        if old.get('organization') != org:
            old = {}
    except (OSError, ValueError, TypeError):
        old = {}
    if 0 <= now - old.get('attempted_at', 0) < TTL:
        return (old.get('sites', []) if 0 <= now - old.get('updated_at', 0) < MAX_STALE else []), old.get('status', 'unavailable')
    try:
        deadline = time.monotonic() + 20
        data = {endpoint: fetch(org, endpoint, key, deadline=deadline)
                for endpoint in ('/networks', '/devices', '/appliance/uplink/statuses')}
        sites = inventory(data['/networks'], data['/devices'], data['/appliance/uplink/statuses'])
        value = {'organization': org, 'attempted_at': now, 'updated_at': now, 'status': 'ready', 'sites': sites}
    except (OSError, ValueError, TypeError, KeyError, TimeoutError) as error:
        status = 'permission_denied' if isinstance(error, HTTPError) and error.code in (401, 403) else 'unavailable'
        value = {**old, 'organization': org, 'attempted_at': now, 'status': status}
    save_cache(cache, value)
    return (value.get('sites', []) if 0 <= now - value.get('updated_at', 0) < MAX_STALE else []), value['status']


def match_site(peer, sites):
    try:
        peer = str(ipaddress.ip_address(peer))
    except (ValueError, TypeError):
        return None
    matches = [site for site in sites if site.get('peer') == peer]
    if not matches or len({s['network_id'] for s in matches}) != 1:
        return None
    if len({s.get('serial') for s in matches}) > 1:
        # A shared WAN/HA pair identifies the site, not one particular appliance.
        return {k: matches[0][k] for k in ('peer', 'network_id', 'shop_name')}
    return dict(matches[0])


def enrich(nodes, loader=load_sites):
    candidates = []
    for node in nodes.values():
        established = node.get('vpn', {}).get('established_connections', [])
        for link in node.get('vpn_topology', {}).get('links', []):
            up = link.get('name') in established or 'mykos-' + link.get('name', '') in established
            state = 'up' if up else 'down' if node.get('vpn', {}).get('status') in ('up', 'down') else 'unknown'
            candidates.append((link, state))
        for link in node.get('vpn_topology', {}).get('via_gateways', []):
            candidates.append((link, 'up' if link.get('full_tunnel_sa') is True else 'unknown'))
    if not candidates:
        return
    try:
        sites, status = loader()
        for link, state in candidates:
            site = match_site(link.get('peer'), sites)
            if site:
                link['meraki'] = {**site, 'source': 'Meraki', 'stale': status != 'ready', 'vpn_status': state}
    except (OSError, ValueError, TypeError, KeyError):
        # Cloud inventory must never interrupt local monitoring or VPN operation.
        return


def organization_sites(loader=load_sites):
    """All sanitized organization routers for mapping, without inferred VPN state."""
    try:
        sites, status = loader()
        unique = {}
        for site in sites:
            key = (site.get('network_id'), site.get('serial'))
            unique.setdefault(key, {**site, 'source': 'Meraki', 'stale': status != 'ready', 'vpn_status': 'unknown'})
        return list(unique.values())
    except (OSError, ValueError, TypeError, KeyError):
        return []
