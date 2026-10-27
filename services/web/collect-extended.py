#!/usr/bin/env python3
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from mykos_config import BASE
import host_monitoring as host
from mykos_vpn_topology import topology
from mykos_vpn_gateway import summaries as gateway_profiles
OUT = BASE / "data/extended.json"
SHOP = re.compile(r"^[0-9]{8}(?:\.|$)")


def run(args, timeout=15):
    return subprocess.run(
        args, capture_output=True, text=True,
        timeout=timeout, check=True
    ).stdout.strip()


def collect(shop_filter=None):
    infos = host.containers(shop_filter) if shop_filter else host.containers()
    resources = host.statistics(infos)
    nodes = {}
    shared_gateways=gateway_profiles()
    for info in infos:
        name = host.name(info)
        if not SHOP.match(name):
            continue
        shop = name[:8]
        running = bool(info.get('State', {}).get('Running'))
        nic = host.network(info)
        memberships=[{'name':key,'address':value.get('IPAddress') or None,'prefix':value.get('IPPrefixLen'),
                      'gateway':value.get('Gateway') or None,'mac':value.get('MacAddress') or None}
                     for key,value in (info.get('NetworkSettings',{}).get('Networks') or {}).items()]
        nodes[shop] = {
            "container": name,
            "running": running,
            "ipv4": nic.get("ipv4") or "Unknown",
            "interface": nic.get("interface"),
            "networks": memberships,
            "eth1_ip": nic.get("ipv4") or "Unknown", # legacy API alias
            "started_at_epoch": host.started_epoch(info) if running else None,
            "resources": resources.get(name, {}),
            "vpn_topology": topology(shop,shared_gateways),
            "vpn": host.cached_vpn(info, BASE / "data/status.json") if running else {
                "status": "unknown"
            }
        }

    if shop_filter and OUT.exists():
        nodes={**{k:v for k,v in json.loads(OUT.read_text()).get('nodes',{}).items() if k!=shop_filter},**nodes}

    payload = {
        "collected_at": int(time.time()),
        "nodes": nodes
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(
        prefix=".extended-", dir=str(OUT.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o640)
        import grp
        os.chown(tmp, 0, grp.getgrnam("mykosweb").gr_gid)
        os.replace(tmp, OUT)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    for shop, node in sorted(nodes.items()):
        print(
            shop,
            "CPU:", node["resources"].get("cpu", "N/A"),
            "VPN:", node["vpn"]["status"]
        )


if __name__ == "__main__":
    import sys
    from collector_lock import collect_locked
    shop=sys.argv[1] if len(sys.argv)==2 else None
    if shop is not None and not re.fullmatch(r'[0-9]{8}',shop): raise SystemExit('Expected eight-digit ID')
    collect_locked(OUT,lambda:collect(shop))
