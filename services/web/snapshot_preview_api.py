"""Browser-session-owned temporary snapshot consoles; no restore of live containers."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
from fastapi import APIRouter,HTTPException,Request,WebSocket
from fastapi.responses import HTMLResponse
from container_admin import invoke

router=APIRouter()
TOKEN=re.compile(r'[0-9a-f]{32}\Z')


def owner(request):
    raw=request.cookies.get('mykos_session','')
    if not raw:raise HTTPException(401,'Login required')
    return hashlib.sha256(raw.encode()).hexdigest()


def authorized(request,modify=False):
    from app import mykos_stage_admin,origin
    mykos_stage_admin(request)
    if modify:origin(request)
    return owner(request)


def selection(value):
    if not TOKEN.fullmatch(value):raise HTTPException(400,'Invalid preview')
    return value


@router.post('/api/web/containers/{shop}/snapshots/{identifier}/preview',include_in_schema=False)
def create(shop:str,identifier:str,request:Request):
    actor=authorized(request,True)
    if not re.fullmatch(r'[0-9]{8}',shop) or not re.fullmatch(r'[0-9]{10,}-[0-9a-f]{12}',identifier):
        raise HTTPException(400,'Invalid snapshot')
    data=invoke('snapshot-preview-create',shop,identifier,actor)
    return {**data,'url':'/mykos/api/web/snapshot-preview/'+selection(data['token'])+'/page'}


@router.get('/api/web/snapshot-preview/{value}',include_in_schema=False)
def status(value:str,request:Request):
    return invoke('snapshot-preview-status',selection(value),authorized(request))


@router.post('/api/web/snapshot-preview/{value}/heartbeat',include_in_schema=False)
def heartbeat(value:str,request:Request):
    return invoke('snapshot-preview-status',selection(value),authorized(request,True))


@router.post('/api/web/snapshot-preview/{value}/close',include_in_schema=False)
def close(value:str,request:Request):
    return invoke('snapshot-preview-close',selection(value),authorized(request,True))


@router.get('/api/web/snapshot-preview/{value}/page',include_in_schema=False)
def page(value:str,request:Request):
    data=invoke('snapshot-preview-status',selection(value),authorized(request))
    config=json.dumps({'token':value,'code':data['code'],'snapshot':data['snapshot'],'mode':data['mode']}).replace('<','\\u003c')
    html=Path(__file__).with_name('snapshot-preview.html').read_text().replace('__MYKOS_PREVIEW_CONFIG__',config)
    return HTMLResponse(html,headers={'Cache-Control':'no-store'})


@router.websocket('/api/web/snapshot-preview/{value}/terminal')
async def terminal(value:str,ws:WebSocket):
    try:
        from app import origin,mykos_stage_admin,mykos_terminal_bridge
        selection(value);origin(ws);mykos_stage_admin(ws)
        actor=owner(ws)
        data=await asyncio.to_thread(invoke,'snapshot-preview-status',value,actor)
        if data['state']!='ready' or data['mode']!='filesystem':raise HTTPException(400,'Filesystem preview is not ready')
    except (HTTPException,ValueError,OSError):
        await ws.close(code=1008);return
    await mykos_terminal_bridge(ws,value,action='preview-shell',preview_owner=actor)
