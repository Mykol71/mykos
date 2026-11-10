"""Local full-admin copy controls and narrowly authenticated peer receivers."""
import contextlib
import asyncio
import json
import os
from pathlib import Path
import re
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

import mykos_peers as peers
from container_admin import invoke
from peers_api import admin, actor


def local_admin(request, modify=False):
    admin(request, modify)
    from app import mykos_terminal_permission
    from mykos_users import is_admin
    permission = mykos_terminal_permission(request, include_user=True)
    if not is_admin(permission.get("username", "")):
        raise HTTPException(403, "Full administrator login required")

router = APIRouter()
INBOX = Path('/opt/mykos-web/data/instance-transfer-inbox')
CHUNK = 4 * 1024 * 1024


class CopySelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    peer: str = Field(pattern=r'^[0-9a-f]{32}$')
    destination: str = Field(pattern=r'^[0-9]{8}$')


class CopyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan: dict


def peer_identity(request):
    principal = peers.principal(request)
    if not principal:
        raise HTTPException(401, 'Authenticated peer required')
    # The middleware checks key, node identity, active trust, VPN and allowlist.
    return principal['id']


def token(value):
    if not re.fullmatch(r'[0-9a-f]{32}', value):
        raise HTTPException(400, 'Invalid transfer identifier')
    return value


@router.get('/instance-copy.js', include_in_schema=False)
def javascript():
    return Response(Path(__file__).with_name('instance-copy.js').read_text(), media_type='text/javascript', headers={'Cache-Control': 'no-cache'})


@router.get('/api/web/instance-transfers/permission')
def permission(request: Request):
    try:
        local_admin(request)
        return {'allowed': True}
    except HTTPException as error:
        if error.status_code not in (401, 403):raise
        return {'allowed': False}


@router.post('/api/web/instance-transfers/{code}/plan')
def plan(code: str, selection: CopySelection, request: Request):
    local_admin(request, True)
    if not re.fullmatch(r'[0-9]{8}', code):
        raise HTTPException(400, 'Invalid instance ID')
    peers.get(selection.peer)
    return invoke('instance-copy-plan', code, selection.destination, selection.peer)


@router.get('/api/web/instance-transfers')
def history(request: Request):
    local_admin(request)
    return {'transfers': invoke('instance-copy-list')}


@router.post('/api/web/instance-transfers')
def create(data: CopyRequest, request: Request):
    local_admin(request, True)
    if len(json.dumps(data.plan)) > 32768:
        raise HTTPException(413, 'Copy plan too large')
    return invoke('instance-copy-create', json.dumps(data.plan), actor(request))


@router.get('/api/web/instance-transfers/{identifier}')
def status(identifier: str, request: Request):
    local_admin(request)
    return invoke('instance-copy-local-status', token(identifier))


@router.get('/api/peers/instance-transfers/capabilities/{code}')
def capabilities(code: str, request: Request):
    peer_identity(request)
    if not re.fullmatch(r'[0-9]{8}', code):
        raise HTTPException(400, 'Invalid destination ID')
    return invoke('instance-copy-capabilities', code)


@router.post('/api/peers/instance-transfers/begin')
async def begin(request: Request):
    peer = peer_identity(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 32768:
            raise HTTPException(413, 'Transfer manifest too large')
    try:
        value = json.loads(body)
    except ValueError:
        raise HTTPException(400, 'Invalid transfer manifest')
    return await run_in_threadpool(invoke, 'instance-copy-begin', peer, json.dumps(value))


@router.put('/api/peers/instance-transfers/{identifier}/chunk/{offset}/{checksum}')
async def chunk(identifier: str, offset: int, checksum: str, request: Request):
    peer = peer_identity(request); token(identifier)
    if offset < 0 or not re.fullmatch(r'[0-9a-f]{64}', checksum):
        raise HTTPException(400, 'Invalid transfer chunk')
    # Reject unknown/unowned transfers before creating any web-owned scratch.
    await run_in_threadpool(invoke, 'instance-copy-status', identifier, peer)
    INBOX.mkdir(mode=0o700, exist_ok=True)
    if INBOX.is_symlink() or INBOX.stat().st_mode & 0o077 or INBOX.stat().st_uid != os.geteuid():
        raise HTTPException(503, 'Unsafe transfer inbox')
    upload = secrets.token_hex(16); path = INBOX / (upload + '.chunk')
    try:
        with path.open('xb') as handle:
            os.fchmod(handle.fileno(), 0o600); size = 0
            try:
                async with asyncio.timeout(60):
                    async for payload in request.stream():
                        size += len(payload)
                        if size > CHUNK:
                            raise HTTPException(413, 'Transfer chunk too large')
                        handle.write(payload)
            except TimeoutError:
                raise HTTPException(408, 'Transfer chunk timed out')
            handle.flush(); os.fsync(handle.fileno())
        if not size:
            raise HTTPException(400, 'Empty transfer chunk')
        return await run_in_threadpool(invoke, 'instance-copy-chunk', identifier, peer, upload, str(offset), checksum)
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


@router.post('/api/peers/instance-transfers/{identifier}/abort')
def abort(identifier: str, request: Request):
    return invoke('instance-copy-abort', token(identifier), peer_identity(request))


@router.post('/api/peers/instance-transfers/{identifier}/finish')
def finish(identifier: str, request: Request):
    return invoke('instance-copy-finish', token(identifier), peer_identity(request))


@router.get('/api/peers/instance-transfers/{identifier}')
def receiver_status(identifier: str, request: Request):
    return invoke('instance-copy-status', token(identifier), peer_identity(request))
