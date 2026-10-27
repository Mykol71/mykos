import json
import os
import subprocess

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
router = APIRouter()

class HostPurge(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation: str = Field(min_length=7, max_length=270, description='Exact PURGE hostname phrase returned by the purge plan')


class ContainerDelete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation: str = Field(pattern=r'^[0-9]{8}$', description='Repeat the path ID to confirm permanent deletion of the container and its private saved data.')


class HostReboot(BaseModel):
    hostname: str


def desktop_info(shop, request):
    authorized(request)
    from app import mykos_terminal_permission
    if not mykos_terminal_permission(request).get('allowed'):
        raise HTTPException(403, 'Administrator login required')
    if len(shop) != 8 or not shop.isascii() or not shop.isdigit():
        raise HTTPException(400, 'Invalid ID')
    return invoke('desktop-info', shop)


def rdp_response(data):
    import ipaddress
    from fastapi.responses import Response
    host = str(ipaddress.IPv4Address(data['host']))
    port = int(data['desktop_port'])
    if data['protocol'] != 'rdp' or data.get('login_supported') is False or not 1024 <= port <= 65535:
        raise HTTPException(400, 'Not an RDP desktop')
    return Response('full address:s:%s:%s\r\nprompt for credentials:i:1\r\n' % (host, port),
                    media_type='application/x-rdp', headers={'Content-Disposition':'attachment; filename="mykos-desktop.rdp"','Cache-Control':'no-store'})


@router.get('/api/web/desktop/{shop}')
def desktop_connection(shop: str, request: Request):
    return desktop_info(shop, request)


@router.get('/api/web/desktop/{shop}/rdp')
def desktop_rdp(shop: str, request: Request):
    return rdp_response(desktop_info(shop, request))


@router.get('/api/web/host/{action}')
def host_diagnostic(action: str, request: Request):
    authorized(request)
    if action not in ('logs', 'top', 'ipsec'):
        raise HTTPException(404, 'Unknown host action')
    return invoke('host-' + action)


@router.post('/api/web/host/reboot')
def reboot_host(data: HostReboot, request: Request):
    authorized(request, modify=True)
    from app import mykos_terminal_permission
    if not mykos_terminal_permission(request).get('allowed'):
        raise HTTPException(403, 'Administrator login required')
    return invoke('host-reboot', data.hostname)

HELPER = "/usr/local/sbin/mykos-container-helper"

def authorized(request, modify=False):
    from app import check, origin
    check(request)
    if modify:
        origin(request)

def invoke(*args):
    import socket

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(170)
            client.connect(os.environ.get("MYKOS_CONTAINER_SOCKET", "/run/mykos-container-admin.sock"))
            client.sendall(
                (json.dumps({"args": list(args)}) + "\n").encode()
            )

            chunks = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(map(len, chunks)) > 1048576:
                    raise ValueError("Response too large")

        response = json.loads(b"".join(chunks))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            503, "Container service unavailable: " + str(exc)
        )

    if not response.get("ok"):
        raise HTTPException(
            500, response.get("error", "Container operation failed")
        )

    return response["result"]

@router.get("/api/admin/containers")
def list_containers(request: Request):
    authorized(request)
    return {"containers": invoke("list")}

@router.get("/api/admin/containers/{shop}/logs")
def container_logs(shop: str, request: Request):
    authorized(request)
    if len(shop) != 8 or not shop.isascii() or not shop.isdigit():
        raise HTTPException(400, "Invalid ID")
    return invoke("logs", shop)


@router.get("/api/admin/containers/{shop}/top")
def container_top(shop: str, request: Request):
    authorized(request)
    if len(shop) != 8 or not shop.isascii() or not shop.isdigit():
        raise HTTPException(400, "Invalid ID")
    return invoke("top", shop)



@router.get("/api/admin/containers/{shop}/checkit")
def container_checkit(shop: str, request: Request):
    from app import mykos_terminal_permission
    if not mykos_terminal_permission(request).get("allowed"):
        raise HTTPException(403, "Administrator login required")
    if len(shop) != 8 or not shop.isascii() or not shop.isdigit():
        raise HTTPException(400, "Invalid ID")
    return invoke("checkit", shop)


@router.get("/api/admin/containers/{shop}/ipsec")
def container_ipsec(shop: str, request: Request):
    authorized(request)
    if len(shop) != 8 or not shop.isascii() or not shop.isdigit():
        raise HTTPException(400, "Invalid ID")
    return invoke("ipsec", shop)
