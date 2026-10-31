"""Authenticated same-origin noVNC HTTP and WebSocket transport."""
import asyncio
import contextlib
import http.client
from ipaddress import IPv4Address
import json
import os
import re
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from websockets.asyncio.client import connect

LIMIT = 16 * 1024 * 1024
AUTH_INTERVAL = 15
SELECTION = re.compile(r'(?:preview-[0-9a-f]{32}|[0-9]{8}|(?:win|wns|mac|ubuntu)[A-Za-z0-9.]+)\Z')


def authorize(connection, api=False):
    if api:
        from api_v2 import administrator
        administrator(connection)
    else:
        from app import mykos_terminal_permission
        if not mykos_terminal_permission(connection).get('allowed'):
            raise HTTPException(403, 'Administrator login required')


def target(selection, preview_owner=None):
    if not SELECTION.fullmatch(selection): raise HTTPException(400, 'Invalid console selection')
    from container_admin import invoke
    if selection.startswith('preview-'):
        if not preview_owner:raise HTTPException(403,'Preview session required')
        value=invoke('snapshot-preview-target',selection[8:],preview_owner)
    else:value = invoke('console-target', selection)
    host = IPv4Address(value['host'])
    port = value['port']
    if host.is_unspecified or host.is_multicast or type(port) is not int or not 18006 <= port <= 19005:
        raise HTTPException(502, 'Invalid managed console endpoint')
    return str(host), port


def origin():
    value = urlsplit(os.environ.get('MYKOS_PUBLIC_ORIGIN', 'https://mykol.com'))
    if value.scheme not in ('http', 'https') or not value.hostname:
        raise HTTPException(503, 'Public origin is not configured')
    return value


def fetch(host, port, path, query, method):
    connection = http.client.HTTPConnection(host, port, timeout=15)
    try:
        # Caller chooses only a path on a verified local container console.
        # Neither MYKOS cookies/API keys nor client-supplied upstream headers travel here.
        connection.request(method, '/' + quote(path, safe='/._-') + ('?' + query if query else ''))
        response = connection.getresponse()
        data = response.read(LIMIT + 1)
        if len(data) > LIMIT: raise HTTPException(502, 'Console asset too large')
        if 300 <= response.status < 400: raise HTTPException(502, 'Unexpected console redirect')
        return response.status, response.getheader('Content-Type', 'application/octet-stream'), data
    except (OSError, http.client.HTTPException) as error:
        raise HTTPException(502, 'Managed console is unavailable') from error
    finally:
        connection.close()


def http_proxy(request, selection, path, api):
    authorize(request, api)
    if '..' in path.split('/') or '\\' in path or '\x00' in path:
        raise HTTPException(400, 'Invalid console path')
    from snapshot_preview_api import owner
    host, port = target(selection,owner(request)) if selection.startswith('preview-') else target(selection)
    status, content_type, body = fetch(host, port, path, request.url.query, request.method)
    if path == 'mandatory.json' and status == 200:
        try: settings = json.loads(body)
        except (ValueError, TypeError) as error: raise HTTPException(502, 'Invalid console settings') from error
        if not isinstance(settings, dict): raise HTTPException(502, 'Invalid console settings')
        public = origin()
        prefix = '/mykos/api/v2/console/' if api else '/mykos/console/'
        # Force the public proxy even when noVNC remembers an old LAN/dev setting.
        settings.update(host=public.hostname, port=str(public.port or (443 if public.scheme == 'https' else 80)),
                        encrypt=public.scheme == 'https', path=prefix + selection + '/websockify', autoconnect=True)
        body = json.dumps(settings).encode()
    return Response(body, status_code=status, headers={'Content-Type':content_type, 'Cache-Control':'no-store'})


async def websocket_proxy(ws, selection, path, api):
    if path != 'websockify':
        await ws.close(code=1008); return
    try:
        if not api and ws.headers.get('origin') != os.environ.get('MYKOS_PUBLIC_ORIGIN', 'https://mykol.com'):
            raise HTTPException(403, 'Invalid origin')
        await asyncio.to_thread(authorize, ws, api)
        from snapshot_preview_api import owner
        preview_owner=owner(ws) if selection.startswith('preview-') else None
        if selection.startswith('preview-'):
            host, port = await asyncio.to_thread(target, selection,preview_owner)
        else:host, port = await asyncio.to_thread(target, selection)
    except (HTTPException, OSError, ValueError):
        await ws.close(code=1008); return
    offered = [p.strip() for p in ws.headers.get('sec-websocket-protocol', '').split(',') if p.strip() in ('binary', 'base64')]
    try:
        async with connect('ws://%s:%s/websockify' % (host, port), proxy=None,
                           subprotocols=offered or None, max_size=LIMIT, compression=None,
                           open_timeout=15, close_timeout=5) as upstream:
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
            async def authorization_watch():
                while True:
                    await asyncio.sleep(AUTH_INTERVAL)
                    await asyncio.to_thread(authorize, ws, api)
                    if preview_owner:
                        from container_admin import invoke
                        await asyncio.to_thread(invoke,'snapshot-preview-status',selection[8:],preview_owner)
            tasks = [asyncio.create_task(f()) for f in (inbound, outbound, authorization_watch)]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done: task.result()
            finally:
                for task in tasks: task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        # The ASGI server cancels a connection task when its client leaves.
        # Child transports have already been cancelled by the cleanup above.
        return
    except (Exception, WebSocketDisconnect):
        with contextlib.suppress(RuntimeError, OSError): await ws.close(code=1011)
    else:
        with contextlib.suppress(RuntimeError, OSError): await ws.close(code=1000)


def routes(api=False):
    router = APIRouter(prefix='/console', tags=['Desktop consoles'])
    @router.get('/{selection}/{path:path}', summary='Authenticated browser console and assets')
    def console_http(selection: str, path: str, request: Request):
        return http_proxy(request, selection, path, api)
    @router.websocket('/{selection}/{path:path}')
    async def console_ws(selection: str, path: str, ws: WebSocket):
        await websocket_proxy(ws, selection, path, api)
    return router
