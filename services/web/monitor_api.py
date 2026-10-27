from generic_monitoring import details as generic_details, extended as generic_extended
import json
import hmac
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from mykos_config import BASE
TOKEN_FILE = BASE / "data/api-token"

router = APIRouter(prefix="/api/monitor")


def authenticate(request):
    token = request.headers.get("Authorization", "")
    if not token.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")

    try:
        expected = TOKEN_FILE.read_text().strip()
    except OSError:
        raise HTTPException(503, "API token unavailable")

    supplied = token[7:]

    if not expected or not hmac.compare_digest(
        supplied, expected
    ):
        raise HTTPException(401, "Invalid API token")


def read_json(filename, default):
    try:
        return json.loads((BASE / filename).read_text())
    except (OSError, ValueError):
        return default


def snapshot():
    inventory = read_json("inventory.json", {"nodes": []})
    states = read_json("data/status.json", {})
    details = generic_details(read_json("data/details.json", {}))

    now = int(time.time())

    status_time = states.get("collected_at", 0)
    details_time = details.get("collected_at", 0)

    status_fresh = (
        isinstance(status_time, (int, float))
        and 0 <= now - status_time <= 120
    )
    details_fresh = (
        isinstance(details_time, (int, float))
        and 0 <= now - details_time <= 120
    )

    nodes = []

    for item in inventory.get("nodes", []):
        shop = str(item["id"])

        observation = (
            states.get("observations", {}).get(shop, {})
            if status_fresh else {}
        )
        extra = (
            details.get("details", {}).get(shop, {})
            if details_fresh else {}
        )

        nodes.append({
            "shop": shop,
            "name": item.get("name", shop),
            "hub": item.get("hub", "Unassigned"),
            "latitude": item.get("lat"),
            "longitude": item.get("lon"),
            "status": observation.get("status", "unknown"),
            "vpn_status": observation.get(
                "vpn_status", "unknown"
            ),
            "details": extra
        })

    nodes.sort(key=lambda n: n["shop"])

    return {
        "collected_at": status_time,
        "status_fresh": status_fresh,
        "details_fresh": details_fresh,
        "nodes": nodes
    }


@router.get("/summary")
def summary(request: Request):
    authenticate(request)
    data = snapshot()
    nodes = data["nodes"]

    return {
        "collected_at": data["collected_at"],
        "status_fresh": data["status_fresh"],
        "total": len(nodes),
        "online": sum(
            n["status"] == "online" for n in nodes
        ),
        "offline": sum(
            n["status"] == "offline" for n in nodes
        ),
        "vpn_up": sum(
            n["vpn_status"] == "up" for n in nodes
        ),
        "vpn_down": sum(
            n["vpn_status"] == "down" for n in nodes
        ),
        "vpn_not_configured": sum(
            n["vpn_status"] == "not_configured"
            for n in nodes
        ),
        "unknown": sum(
            n["status"] == "unknown"
            or n["vpn_status"] == "unknown"
            for n in nodes
        )
    }


@router.get("/nodes")
def all_nodes(request: Request):
    authenticate(request)
    return snapshot()


@router.get("/nodes/{shop}")
def one_node(shop: str, request: Request):
    authenticate(request)

    if len(shop) != 8 or not shop.isascii() or not shop.isdigit():
        raise HTTPException(400, "Invalid ID")

    data = snapshot()

    for node in data["nodes"]:
        if node["shop"] == shop:
            return {
                "collected_at": data["collected_at"],
                "status_fresh": data["status_fresh"],
                "details_fresh": data["details_fresh"],
                "node": node
            }

    raise HTTPException(404, "Shop not found")


def extended_snapshot():
    from pathlib import Path
    import json
    import time

    path = Path("/opt/mykos-web/data/extended.json")

    if not path.exists():
        return {
            "collected_at": 0,
            "fresh": False,
            "nodes": {}
        }

    payload = generic_extended(json.loads(path.read_text()))
    age = int(time.time()) - payload.get("collected_at", 0)

    return {
        "collected_at": payload.get("collected_at", 0),
        "fresh": 0 <= age <= 120,
        "nodes": payload.get("nodes", {}) if 0 <= age <= 120 else {}
    }


@router.get("/extended")
def extended_all(request: Request):
    authenticate(request)
    return extended_snapshot()


@router.get("/extended/{shop}")
def extended_one(shop: str, request: Request):
    authenticate(request)

    import re
    if not re.fullmatch(r"[0-9]{8}", shop):
        raise HTTPException(400, "Invalid ID")

    data = extended_snapshot()

    if not data["fresh"]:
        raise HTTPException(503, "Extended monitoring data is stale")

    node = data["nodes"].get(shop)

    if node is None:
        raise HTTPException(404, "Shop not found")

    return {
        "shop": shop,
        "collected_at": data["collected_at"],
        "node": node
    }
