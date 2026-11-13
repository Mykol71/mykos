"""Host-local full-admin maintenance controls; never peer-forwarded."""
import json
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from container_admin import invoke
from instance_transfers import local_admin
router = APIRouter()

class Prepare(BaseModel):
    model_config = ConfigDict(extra='forbid')
    hostname: str = Field(min_length=1, max_length=253)
    plan_token: str = Field(pattern=r'^[0-9a-f]{64}$')
    restart: list[str] = Field(default_factory=list, max_length=100)

@router.get('/api/web/host/maintenance/plan')
def plan(request: Request):
    local_admin(request)
    return invoke('maintenance-plan')

@router.get('/api/web/host/maintenance/status')
def status(request: Request):
    local_admin(request)
    return invoke('maintenance-status')

@router.post('/api/web/host/maintenance/prepare')
def prepare(data: Prepare, request: Request):
    local_admin(request, True)
    return invoke('maintenance-prepare', data.hostname, json.dumps(data.restart), data.plan_token)

class Resume(BaseModel):
    model_config = ConfigDict(extra='forbid')
    hostname: str | None = Field(default=None, max_length=253)
    restart: list[str] = Field(default_factory=list, max_length=100)

@router.post('/api/web/host/maintenance/resume')
def resume(data: Resume, request: Request):
    local_admin(request, True)
    return invoke('maintenance-resume', data.model_dump_json(exclude_none=True))

@router.get('/host-maintenance.js', include_in_schema=False)
def script():
    from pathlib import Path
    from fastapi.responses import Response
    return Response(Path(__file__).with_name('host-maintenance.js').read_text(), media_type='text/javascript', headers={'Cache-Control':'no-cache'})
