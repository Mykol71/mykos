"""Key-authenticated image builds and narrowly scoped installer completion."""
import re
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, SecretStr
from container_admin import invoke

router=APIRouter()
api_router=APIRouter()
OS=re.compile(r'(?:ubuntu(?:20\.04|22\.04|24\.04)|mac1[2-5]|win(?:10|11|8e)|wns(?:2016|2019|2022|2025))\Z')

class BuildRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    username:Optional[str]=None
    password:Optional[SecretStr]=None
    replace:bool=False

@api_router.put('/images/{os_name}/build',tags=['MYKOS API v2'],summary='Install, capture and commit an OS image unattended')
def image_build(os_name:str,data:BuildRequest,request:Request):
    from api_v2 import administrator
    administrator(request)
    if not OS.fullmatch(os_name):raise HTTPException(400,'Unsupported unattended image OS')
    import json
    return invoke('image-build-queue',os_name,json.dumps({'username':data.username,'password':data.password.get_secret_value() if data.password else None,'replace':data.replace}))

@api_router.get('/images/{os_name}/build',tags=['MYKOS API v2'],summary='Read unattended image build progress')
def image_build_status(os_name:str,request:Request):
    from api_v2 import authenticated
    authenticated(request)
    if not OS.fullmatch(os_name):raise HTTPException(400,'Unsupported unattended image OS')
    return invoke('image-build-status',os_name)

@router.post('/api/image-build/{os_name}/complete',include_in_schema=False)
def image_complete(os_name:str,request:Request):
    if not OS.fullmatch(os_name):raise HTTPException(403,'Invalid installation completion')
    auth=request.headers.get('authorization','')
    if not re.fullmatch(r'Bearer [A-Za-z0-9_-]{40,64}',auth):raise HTTPException(403,'Invalid installation completion')
    try:return invoke('image-build-complete',os_name,auth[7:])
    except HTTPException:raise HTTPException(403,'Invalid installation completion')


class DeleteBuildRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmation:str

@api_router.delete('/images/{os_name}/build',tags=['MYKOS API v2'],summary='Remove a failed image builder and its private disks')
def image_build_delete(os_name:str,data:DeleteBuildRequest,request:Request):
    from api_v2 import administrator
    administrator(request)
    if not OS.fullmatch(os_name):raise HTTPException(400,'Unsupported unattended image OS')
    return invoke('image-build-delete',os_name,data.confirmation)

@router.delete('/api/web/image-build/{os_name}',include_in_schema=False)
def web_image_build_delete(os_name:str,data:DeleteBuildRequest,request:Request):
    from app import mykos_stage_admin
    mykos_stage_admin(request)
    if not OS.fullmatch(os_name):raise HTTPException(400,'Unsupported unattended image OS')
    return invoke('image-build-delete',os_name,data.confirmation)


def prepared_os(os_name):
    NATIVE_OS_NAMES=('rh7','rh8','rh9','rh10','mssql2022','mssql2025','rocky8','rocky9','rocky10','alma8','alma9','alma10','oracle8','oracle9','oracle10','centos8','centos9','centos10','fedora43','fedora44')
    if not OS.fullmatch(os_name) and os_name not in NATIVE_OS_NAMES and os_name not in ('win7u','winxp','winme','win2k','win98','mac12','mac13','mac14','mac15'):
        raise HTTPException(400,'Unsupported image OS')

@api_router.delete('/images/{os_name}',tags=['MYKOS API v2'],summary='Remove a prepared OS image and generic base if unreferenced')
def image_remove(os_name:str,data:DeleteBuildRequest,request:Request):
    from api_v2 import administrator
    administrator(request);prepared_os(os_name)
    return invoke('image-remove',os_name,data.confirmation)

@api_router.get('/images/{os_name}/delete',tags=['MYKOS API v2'],summary='Read prepared-image removal progress')
def image_remove_status(os_name:str,request:Request):
    from api_v2 import administrator
    administrator(request);prepared_os(os_name)
    return invoke('image-remove-status',os_name)

@router.delete('/api/web/images/{os_name}',include_in_schema=False)
def web_image_remove(os_name:str,data:DeleteBuildRequest,request:Request):
    from app import mykos_stage_admin,origin
    mykos_stage_admin(request);origin(request);prepared_os(os_name)
    return invoke('image-remove',os_name,data.confirmation)
