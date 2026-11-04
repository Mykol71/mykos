"""HTTP boot and short-lived, target-scoped Windows staging leases."""
import base64
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import pxe_store as store

router = APIRouter(prefix='/pxe', tags=['PXE staging'])
NO_CACHE = {'Cache-Control':'no-store','Pragma':'no-cache','Referrer-Policy':'no-referrer'}


def ip(request):
    return request.client.host if request.client else ''


def secure(request):
    if request.url.scheme != 'https': raise HTTPException(403,'Staging leases require HTTPS')


def text(value, kind='text/plain'):
    return Response(value,media_type=kind,headers=NO_CACHE)


@router.get('/autoexec.ipxe')
def autoexec():
    origin=store.settings()['origin']
    return text('#!ipxe\n# iPXE https://ipxe.org\ndhcp || exit\nchain '+origin+'/mykos/pxe/select.ipxe?mac=${net0/mac:hexhyp} || exit\n')


@router.get('/select.ipxe')
def select(request: Request, mac: str):
    secure(request)
    row=store.claim(mac,ip(request));cfg=json.loads(row['config']);origin=store.settings()['origin']
    # A fresh lease is delivered in an HTTPS initrd body, never in a URL or access log.
    lines=['#!ipxe','# iPXE https://ipxe.org','iseq ${platform} efi || exit']
    lines += ['kernel '+origin+'/mykos/pxe/assets/'+cfg['assets']['wimboot']['sha256']+'/wimboot index=2']
    for name in ('curl.exe','curl-ca-bundle.crt','mykos-pe-guard.exe'):
        lines.append('initrd '+origin+'/mykos/pxe/assets/'+cfg['assets'][name]['sha256']+'/'+name+' '+name)
    lines += ['initrd --name install.cmd '+origin+'/mykos/pxe/bootstrap.cmd?mac='+row['mac'],
              'initrd '+origin+'/mykos/pxe/winpeshl.ini winpeshl.ini',
              'initrd '+origin+'/mykos/pxe/assets/'+cfg['assets']['boot.wim']['sha256']+'/boot.wim boot.wim','boot']
    return text('\n'.join(lines)+'\n')


@router.get('/bootstrap.cmd')
def bootstrap(request: Request, mac: str):
    secure(request);row=store.claim(mac,ip(request))
    data=(Path(__file__).with_name('pxe-winpe.cmd')).read_text()
    cfg=json.loads(row['config'])
    values={'ORIGIN':store.settings()['origin'],'ID':row['id'],'TICKET':row['ticket'],
            'MAC':row['mac'].upper(),'EMPTY':'0' if cfg['allow_wipe'] else '1','INDEX':str(cfg['image_index'])}
    for name,value in values.items(): data=data.replace('@@'+name+'@@',value)
    return text(data.replace('\r\n','\n').replace('\n','\r\n'))


@router.get('/winpeshl.ini')
def shell():
    store.settings()
    return text('[LaunchApps]\r\n%SYSTEMROOT%\\System32\\cmd.exe, /k %SYSTEMROOT%\\System32\\install.cmd\r\n')


@router.get('/assets/{digest}/{name}')
def asset(digest: str,name: str,request: Request):
    if not store.source_allowed(ip(request)): raise HTTPException(403,'Source subnet is not allowed')
    item=store.asset(name)
    if digest!=item['sha256']: raise HTTPException(404,'Asset revision not found')
    # Jobs pin content; admin must reimport changed media rather than mutate it in place.
    return FileResponse(item['path'],media_type='application/octet-stream',headers={'Cache-Control':'private, max-age=3600','ETag':'"'+digest+'"'})


def leased(key,request):
    secure(request)
    return store.authorize(key,request.headers.get('X-MYKOS-PXE-Ticket',''),ip(request))


@router.get('/jobs/{key}/answer.xml')
def answer(key: str,request: Request):
    return Response(store.answer(leased(key,request)),media_type='application/xml',headers=NO_CACHE)


@router.get('/jobs/{key}/stage.ps1')
def stage(key: str,request: Request):
    row=leased(key,request);cfg=json.loads(row['config'])
    config={'origin':store.settings()['origin'],'job':key,'ticket':row['ticket'],'profile':row['profile'],
            'customer':row['customer'],'username':cfg['username'],'apps':cfg['apps'],
            'assets':{name:{k:item[k] for k in ('sha256','size')} for name,item in cfg['assets'].items()}}
    encoded=base64.b64encode(json.dumps(config).encode()).decode()
    return text((Path(__file__).with_name('pxe-stage.ps1')).read_text().replace('@@CONFIG@@',encoded))


@router.get('/jobs/{key}/install.wim')
def image(key: str,request: Request):
    row=leased(key,request);item=json.loads(row['config'])['assets']['install.wim']
    current=store.asset('install.wim')
    if current!=item: raise HTTPException(409,'Media changed after this job was armed')
    return FileResponse(item['path'],media_type='application/octet-stream',headers=NO_CACHE)


class Phase(BaseModel):
    state: str


@router.post('/jobs/{key}/status')
def status(key: str,data: Phase,request: Request):
    row=leased(key,request);store.status(row,data.state)
    return {'state':data.state}
