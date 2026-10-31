"""Read host performance counters without privileged commands or dependencies."""
import os
import json
import platform
import shlex
import re
import socket
from ipaddress import IPv4Address
from pathlib import Path
import shutil
import time
from mykos_config import BASE

PROC = Path('/proc')


def internal_ip():
    """Return the host source IPv4 selected by its route; send no packets."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(('8.8.8.8', 53))
            address = IPv4Address(connection.getsockname()[0])
        return None if address.is_loopback or address.is_unspecified else str(address)
    except (OSError, ValueError):
        return None


def cpu_times():
    fields = (PROC / 'stat').read_text().splitlines()[0].split()
    if fields[0] != 'cpu' or len(fields) < 5:
        raise ValueError('Missing aggregate CPU counters')
    # Guest counters are already included in user/nice and must not be added again.
    ticks = [int(value) for value in fields[1:9]]
    return sum(ticks), ticks[3] + (ticks[4] if len(ticks) > 4 else 0)


def snapshot():
    release = {}
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and key in ('PRETTY_NAME', 'VERSION_ID'):
                parsed = shlex.split(value)
                release[key] = parsed[0] if parsed else ''
    except (OSError, ValueError):
        pass
    result = {'collected_at': int(time.time()), 'cpu_percent': None,
              'cpu_count': os.cpu_count(), 'load_average': None,
              'memory': None, 'swap': None, 'disk': None, 'data': None, 'uptime_seconds': None,
              'graph_totals': None, 'graph_counts': None,
              'host': {'hostname': socket.gethostname(), 'status': 'online',
                       'os': release.get('PRETTY_NAME', 'Linux'),
                       'version': release.get('VERSION_ID', 'Unknown'),
                       'os_codename': release.get('VERSION_CODENAME') or (re.search(r'\(([^)]+)\)',release.get('VERSION','')).group(1) if re.search(r'\(([^)]+)\)',release.get('VERSION','')) else None),
                       'kernel': platform.release(), 'internal_ip': internal_ip(), 'vpn': 'unknown'}}
    try:
        status = json.loads((BASE / 'data/status.json').read_text())
        totals = status.get('graph_totals')
        if 0 <= time.time() - status['collected_at'] <= 120:
            certificate_domain = status.get('host_certificate_domain')
            if isinstance(certificate_domain,str) and certificate_domain:
                result['host']['certificate_domain'] = certificate_domain
            vpn = status.get('host_vpn_status')
            if vpn in ('up', 'down', 'not_configured', 'unknown'):
                result['host']['vpn'] = vpn
        if (0 <= time.time() - status['collected_at'] <= 120 and isinstance(totals, dict)
                and all(type(totals.get(key)) is int and totals[key] >= 0 for key in ('containers', 'vpns'))):
            result['graph_totals'] = {key: totals[key] for key in ('containers', 'vpns')}
            observations = status.get('observations', {})
            if isinstance(observations, dict):
                result['graph_counts'] = {
                    'containers': sum(isinstance(v, dict) and v.get('status') == 'online' for v in observations.values()),
                    'vpns': sum(isinstance(v, dict) and v.get('vpn_status') == 'up' for v in observations.values())}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        before, idle_before = cpu_times()
        time.sleep(0.1)
        after, idle_after = cpu_times()
        if after > before and idle_after >= idle_before:
            result['cpu_percent'] = round(max(0, min(100,
                100 * (1 - (idle_after - idle_before) / (after - before)))), 1)
    except (OSError, ValueError, IndexError):
        pass
    try:
        values = {}
        for line in (PROC / 'meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            values[key] = int(value.split()[0]) * 1024
        for label, total_key, free_key in (
                ('memory', 'MemTotal', 'MemAvailable'),
                ('swap', 'SwapTotal', 'SwapFree')):
            total, available = values[total_key], values[free_key]
            used = max(0, total - available)
            result[label] = {'total_bytes': total, 'used_bytes': used,
                             'percent': round(used / total * 100, 1) if total else 0}
    except (OSError, ValueError, KeyError, IndexError):
        pass
    try:
        result['load_average'] = list(os.getloadavg())
    except OSError:
        pass
    for label, path in (('disk', '/'), ('data', '/home')):
        try:
            total, used, free = shutil.disk_usage(path)
            result[label] = {'path': path, 'total_bytes': total, 'used_bytes': used,
                             'free_bytes': free,
                             'percent': round(used / total * 100, 1) if total else 0}
        except OSError:
            pass
    try:
        result['uptime_seconds'] = float((PROC / 'uptime').read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return result
