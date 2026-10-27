#!/usr/bin/env python3

import json
import grp
import re
import subprocess
import tempfile
import os
import time
from pathlib import Path
from datetime import datetime, timezone

from mykos_config import BASE
import host_monitoring as host
OUTPUT = BASE / "data" / "details.json"

SHOP = re.compile(r"^([0-9]{8})(?:\.|$)")


def command(args, timeout=15):
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=True
    ).stdout


def get_ips(info):
    networks = info.get("NetworkSettings", {}).get("Networks", {})
    ips = []

    for network, details in networks.items():
        ip = details.get("IPAddress")
        if ip:
            ips.append({"network": network, "address": ip})

    return ips


def collect(shop_filter=None):
    infos = host.containers(shop_filter) if shop_filter else host.containers()
    catalog = host.image_catalog(infos)
    details = {}
    for info in infos:
        name = host.name(info)
        match = SHOP.match(name)
        if not match:
            continue
        shop = match.group(1)
        state = info.get("State", {})
        running = state.get("Running", False)

        started = state.get("StartedAt", "")
        vm_os = (info.get('Config', {}).get('Labels') or {}).get('io.mykos.vm.os', '')
        desktop = bool(re.fullmatch(r'(?:win(?:11|10|8e|7u|xp|me|2k|98)|wns(?:2025|2022|2019|2016)|mac(?:15|14|13|12)|ubuntu(?:20|22|24)\.04)', vm_os))
        vm_type = 'Windows Server' if vm_os.startswith('wns') else 'Windows' if vm_os.startswith('win') else 'macOS' if vm_os.startswith('mac') else 'Ubuntu'
        os_info = {'os_type': vm_type, 'os_version': re.sub(r'^(?:ubuntu|wns|win|mac)', '', vm_os)} if desktop else host.image_os(info, host.image_info(info, catalog))

        os_info['os_codename']=host.release_name(os_info.get('os_type'),os_info.get('os_version'))
        details[shop] = {
            "container": name,
            "image": host.image_name(info, host.image_info(info, catalog)),
            **os_info,
            'desktop': desktop,
            'persistent_storage': info.get('HostConfig', {}).get('AutoRemove') is False,
            'desktop_host': (info.get('Config', {}).get('Labels') or {}).get('io.mykos.vm.host') if desktop else None,
            "running": running,
            "started_at": started,
            "ips": get_ips(info),
            "vpn": host.cached_vpn(info, BASE / "data/status.json") if running and not desktop else {
                "status": "unknown"
            }
        }

    if shop_filter and OUTPUT.exists():
        details={**json.loads(OUTPUT.read_text()).get('details',{}),**details}

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(
        dir=str(OUTPUT.parent),
        prefix=".details-"
    )

    try:
        with os.fdopen(fd, "w") as file:
            json.dump({
                "collected_at": int(time.time()),
                "details": details
            }, file, indent=2)
            file.write("\n")

        os.chmod(tmp, 0o640)
        if os.geteuid() == 0:
            os.chown(tmp, 0, grp.getgrnam("mykosweb").gr_gid)
        os.replace(tmp, OUTPUT)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    for shop in details:
        print(shop, details[shop]["container"])


if __name__ == "__main__":
    import sys
    from collector_lock import collect_locked
    shop=sys.argv[1] if len(sys.argv)==2 else None
    if shop is not None and not re.fullmatch(r'[0-9]{8}',shop): raise SystemExit('Expected eight-digit ID')
    collect_locked(OUTPUT,lambda:collect(shop))
