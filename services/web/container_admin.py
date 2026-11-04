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
            409 if str(response.get("error", "")).startswith("Container limit reached") else 500,
            response.get("error", "Container operation failed")
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


@router.post('/api/web/containers/{shop}/vpn/{action}')
def container_vpn_control(shop: str, action: str, request: Request):
    authorized(request,modify=True)
    from app import mykos_terminal_permission
    if not mykos_terminal_permission(request).get('allowed'):raise HTTPException(403,'Administrator login required')
    if len(shop)!=8 or not shop.isascii() or not shop.isdigit():raise HTTPException(400,'Invalid ID')
    if action not in ('start','stop'):raise HTTPException(404,'Unknown VPN action')
    return invoke(action+'-vpn',shop)


STARTUP_STATUS_DIR = __import__('pathlib').Path('/run/mykos-startup-status')


def startup_status_snapshot():
    import re,stat
    result={}
    try:
        root=STARTUP_STATUS_DIR.lstat()
        if not stat.S_ISDIR(root.st_mode) or root.st_uid!=0 or root.st_mode&0o022:return result
        for path in STARTUP_STATUS_DIR.iterdir():
            if not re.fullmatch(r'(container|vpn)\.[0-9]{8}',path.name):continue
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or info.st_size>7:continue
            state=path.read_text()
            if state in ('up','down','deleted'):result[path.name]=((info.st_ino,info.st_mtime_ns),state)
    except FileNotFoundError:pass
    return result


@router.get('/api/web/lifecycle-events')
async def startup_events(request: Request):
    authorized(request)
    from fastapi.responses import StreamingResponse
    import asyncio,time,re
    async def events():
        previous=startup_status_snapshot();checked=time.monotonic();heartbeat=checked;started=checked
        cursor=request.headers.get('last-event-id','')
        if re.fullmatch(r'[0-9]+:(container|vpn):[0-9]{8}',cursor):
            previous={name:value for name,value in previous.items() if value[0][1]<=int(cursor.split(':')[0])}
        yield 'retry: 1000\n: connected\n\n'
        while time.monotonic()-started<20 and not await request.is_disconnected():
            now=time.monotonic()
            if now-checked>=15:
                try:authorized(request)
                except Exception:return
                checked=now
            current=startup_status_snapshot()
            for name,(token,state) in sorted(current.items(),key=lambda item:item[1][0][1]):
                if previous.get(name)==(token,state):continue
                kind,shop=name.split('.')
                yield 'event: '+kind+'\nid: '+str(token[1])+':'+kind+':'+shop+'\ndata: '+state+'\n\n'
            previous=current
            if now-heartbeat>=15:yield ': keepalive\n\n';heartbeat=now
            await asyncio.sleep(.5)
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
