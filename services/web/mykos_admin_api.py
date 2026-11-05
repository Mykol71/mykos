"""Dashboard views and administrator operations for MYKOS repositories."""
import json
import os
from urllib.parse import urlsplit, urlunsplit
import threading
import time
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from container_admin import invoke

router = APIRouter(prefix='/api/web/mykos')
_cache = {}
_lock = threading.Lock()


def authorize(request, modify=False):
    from app import check, mykos_terminal_permission, origin
    check(request)
    if modify:
        if not mykos_terminal_permission(request).get('allowed'):
            raise HTTPException(403, 'Administrator login required')
        origin(request)


def listing(kind, refresh=False):
    with _lock:
        cached = _cache.get(kind)
        if not refresh and cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        result = invoke(kind + '-list')
        _cache[kind] = (time.monotonic(), result)
        return result




@router.get('/repos')
def repos(request: Request, refresh: bool = False):
    authorize(request)
    data=dict(listing('repo', refresh))
    if not data.get('download_base_url') and not data.get('public_base_url'):
        # A forwarded UI or SSH tunnel can have a different browser origin.
        # Use the configured server origin, never that incidental UI address.
        origin=os.environ.get('MYKOS_PUBLIC_ORIGIN')
        if origin:
            url=urlsplit(origin)
            if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise HTTPException(503,'Invalid repository origin configuration')
            data['download_base_url']=urlunsplit((url.scheme,url.netloc,'/repos/','',''))
    return data




class RepoAction(BaseModel):
    action: Literal['enable', 'disable', 'sync', 'delete']
    name: str = Field(default='all', pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')


def perform(kind, action, target, payload):
    try:
        return invoke(kind + '-action', action, target, json.dumps(payload))
    finally:
        with _lock:
            _cache.pop(kind, None)




@router.post('/repos')
def repo_action(data: RepoAction, request: Request):
    authorize(request, modify=True)
    return perform('repo', data.action, data.name, {})
