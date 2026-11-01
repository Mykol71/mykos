"""Administrator-only local broker for reciprocal peer dashboards."""
import asyncio
import contextlib
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
import mykos_peers as peers

router = APIRouter()


def admin(request, modify=False):
    from app import mykos_stage_admin, origin
    if peers.principal(request):
        raise HTTPException(403, 'Peer management requires a local browser login')
    mykos_stage_admin(request)
    if modify:
        origin(request)


def actor(request):
    raw = request.cookies.get('mykos_session', '')
    return hashlib.sha256(raw.encode()).hexdigest()


class InvitationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    local_ip: str = Field(max_length=15)
    remote_ip: str = Field(max_length=15)


class AcceptRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    invitation: str = Field(max_length=32768)


@router.get('/peer-ui.js', include_in_schema=False)
def javascript():
    return Response(Path(__file__).with_name('peer-ui.js').read_text(), media_type='text/javascript')


@router.get('/api/web/peers')
def listing(request: Request):
    admin(request)
    return peers.listing()


@router.post('/api/web/peers/invite')
def invite(data: InvitationRequest, request: Request):
    admin(request, True)
    try:
        return peers.invite(data.local_ip, data.remote_ip)
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'A pairing already exists for this host; remove it before pairing again')


@router.post('/api/web/peers/accept')
def accept(data: AcceptRequest, request: Request):
    admin(request, True)
    try:
        return peers.accept(data.invitation)
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'A pairing already exists for this host; remove it before pairing again')


@router.post('/api/peers/confirm')
def confirm(data: dict, request: Request):
    principal = peers.principal(request)
    if not principal:
        raise HTTPException(401, 'Peer authentication required')
    identity = peers.validate_identity(data)
    if identity['node'] != principal['node'] or identity['node'] == peers.node_id():
        raise HTTPException(403, 'Invalid reciprocal identity')
    with peers.connect() as conn:
        row = conn.execute('SELECT * FROM peers WHERE id=?', (principal['id'],)).fetchone()
        if not row or identity['ip'] != row['ip']:
            raise HTTPException(403, 'Unexpected peer host address')
        if row['state'] == 'active':
            if any(row[key] != identity[key] for key in ('node', 'name', 'origin', 'certificate')):
                raise HTTPException(409, 'Pairing is already bound to another identity')
        elif row['state'] == 'pending' and row['expires'] > time.time():
            conn.execute("UPDATE peers SET node=?,name=?,origin=?,certificate=?,state='active',expires=0 WHERE id=?",
                         (identity['node'], identity['name'], identity['origin'], identity['certificate'], row['id']))
        else:
            raise HTTPException(403, 'Invitation expired or revoked')
    return {'node': peers.node_id(), 'state': 'active'}


def revoke(identifier):
    # Revoke API trust before taking down the transport, even if teardown fails.
    with peers.connect() as conn:
        conn.execute("UPDATE peers SET state='revoked',incoming=NULL,outgoing=NULL WHERE id=?", (identifier,))
    return peers.vpn('remove', identifier)


@router.post('/api/peers/disconnect')
def disconnect(request: Request):
    principal = peers.principal(request)
    if not principal:
        raise HTTPException(401, 'Peer authentication required')
    # Delay tunnel removal until the encrypted response can leave this host.
    # Local revocation is immediate; the normal cleanup worker removes the VPN.
    with peers.connect() as conn:
        conn.execute("UPDATE peers SET state='revoked',incoming=NULL,outgoing=NULL WHERE id=?", (principal['id'],))
    return {'state': 'revoked'}


@router.delete('/api/web/peers/{identifier}')
def remove(identifier: str, request: Request):
    admin(request, True)
    peer = peers.get(identifier, False)
    remote_removed = False
    if peer['state'] == 'active':
        try:
            status, _, _ = peers.exchange(peer, 'POST', '/api/peers/disconnect', confirming=True)
            remote_removed = status == 200
        except HTTPException:
            pass
    result = revoke(identifier)
    return {**result, 'remote_revoked': remote_removed}


def bootstrap(identifier):
    # Every request is routed through the selected node. An offline node never
    # falls back to the local host, even when the two nodes have identical IDs.
    prefix = '/mykos/api/web/peers/' + identifier + '/proxy'
    return '<script>' + Path(__file__).with_name('peer-bootstrap.js').read_text().replace('__MYKOS_PEER_PREFIX__', json.dumps(prefix)) + '</script>'


@router.get('/api/web/peers/{identifier}/view', include_in_schema=False)
def view(identifier: str, request: Request, full: bool = False):
    admin(request)
    peers.get(identifier)
    from app import page
    html = page().body.decode()
    style = '''<style>
html,body { background:transparent!important; margin:0!important; padding:0!important; }
header, body > aside, .mykos-notes-button { display:none!important; }
main { max-width:none!important; padding:0!important; margin:0!important; }
main > :not(#mykos-host-pane):not(#mykos-host-hardware) { display:none!important; }
#mykos-host-pane { margin:0!important; }
</style>'''
    if full:
        style = style.replace('main > :not(#mykos-host-pane):not(#mykos-host-hardware) { display:none!important; }', '')
    return HTMLResponse(html.replace('<head>', '<head>' + bootstrap(identifier) + style, 1))


def rewrite_response(identifier, path, content_type, body):
    prefix = '/mykos/api/web/peers/' + identifier + '/proxy'
    if 'text/html' in content_type:
        html = body.decode('utf-8')
        if '<head>' in html:
            html = html.replace('<head>', '<head>' + bootstrap(identifier), 1)
        else:
            html = bootstrap(identifier) + html
        return html.encode()
    if 'application/json' in content_type:
        try:
            data = json.loads(body)
        except ValueError:
            raise HTTPException(502, 'Invalid peer JSON response')
        if path.startswith('console/') and path.endswith('/mandatory.json') and isinstance(data, dict):
            origin = urlsplit(peers.public_origin())
            data.update(host=origin.hostname, port='443', encrypt=True)
            if isinstance(data.get('path'), str) and data['path'].startswith('/mykos/'):
                data['path'] = prefix + data['path'][6:]
        return json.dumps(data).encode()
    return body


@router.api_route('/api/web/peers/{identifier}/proxy/{path:path}', methods=['GET','HEAD','POST','PUT','PATCH','DELETE'], include_in_schema=False)
async def proxy(identifier: str, path: str, request: Request):
    await run_in_threadpool(admin, request, request.method not in ('GET', 'HEAD'))
    if not peers.allowed_path('/' + path):
        raise HTTPException(403, 'This endpoint is not shared with peers')
    peer = await run_in_threadpool(peers.get, identifier)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > peers.MAX_BODY:
            raise HTTPException(413, 'Peer request too large')
    target = '/' + path + ('?' + request.url.query if request.url.query else '')
    status, content_type, result = await run_in_threadpool(peers.exchange, peer, request.method, target, bytes(body), request.headers.get('content-type', 'application/json'), actor(request))
    result = rewrite_response(identifier, path, content_type, result)
    return Response(result, status_code=status, headers={'Content-Type': content_type, 'Cache-Control': 'no-store'})


@router.websocket('/api/web/peers/{identifier}/proxy/{path:path}')
async def websocket_proxy(ws: WebSocket, identifier: str, path: str):
    from websockets.asyncio.client import connect
    try:
        from app import origin
        origin(ws)
        await run_in_threadpool(admin, ws)
        if not peers.allowed_path('/' + path) or not (path.startswith('api/terminal/') or path.startswith('console/') or __import__('re').fullmatch(r'api/web/snapshot-preview/[0-9a-f]{32}/terminal', path)):
            raise HTTPException(403, 'Invalid peer WebSocket')
        peer = await run_in_threadpool(peers.get, identifier)
        if not (await run_in_threadpool(peers.vpn, 'status', identifier)).get('connected'):
            raise HTTPException(503, 'Peer VPN is offline')
        context, pin = peers.certificate_context(peer['certificate'])
        raw = await run_in_threadpool(socket.create_connection, (peer['ip'], 443), 15)
        raw.setblocking(False)
        protocols = [x for x in ws.scope.get('subprotocols', []) if x in ('binary', 'base64')]
        target = peer['origin'].replace('https://', 'wss://', 1) + '/mykos/' + path
        if ws.url.query:
            target += '?' + ws.url.query
        machine_headers = peers.headers(peer, actor(ws))
        machine_headers.pop('Origin')
        try:
            async with connect(target, sock=raw, ssl=context, proxy=None, origin=peer['origin'],
                               additional_headers=machine_headers, subprotocols=protocols or None,
                               max_size=peers.MAX_BODY, compression=None, open_timeout=20, close_timeout=3) as upstream:
                tls = upstream.transport.get_extra_info('ssl_object')
                if not tls or hashlib.sha256(tls.getpeercert(binary_form=True)).digest() != pin:
                    raise HTTPException(502, 'Peer certificate changed')
                await ws.accept(subprotocol=upstream.subprotocol)
                async def inbound():
                    while True:
                        message = await ws.receive()
                        if message['type'] == 'websocket.disconnect': return
                        data = message.get('bytes') if message.get('bytes') is not None else message.get('text')
                        if data is not None: await upstream.send(data)
                async def outbound():
                    async for data in upstream:
                        if isinstance(data, bytes): await ws.send_bytes(data)
                        else: await ws.send_text(data)
                async def watch():
                    while True:
                        await asyncio.sleep(15)
                        await run_in_threadpool(admin, ws)
                        current = await run_in_threadpool(peers.get, identifier)
                        status, _, _ = await run_in_threadpool(peers.exchange, current, 'GET', '/api/terminal/permission', b'', 'application/json', actor(ws))
                        if status != 200: raise HTTPException(403, 'Peer session revoked')
                tasks = [asyncio.create_task(fn()) for fn in (inbound, outbound, watch)]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done: task.result()
                finally:
                    for task in tasks: task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            raw.close()
    except asyncio.CancelledError:
        raise
    except Exception:
        with contextlib.suppress(Exception):
            await ws.close(code=1008)


def cleanup_expired():
    if not peers.DB.exists(): return
    with peers.connect() as conn:
        rows = conn.execute("SELECT id,state FROM peers WHERE state='revoked' OR (state='pending' AND expires<=?)", (int(time.time()),)).fetchall()
    for row in rows:
        try:
            peers.vpn('remove', row['id'])
            with peers.connect() as conn:
                conn.execute("DELETE FROM peers WHERE id=? AND (state='revoked' OR (state='pending' AND expires<=?))", (row['id'], int(time.time())))
        except Exception:
            # Keep the row for a later teardown retry; do not forget owned resources.
            pass
