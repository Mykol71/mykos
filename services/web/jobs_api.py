"""Jobs are local full-administrator controls, never peer-forwarded."""
import re
from pathlib import Path
from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import Response,JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from instance_transfers import local_admin
from container_admin import invoke
router=APIRouter()

@router.get('/jobs.js',include_in_schema=False)
def script():
    return Response(Path(__file__).with_name('jobs.js').read_text(),media_type='text/javascript',headers={'Cache-Control':'no-cache'})

@router.get('/jobs.css',include_in_schema=False)
def stylesheet():
    return Response(Path(__file__).with_name('jobs.css').read_text(),media_type='text/css',headers={'Cache-Control':'no-cache'})

@router.get('/api/web/jobs')
def history(request:Request):
    local_admin(request)
    return JSONResponse(invoke('jobs-list'),headers={'Cache-Control':'no-store'})

class Retry(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:str=Field(min_length=1,max_length=64)

@router.post('/api/web/jobs/{key}/retry')
def retry(key:str,data:Retry,request:Request):
    local_admin(request,True)
    if not re.fullmatch(r'(?:[0-9a-f]{32}|vm-[a-z0-9.]{3,24})',key):raise HTTPException(400,'Invalid job identifier')
    return JSONResponse(invoke('jobs-retry',key,data.revision),headers={'Cache-Control':'no-store'})

class Discard(Retry):
    confirmation:str=Field(max_length=40)

@router.post('/api/web/jobs/{key}/saved-downloads/discard')
def discard(key:str,data:Discard,request:Request):
    local_admin(request,True)
    if not re.fullmatch(r'[0-9a-f]{32}',key) or data.confirmation!='DISCARD':raise HTTPException(400,'Confirm DISCARD for this job')
    return JSONResponse(invoke('jobs-cache-discard',key,data.revision),headers={'Cache-Control':'no-store'})
