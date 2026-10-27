"""Dashboard views and administrator operations for MYKOS Backup and Repos."""
import json
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


@router.get('/backups')
def backups(request: Request, refresh: bool = False):
    authorize(request)
    return listing('backup', refresh)


@router.get('/repos')
def repos(request: Request, refresh: bool = False):
    authorize(request)
    return listing('repo', refresh)


class BackupAction(BaseModel):
    action: Literal['create', 'key', 'subscriber', 'delete', 'keys']
    code: str = Field(pattern=r'^[0-9]{8}$')
    key: Optional[str] = Field(default=None, max_length=3000)
    subscriber: bool = False
    remove_data: bool = False


class RepoAction(BaseModel):
    action: Literal['enable', 'disable', 'sync', 'delete']
    name: str = Field(default='all', pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')


def perform(kind, action, target, payload):
    try:
        return invoke(kind + '-action', action, target, json.dumps(payload))
    finally:
        with _lock:
            _cache.pop(kind, None)


@router.post('/backups')
def backup_action(data: BackupAction, request: Request):
    authorize(request, modify=True)
    return perform('backup', data.action, data.code, data.model_dump(exclude={'action', 'code'}))


@router.post('/repos')
def repo_action(data: RepoAction, request: Request):
    authorize(request, modify=True)
    return perform('repo', data.action, data.name, {})
