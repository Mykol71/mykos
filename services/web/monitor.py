#!/usr/bin/env python3
import json
import os
import re
import subprocess
import time
from pathlib import Path

from mykos_config import BASE
GREEN = "\033[1;32m"
RED = "\033[1;31m"
YELLOW = "\033[1;33m"
CYAN = "\033[1;36m"
GRAY = "\033[0;37m"
RESET = "\033[0m"

def read(name, default):
    try:
        return json.loads((BASE / name).read_text())
    except (OSError, ValueError):
        return default

def status(value):
    labels = {
        "online": "Online",
        "offline": "Offline",
        "up": "Connected",
        "down": "Disconnected",
        "not_configured": "Not Configured",
        "unknown": "Unknown"
    }
    color = {
        "online": GREEN, "up": GREEN,
        "offline": RED, "down": RED,
        "unknown": YELLOW,
        "not_configured": GRAY
    }.get(value, GRAY)
    return color + labels.get(value, str(value)) + RESET

def get_nodes():
    inventory = read("inventory.json", {"nodes": []})
    states = read("data/status.json", {})
    details = read("data/details.json", {})
    now = time.time()

    fresh = 0 <= now - states.get("collected_at", 0) <= 120
    detail_fresh = 0 <= now - details.get("collected_at", 0) <= 120

    result = []
    for node in inventory.get("nodes", []):
        shop = str(node["id"])
        state = states.get("observations", {}).get(shop, {}) if fresh else {}
        extra = details.get("details", {}).get(shop, {}) if detail_fresh else {}

        result.append({
            "id": shop,
            "name": node.get("name", shop),
            "status": state.get("status", "unknown"),
            "vpn": state.get("vpn_status", "unknown"),
            "details": extra
        })
    return sorted(result, key=lambda n: n["id"])

def heading(text):
    print("\n" + CYAN + "=" * 70)
    print("  " + text)
    print("=" * 70 + RESET)

def list_nodes(nodes):
    print(f'{"SHOP":<12} {"NODE":<34} STATUS / VPN')
    print("-" * 70)
    for n in nodes:
        print(f'{n["id"]:<12} {n["name"][:34]:<34}')
        print(f'  Container: {status(n["status"])}'
              f'    VPN: {status(n["vpn"])}')

def summary(nodes):
    heading("Infrastructure Summary")
    print("Total nodes       :", len(nodes))
    print("Online            :", sum(n["status"] == "online" for n in nodes))
    print("Offline           :", sum(n["status"] == "offline" for n in nodes))
    print("VPN connected     :", sum(n["vpn"] == "up" for n in nodes))
    print("VPN disconnected  :", sum(n["vpn"] == "down" for n in nodes))
    print("VPN not configured:", sum(n["vpn"] == "not_configured" for n in nodes))
    print("Unknown status    :", sum(
        n["status"] == "unknown" or n["vpn"] == "unknown"
        for n in nodes
    ))

def shop_details(nodes):
    shop = input("Enter eight-digit shop code: ").strip()
    if not re.fullmatch(r"[0-9]{8}", shop):
        print("Invalid ID")
        return

    n = next((x for x in nodes if x["id"] == shop), None)
    if not n:
        print("Shop not found")
        return

    d = n["details"]
    vpn = d.get("vpn", {})
    heading("Shop " + shop)

    fields = [
        ("Container", d.get("container", n["name"])),
        ("Image", d.get("image", "Unknown")),
        ("Status", status(n["status"])),
        ("Started", d.get("started_at", "Unknown")),
        ("VPN", status(n["vpn"])),
        ("VPN software", vpn.get("software", "Unknown")),
        ("Loaded connections", vpn.get("loaded", "Unknown")),
        ("IPsec SAs", vpn.get("established", "Unknown")),
    ]

    for label, value in fields:
        print(f"{label:<22}: {value}")

    print("\nIP addresses:")
    for ip in d.get("ips", []):
        print(" ", ip.get("network", ""), ip.get("address", ""))

def refresh():
    if os.geteuid() != 0:
        print("Run as root to refresh collectors.")
        return

    for script in ("collect-nodes.py", "collect-details.py"):
        path = BASE / script
        if not path.exists():
            print("Missing:", path)
            continue

        try:
            result = subprocess.run(
                [str(BASE / ".venv/bin/python"), str(path)],
                timeout=120,
                check=False
            )
            print(script, "exit status:", result.returncode)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(script, "failed:", exc)


def extended_monitor():
    data = read("data/extended.json", {})
    age = int(time.time()) - data.get("collected_at", 0)

    heading("Extended Container Monitoring")

    if age < 0 or age > 120:
        print("Monitoring data missing or stale.")
        return

    nodes = data.get("nodes", {})

    for shop, node in sorted(nodes.items()):
        print("\\nShop:", shop)
        print("Container:", node.get("container", "Unknown"))

        resource = node.get("resources", {})
        print("CPU:", resource.get("cpu", "Unknown"))
        print("Memory:", resource.get("memory", "Unknown"))
        print("Memory %:", resource.get("memory_percent", "Unknown"))

        vpn = node.get("vpn", {})
        print("VPN:", status(vpn.get("status", "unknown")))
        print("Loaded connections:", vpn.get("loaded", "Unknown"))
        print("Established SAs:", vpn.get("established", "Unknown"))

        for conn in vpn.get("connections", []):
            print("  Connection:", conn)



def menu():
    while True:
        nodes = get_nodes()
        heading("MYKOS / MONITOR")
        print(f"  {len(nodes)} POS nodes")
        print("""
 1. Infrastructure summary
 2. All POS nodes
 3. VPN connections
 4. Shop details
 5. Offline nodes / VPN problems
 6. Refresh collectors
 7. Extended monitoring
 x. Return to MYKOS menu
""")
        choice = input("Select: ").strip().lower()

        if choice == "x":
            break
        elif choice == "1":
            summary(nodes)
        elif choice == "2":
            heading("POS Nodes")
            list_nodes(nodes)
        elif choice == "3":
            heading("VPN Status")
            for n in nodes:
                print(n["id"], status(n["vpn"]))
        elif choice == "4":
            shop_details(nodes)
        elif choice == "5":
            heading("Problems")
            problems = [
                n for n in nodes
                if n["status"] == "offline"
                or (n["status"] == "online" and n["vpn"] == "down")
            ]
            if problems:
                list_nodes(problems)
            else:
                print("No confirmed outages.")
            if any(n["status"] == "unknown" or n["vpn"] == "unknown"
                   for n in nodes):
                print("Some monitoring results are unknown or stale.")
        elif choice == "6":
            refresh()
        elif choice == "7":
            extended_monitor()
        else:
            continue

        input("\nPress Enter to continue...")

if __name__ == "__main__":
    try:
        menu()
    except (KeyboardInterrupt, EOFError):
        print()
