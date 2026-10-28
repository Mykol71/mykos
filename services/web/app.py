from generic_monitoring import details as generic_details, extended as generic_extended
from monitor_api import router as monitor_router
import hashlib, hmac, json, os, secrets, sqlite3, time
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from html import escape as html_escape
from pydantic import BaseModel
from mykos_config import BASE
DB = BASE/'data/auth.db'
# Use the toolkit root in a source checkout and the installed toolkit on prod.
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
ENVTYPE_FILE = (_SOURCE_ROOT / '.envtype' if (_SOURCE_ROOT / 'MENU').is_file()
                else Path('/usr/local/share/mykos/.envtype'))
app = FastAPI(root_path="/mykos", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(monitor_router)
from image_build_api import router as image_build_router
app.include_router(image_build_router)
from mykos_users import connect as db, is_admin
def digest(password,salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
def check(request):
    token=request.cookies.get('mykos_session','')
    with db() as c:
        row=c.execute('SELECT sessions.name FROM sessions JOIN users ON users.name=sessions.name WHERE token=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone() if token else None
    if not row: raise HTTPException(401,'Login required')
def origin(request):
    expected=os.getenv('MYKOS_PUBLIC_ORIGIN',str(request.base_url).rstrip('/'))
    if not request.headers.get('origin') or not hmac.compare_digest(request.headers['origin'],expected): raise HTTPException(403,'Invalid origin')
class Credentials(BaseModel):
    username: str
    password: str

from pane_notes import Pane, NoteUpdate, read as read_pane_note, save as save_pane_note, authorize as authorize_pane_notes
from pane_notes import read_personal as read_personal_note, save_personal as save_personal_note



from shared_id_notes import read as read_id_note, save as save_id_note

@app.get('/api/web/id-notes/{shop}', summary='Read shared customer notes')
def shared_id_note(shop: str, request: Request):
    check(request)
    return read_id_note(shop)

@app.put('/api/web/id-notes/{shop}', summary='Save shared customer notes (all signed-in users)')
def update_shared_id_note(shop: str, data: NoteUpdate, request: Request):
    identity = mykos_current_user(request)
    origin(request)
    return save_id_note(shop, data, identity['username'])

@app.get('/api/web/personal-notes', summary='Read personal notes (mgreen only; excluded from change requests)')
def personal_note(request: Request):
    authorize_pane_notes(request)
    return read_personal_note()


@app.put('/api/web/personal-notes', summary='Save personal notes (preserved through updates)')
def update_personal_note(data: NoteUpdate, request: Request):
    authorize_pane_notes(request, write=True)
    return save_personal_note(data)

@app.get('/image-setup/ubuntu.py', include_in_schema=False)
def ubuntu_image_setup():
    return FileResponse(Path(__file__).with_name('ubuntu-image-setup.py'), media_type='text/x-python',
                        filename='mykos-ubuntu-setup.py', headers={'Cache-Control':'no-store'})


@app.get('/api/web/notes/{pane}')
def pane_note(pane: Pane, request: Request):
    authorize_pane_notes(request)
    return read_pane_note(pane)


@app.put('/api/web/notes/{pane}')
def update_pane_note(pane: Pane, data: NoteUpdate, request: Request):
    authorize_pane_notes(request, write=True)
    return save_pane_note(pane, data)

@app.middleware('http')
async def headers(request,call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Cache-Control']='no-store'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; style-src 'self' 'unsafe-inline' https://unpkg.com; img-src 'self' data: https://basemaps.cartocdn.com https://*.basemaps.cartocdn.com https://tile.openstreetmap.org; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
    return response


# MYKOS HOST OS AND KERNEL VERSION
@app.get('/api/web/host-metrics')
def mykos_host_metrics(request: Request):
    check(request)
    from host_metrics import snapshot
    from host_history import latest
    return latest() or snapshot()


@app.get('/api/web/host-history')
def mykos_host_history(request: Request, since: float = 0):
    check(request)
    from host_history import read
    try: return read(since)
    except ValueError as error: raise HTTPException(400, str(error))


@app.get("/api/web/host-version")
def mykos_host_version(request: Request):
    check(request)

    import platform

    from host_metrics import internal_ip
    hostname = platform.node()
    os_name = "Linux"
    try:
        with open("/etc/os-release") as file:
            for line in file:
                if line.startswith("PRETTY_NAME="):
                    os_name = line.split("=", 1)[1].strip().strip('"')
                    break
    except OSError:
        pass

    from container_admin import invoke

    free = "Unknown"
    try:
        free = invoke("diskfree")["free"]
    except Exception:
        pass

    clock = time.localtime()
    short_time = f"{time.strftime('%b', clock)} {clock.tm_mday} · {clock.tm_hour % 12 or 12}:{clock.tm_min:02d} {'AM' if clock.tm_hour < 12 else 'PM'}"
    return {
        "hostname": hostname,
        "internal_ip": internal_ip(),
        "server_time": time.strftime("%m/%d/%Y %I:%M:%S %p %Z", clock),
        "server_time_display": short_time,
        "os": os_name,
        "kernel": platform.release(),
        "free": free
    }

@app.get('/')
def page():
    try:
        name = ENVTYPE_FILE.read_text().strip()
    except OSError:
        name = "MYKOS Control Center"
    name = name or "MYKOS Control Center"
    # The aesthetic sample uses Unicode full-width glyphs, not a downloadable font.
    display = ''.join(chr(ord(char) + 0xfee0) if '!' <= char <= '~'
                      else '\u3000' if char == ' ' else char for char in name)
    html = (BASE / "index.html").read_text()
    display_html = html_escape(display).replace('｜', '<span class="mykos-env-separator">｜</span>')
    return HTMLResponse(html.replace("__MYKOS_BRAND_DISPLAY__", display_html)
                        .replace("__MYKOS_BRAND_NAME__", html_escape(name)))

# Public supporting documentation, limited to the maintained operator guides.
_DOCS_ROOT = (_SOURCE_ROOT / 'docs' if (_SOURCE_ROOT / 'MENU').is_file()
              else Path('/usr/local/share/mykos/docs'))
_SUPPORTING_DOCS = {
    'INSTALL_SERVICES.md': 'Installation, updates, and recovery',
    'ARCHITECTURE.md': 'Architecture and components',
    'NETWORKING.md': 'Native LAN networking and Linux root terminals',
    'DESKTOPS.md': 'Windows, macOS and Ubuntu desktop templates',
    'RPM.md': 'RPM installation and custom repository',
    'BOOTSTRAP.md': 'Automatic HTTPS and RPM installation',
    'IMAGE_BUILDS.md': 'Unattended image builds',
    'BALLOONING.md': 'Guest memory ballooning and snapshot compatibility',
    'HOST_SIDE_REVIEW.md': 'Host-side monitoring and container-call review',
    'KEY_ISSUES.md': 'Deployment design and key issues',
    'IDENTITY.md': 'Users, roles, passwords, and API keys',
    'API.md': 'API usage',
    'SCRIPTS.md': 'Operator scripts',
    'VALIDATION.md': 'Validation and integration checks',
}


def _documentation_page(title, content):
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>' + html_escape(title) + '</title><style>'
        'body{margin:0;background:#0b1120;color:#e2e8f0;font:14px/1.6 monospace}'
        'main{max-width:1000px;margin:32px auto;padding:0 24px}'
        'a{color:#38bdf8}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
        'a:focus-visible{outline:2px solid #38bdf8;outline-offset:3px}'
        '</style></head><body><main><nav><a href="/mykos/">Login</a> | '
        '<a href="/mykos/api/v2/docs">API</a> | '
        '<a href="/mykos/docs">Supporting Docs</a></nav><h1>'
        + html_escape(title) + '</h1>' + content + '</main></body></html>')


@app.get('/docs', include_in_schema=False)
def supporting_docs():
    links = ''.join('<li><a href="/mykos/docs/' + name + '">'
                    + html_escape(title) + '</a></li>'
                    for name, title in _SUPPORTING_DOCS.items())
    return _documentation_page('Supporting Docs', '<ul>' + links + '</ul>')


@app.get('/docs/{name}', include_in_schema=False)
def supporting_document(name: str):
    if name not in _SUPPORTING_DOCS:
        raise HTTPException(404, 'Document not found')
    try:
        text = (_DOCS_ROOT / name).read_text()
    except OSError:
        raise HTTPException(404, 'Document not installed')
    return _documentation_page(_SUPPORTING_DOCS[name], '<pre>' + html_escape(text) + '</pre>')


@app.get("/api/web/current-user")
def mykos_current_user(request: Request):
    check(request)

    token = request.cookies.get("mykos_session", "")
    with db() as c:
        row = c.execute(
            "SELECT sessions.name, users.role FROM sessions JOIN users ON users.name=sessions.name WHERE token=? AND expires>?",
            (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))
        ).fetchone()

    return {"username": row[0] if row else "", "preview_all_panes": bool(row and row[1] in ("admin", "arch", "owner"))}

@app.post('/api/login')
def login(data:Credentials,request:Request):
    origin(request)
    with db() as c:
        row=c.execute('SELECT salt,hash FROM users WHERE name=?',(data.username,)).fetchone()
        valid=row and hmac.compare_digest(digest(data.password,row[0]),row[1])
        if not valid: raise HTTPException(401,'Invalid login')
        token=secrets.token_urlsafe(32)
        c.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),data.username,int(time.time())+28800))
    response=JSONResponse({'ok':True})
    response.set_cookie('mykos_session',token,httponly=True,samesite='strict',secure=os.getenv('MYKOS_SECURE_COOKIE','1')=='1',max_age=28800)
    return response
@app.post('/api/logout')
def logout(request:Request):
    origin(request)
    token=request.cookies.get('mykos_session','')
    with db() as c: c.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),))
    response=JSONResponse({'ok':True}); response.delete_cookie('mykos_session'); return response

@app.get('/api/nodes/{shop}/details')
def node_details(shop: str, request: Request):
    import re

    check(request)

    if not re.fullmatch(r"[0-9]{8}", shop):
        raise HTTPException(400, "Invalid ID")

    inventory = json.loads((BASE / 'inventory.json').read_text())

    if not any(str(n.get('id')) == shop
               for n in inventory.get('nodes', [])):
        raise HTTPException(404, "Shop not in inventory")

    details_file = BASE / 'data/details.json'

    if not details_file.exists():
        raise HTTPException(404, "Details not collected")

    payload = generic_details(json.loads(details_file.read_text()))
    details = payload.get('details', {}).get(shop)

    if not details:
        raise HTTPException(404, "Node details unavailable")

    age = int(time.time()) - payload.get('collected_at', 0)

    return {
        'shop': shop,
        'details': details,
        'collected_at': payload.get('collected_at'),
        'stale': age < 0 or age > 120
    }



@app.get('/api/web/host-uptime')
def host_uptime(request: Request):
    check(request)
    with open('/proc/uptime') as f:
        seconds = float(f.read().split()[0])
    return {'seconds': seconds}


@app.get('/api/web/extended')
def web_extended(request: Request):
    check(request)

    import time

    path = BASE / 'data/extended.json'

    if not path.exists():
        raise HTTPException(
            503, 'Extended monitoring unavailable'
        )

    payload = generic_extended(json.loads(path.read_text()))
    collected = payload.get('collected_at', 0)
    age = int(time.time()) - collected

    return {
        'collected_at': collected,
        'fresh': 0 <= age <= 120,
        'nodes': (
            payload.get('nodes', {})
            if 0 <= age <= 120 else {}
        )
    }

@app.get('/api/nodes')
def nodes(request:Request):
    check(request)
    inventory=json.loads((BASE/'inventory.json').read_text())
    statuses=json.loads((BASE/'data/status.json').read_text()) if (BASE/'data/status.json').exists() else {}
    observations=statuses.get('observations',{})
    fresh=int(time.time())-statuses.get('collected_at',0)<120
    for n in inventory['nodes']:
        observed=observations.get(str(n['id']),{}) if fresh else {}
        n['status']=observed.get('status','unknown')
        n['vpn']=observed.get('vpn_status','unknown')
    return inventory


# Container Management
from container_admin import router as container_admin_router
app.include_router(container_admin_router)

from mykos_admin_api import router as mykos_admin_router
app.include_router(mykos_admin_router)



# MYKOS admin-only interactive POS terminal; separate from command helper.
from fastapi import WebSocket, WebSocketDisconnect
import asyncio as _mykos_asyncio
import re as _mykos_re

@app.get('/api/terminal/permission')
def mykos_terminal_permission(request: Request, include_user: bool = False):
    check(request)
    raw = request.cookies.get('mykos_session', '')
    with db() as connection:
        row = connection.execute(
            'SELECT sessions.name FROM sessions JOIN users ON users.name=sessions.name WHERE token=? AND expires>?',
            (hashlib.sha256(raw.encode()).hexdigest(), int(time.time()))
        ).fetchone()
    result = {'allowed': bool(row and is_admin(row[0]))}
    if include_user: result['username'] = row[0] if row else ''
    return result

@app.get('/api/terminal/window/{shop}', include_in_schema=False)
def mykos_terminal_window(shop: str, request: Request, action: str = 'shell', family: str = 'linux', run_sshbbx: bool = False):
    permission = mykos_terminal_permission(request, include_user=True)
    if not permission['allowed']: raise HTTPException(403, 'Admin terminal required')
    if action not in ('shell', 'image-shell'): raise HTTPException(400, 'Invalid terminal window action')
    if action == 'image-shell':
        from image_build_api import prepared_os
        prepared_os(shop)
    elif shop != 'host' and not _mykos_re.fullmatch(r'[0-9]{8}', shop):
        raise HTTPException(400, 'Invalid ID')
    label = 'PowerShell' if family in ('windows','windows-server') else 'macOS shell' if family == 'macos' else 'root terminal'
    payload = {'shop': shop, 'action': action, 'title': label+' — '+shop,
               'username': permission['username'], 'run_sshbbx': bool(run_sshbbx)}
    config = json.dumps(payload).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    html = Path(__file__).with_name('terminal-window.html').read_text().replace('__MYKOS_TERMINAL_CONFIG__', config)
    return HTMLResponse(html)


@app.websocket('/api/terminal/{shop}')
async def mykos_terminal_websocket(ws: WebSocket, shop: str):
    # Validate Origin and session before WebSocket accept.
    expected = os.environ.get('MYKOS_PUBLIC_ORIGIN', 'https://mykol.com')
    action = ws.query_params.get('action', 'shell')
    image_shell = action == 'image-shell'
    from image_build_api import prepared_os
    try:
        if image_shell: prepared_os(shop)
    except HTTPException:
        await ws.close(code=1008)
        return
    if ws.headers.get('origin') != expected or (not image_shell and shop != 'host' and not _mykos_re.fullmatch(r'[0-9]{8}', shop)):
        await ws.close(code=1008)
        return
    token = ws.cookies.get('mykos_session', '')
    with db() as connection:
        row = connection.execute(
            'SELECT sessions.name FROM sessions JOIN users ON users.name=sessions.name WHERE token=? AND expires>?',
            (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))
        ).fetchone() if token else None
    if not row or not is_admin(row[0]):
        await ws.close(code=1008)
        return

    action = ws.query_params.get('action', 'shell')
    if action not in ('shell', 'add-vpn', 'remove-vpn', 'image-shell'):
        await ws.close(code=1008)
        return
    if action == 'shell':
        await mykos_terminal_bridge(ws, shop)
    else:
        await mykos_terminal_bridge(ws, shop, action=action)


async def mykos_terminal_bridge(ws: WebSocket, shop: str, action: str = 'shell'):
    """Common PTY transport; callers must authorize and validate the shop first."""
    try:
        reader, writer = await _mykos_asyncio.wait_for(
            _mykos_asyncio.open_unix_connection(os.environ.get('MYKOS_TERMINAL_SOCKET', '/run/mykos-terminal.sock')), 5)
        payload = {'shop': shop}
        if action != 'shell': payload['action'] = action
        writer.write((json.dumps(payload) + '\n').encode())
        await writer.drain()
        ready = await _mykos_asyncio.wait_for(reader.readline(), 20)
        if ready.startswith(b'MYKOS_TERMINAL_ERROR '):
            writer.close();await writer.wait_closed()
            await ws.accept();await ws.send_text(ready.decode('utf-8','replace').removeprefix('MYKOS_TERMINAL_ERROR '));await ws.close(code=1013);return
        if ready != b'MYKOS_TERMINAL_READY\n':
            writer.close()
            await writer.wait_closed()
            await ws.close(code=1011)
            return
    except (OSError, TimeoutError, ValueError):
        await ws.close(code=1011)
        return

    await ws.accept()

    async def to_shell():
        while True:
            packet = await ws.receive()
            if packet['type'] == 'websocket.disconnect':
                return
            chunk = packet.get('bytes')
            if chunk is None and packet.get('text') is not None:
                chunk = packet['text'].encode()
            if chunk:
                writer.write(chunk[:4096])
                await writer.drain()

    async def to_browser():
        while True:
            data = await reader.read(4096)
            if not data:
                return
            await ws.send_bytes(data)

    tasks = [_mykos_asyncio.create_task(to_shell()),
             _mykos_asyncio.create_task(to_browser())]
    try:
        await _mykos_asyncio.wait(tasks, return_when=_mykos_asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await _mykos_asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        try:
            await ws.close()
        except (RuntimeError, OSError):
            pass


# mykos-stage-start-v1
from fastapi import HTTPException as MYKOSStageHTTPException
from pydantic import BaseModel as MYKOSStageBaseModel
import re as mykos_stage_re
from container_admin import invoke as mykos_stage_invoke

class MYKOSStageRequest(MYKOSStageBaseModel):
    shop: str
    restore: bool = False
    os: Optional[str] = None

def mykos_stage_admin(request: Request):
    from app import origin
    check(request)
    if mykos_terminal_permission(request).get("allowed") is not True:
        raise MYKOSStageHTTPException(403, "Administrator login required")

def mykos_stage_valid(shop):
    if not mykos_stage_re.fullmatch(r"[0-9]{8}", shop):
        raise MYKOSStageHTTPException(400, "ID must be exactly eight digits")

@app.get("/api/web/stage-images")
def mykos_stage_images(request: Request):
    check(request)
    return mykos_stage_invoke("stage-images")

@app.get("/api/web/stage/{shop}/check")
def mykos_stage_check(shop: str, request: Request):
    mykos_stage_admin(request)
    mykos_stage_valid(shop)
    return mykos_stage_invoke("stage-check", shop)

@app.get("/api/web/stage/{shop}/status")
def mykos_stage_status(shop: str, request: Request):
    mykos_stage_admin(request)
    mykos_stage_valid(shop)
    return mykos_stage_invoke("stage-status", shop)

@app.post("/api/web/stage")
def mykos_stage_start(data: MYKOSStageRequest, request: Request):
    mykos_stage_admin(request)
    origin(request)
    mykos_stage_valid(data.shop)
    args = ["stage-start", data.shop, "yes" if data.restore else "no"]
    if data.os is not None: args.append(data.os)
    return mykos_stage_invoke(*args)

from container_admin import ContainerDelete

@app.delete("/api/web/containers/{shop}")
def mykos_delete_container(shop: str, data: ContainerDelete, request: Request):
    from container_admin import invoke
    mykos_stage_admin(request)
    origin(request)
    mykos_stage_valid(shop)
    if data.confirmation != shop:
        raise HTTPException(400, 'Confirm the exact container ID before deletion')
    return invoke('delete-pos', shop, data.confirmation)

@app.post("/api/web/containers/{shop}/start")
def mykos_start_container(shop: str, request: Request):
    from container_admin import invoke
    mykos_stage_admin(request)
    origin(request)
    mykos_stage_valid(shop)
    return invoke("start-pos", shop)

@app.post("/api/web/containers/{shop}/snapshot")
def mykos_snapshot_container(shop: str, request: Request):
    from container_admin import invoke
    mykos_stage_admin(request)
    origin(request)
    mykos_stage_valid(shop)
    return invoke("snapshot-pos", shop)

@app.get("/api/web/containers/{shop}/snapshots")
def mykos_container_snapshots(shop: str, request: Request):
    from container_admin import invoke
    check(request)
    mykos_stage_valid(shop)
    return invoke("list-snapshots", shop)

# MYKOS STOP BUTTON V1
@app.post("/api/web/containers/{shop}/stop")
def mykos_stop_container(shop: str, request: Request):
    from app import origin
    from container_admin import invoke
    from fastapi import HTTPException
    import re
    mykos_stage_admin(request)
    origin(request)
    if not re.fullmatch(r"[0-9]{8}", shop):
        raise HTTPException(400, "ID must be exactly eight digits")
    return invoke("stop-pos", shop)

# MYKOS API v2 - separate authenticated router
from api_v2 import router as mykos_api_v2_router
app.include_router(mykos_api_v2_router)


# MYKOS Infrastructure API v2



@app.get('/api/web/snapshots')
def mykos_snapshots(request: Request):
    check(request)
    from container_admin import invoke
    return invoke('list-snapshots')

@app.post('/api/web/containers/{shop}/snapshots/{identifier}/restore')
def mykos_restore_snapshot(shop: str, identifier: str, request: Request):
    mykos_stage_admin(request)
    origin(request)
    mykos_stage_valid(shop)
    import re
    if not re.fullmatch(r'[0-9]{10,}-[0-9a-f]{12}',identifier):raise HTTPException(400,'Invalid snapshot selection')
    from container_admin import invoke
    return invoke('restore-snapshot',shop,identifier)


@app.delete('/api/web/containers/{shop}/snapshots/{identifier}')
def mykos_delete_snapshot(shop: str, identifier: str, request: Request):
    mykos_stage_admin(request)
    origin(request)
    mykos_stage_valid(shop)
    import re
    if not re.fullmatch(r'[0-9]{10,}-[0-9a-f]{12}',identifier):raise HTTPException(400,'Invalid snapshot selection')
    from container_admin import invoke
    return invoke('delete-snapshot',shop,identifier)


from container_admin import HostPurge


@app.get('/api/web/system/purge')
def mykos_purge_plan(request: Request):
    mykos_stage_admin(request)
    from container_admin import invoke
    return invoke('purge-plan')


@app.post('/api/web/system/purge')
def mykos_purge_start(data: HostPurge, request: Request):
    mykos_stage_admin(request)
    origin(request)
    from container_admin import invoke
    return invoke('purge-start', data.confirmation)


@app.get('/api/web/system/purge/status')
def mykos_purge_status(request: Request):
    mykos_stage_admin(request)
    from container_admin import invoke
    return invoke('purge-status')


from desktop_proxy import routes as desktop_proxy_routes
app.include_router(desktop_proxy_routes())
