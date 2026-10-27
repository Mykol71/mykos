#!/usr/bin/env python3

import json
import grp
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from mykos_config import BASE
import host_monitoring as host
DATA = BASE / "data"
INVENTORY = BASE / "inventory.json"
STATUS = DATA / "status.json"
POS = Path('/usr/local/share/mykos/pos')


def graph_totals(root=POS):
    """Count configured resources without following links or reading secrets."""
    try:
        if root.is_symlink() or not root.is_dir():
            return None
        with os.scandir(root) as entries:
            containers = sum(bool(re.fullmatch(r'\.[0-9]{8}\.container', entry.name))
                             and entry.is_file(follow_symlinks=False) for entry in entries)
        vpns = 0
        def failed(error):
            raise error
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
            dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
            for name in files:
                path = Path(directory) / name
                if name.endswith('.conn.conf') and not path.is_symlink() and path.is_file():
                    vpns += 1
        return {'containers': containers, 'vpns': vpns}
    except OSError:
        return None

SHOP_PATTERN = re.compile(r"^([0-9]{8})(?:\.|$)")


def run(command, timeout=15):
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=timeout,
        check=True
    ).stdout



def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp = tempfile.mkstemp(
        prefix=".mykos-",
        dir=str(path.parent)
    )

    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        os.chmod(temp, 0o640)
        if os.geteuid() == 0:
            os.chown(temp, 0, grp.getgrnam("mykosweb").gr_gid)
        os.replace(temp, str(path))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def collect(shop_filter=None):
    old_inventory = {"nodes": []}

    if INVENTORY.exists():
        old_inventory = json.loads(INVENTORY.read_text())

    metadata = {
        str(node["id"]): node
        for node in old_inventory.get("nodes", [])
        if not str(node.get("name", "")).startswith("Sample ")
    }

    host_only = shop_filter == "host"
    infos = [] if host_only else host.containers(shop_filter) if shop_filter else host.containers()
    nodes = []
    observations = {}
    vpn_samples = {}
    for info in infos:
        name = host.name(info)
        match = SHOP_PATTERN.match(name)
        if not match:
            continue
        running = bool(info.get('State', {}).get('Running'))
        shop = match.group(1)

        if shop in observations:
            print("Duplicate shop code:", shop)
            continue

        existing = metadata.get(shop, {})

        node = {
            "id": shop,
            "name": existing.get("name", name),
            "hub": existing.get("hub", "Unassigned"),
            "lat": existing.get("lat"),
            "lon": existing.get("lon")
        }

        for field in ('shop_name', 'pos_type', 'app_info'):
            if field in existing: node[field] = existing[field]
        nodes.append(node)

        vpn = host.query_vpn(info)
        vpn_samples[name] = {'identity': host.identity(info), 'vpn': vpn, 'collected_at': int(time.time())}
        observations[shop] = {
            "status": "online" if running else "offline",
            "vpn_status": (
                vpn["status"]
            )
        }

    if shop_filter:
        nodes += [node for node in old_inventory.get('nodes',[]) if host_only or str(node['id'])!=shop_filter]
        old=json.loads(STATUS.read_text()) if STATUS.exists() else {}
        observations={**{key:value for key,value in old.get('observations',{}).items() if key!=shop_filter},**observations}
        vpn_samples={**{key:value for key,value in old.get('vpn_samples',{}).items() if host_only or not SHOP_PATTERN.match(key) or SHOP_PATTERN.match(key).group(1)!=shop_filter},**vpn_samples}
    nodes.sort(key=lambda n: n["id"])

    if not host_only:
        atomic_json(INVENTORY, {**({"hosts": old_inventory["hosts"]} if "hosts" in old_inventory else {}), "nodes": nodes})
    atomic_json(STATUS, {
        "collected_at": int(time.time()),
        "graph_totals": graph_totals(),
        "host_vpn_status": host.query_vpn()["status"] if not shop_filter or host_only else old.get("host_vpn_status","unknown"),
        "host_certificate_domain": host.apache_certificate_domain() if not shop_filter or host_only else old.get("host_certificate_domain"),
        "vpn_samples": vpn_samples,
        "observations": observations
    })

    for node in nodes:
        shop = node["id"]
        state = observations.get(shop, {"status":"unknown","vpn_status":"unknown"})

        print(
            shop,
            state.get("status","unknown"),
            "VPN:", state.get("vpn_status","unknown")
        )


if __name__ == "__main__":
    import sys
    from collector_lock import collect_locked
    shop=sys.argv[1] if len(sys.argv)==2 else None
    if shop is not None and shop!='host' and not re.fullmatch(r'[0-9]{8}',shop): raise SystemExit('Expected eight-digit ID')
    collect_locked(STATUS,lambda:collect(shop))
