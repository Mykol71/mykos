"""Full-administrator PXE job UI. Boot clients use independent scoped leases."""
import hashlib,json,time
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,Field,SecretStr
import pxe_store as store
router=APIRouter(prefix='/api/web/pxe',tags=['PXE administration'])
def admin(request,modify=False):
    from app import check,origin
    import mykos_users
    check(request)
    if modify:origin(request)
    token=hashlib.sha256(request.cookies.get('mykos_session','').encode()).hexdigest()
    with mykos_users.connect() as c:row=c.execute('SELECT name FROM sessions WHERE token=? AND expires>?',(token,int(time.time()))).fetchone()
    if not row or not mykos_users.is_admin(row[0]):raise HTTPException(403,'Full administrator login required')
@router.get('')
def overview(request:Request):
    admin(request)
    try:cfg=store.settings()
    except store.HTTPException as error:
        if error.status_code!=503:raise
        return {'enabled':False,'profiles':store.PROFILES,'jobs':[],'assets':{},'media_index':1}
    try:assets={k:{field:v[field] for field in ('sha256','size')} for k,v in store.assets().items()}
    except store.HTTPException:assets={}
    try:index=json.loads((store.ROOT/'media.json').read_text())['index']
    except (OSError,KeyError,ValueError):index=1
    return {'enabled':True,'origin':cfg['origin'],'allowed_networks':cfg['allowed_networks'],'profiles':store.PROFILES,'assets':assets,'jobs':store.jobs(),'media_index':index}
class Arm(BaseModel):
    mac:str=Field(max_length=17)
    customer:str=Field(pattern=r'^[0-9]{8}$')
    profile:str=Field(max_length=16)
    password:SecretStr
    hostname:str|None=Field(default=None,max_length=15)
    minutes:int=Field(default=120,ge=5,le=1440)
    allow_wipe:bool=False
    confirmation:str=Field(default='',max_length=17)
    image_index:int=Field(default=1,ge=1,le=100)
@router.post('/jobs')
def arm_job(data:Arm,request:Request):
    admin(request,True)
    if len(data.password.get_secret_value())<12:raise HTTPException(400,'Use at least 12 characters for the desktop password')
    if data.allow_wipe:
        try:matched=store.mac(data.confirmation)==store.mac(data.mac)
        except ValueError:matched=False
        if not matched:raise HTTPException(400,'Repeat the target MAC to authorize erasing its disk')
    try:return store.enroll(data.mac,data.customer,data.profile,data.password.get_secret_value(),hostname=data.hostname,minutes=data.minutes,allow_wipe=data.allow_wipe,image_index=data.image_index)
    except (ValueError,store.HTTPException) as error:raise HTTPException(getattr(error,'status_code',400),str(error))
@router.post('/jobs/{key}/revoke')
def revoke(key:str,request:Request):
    admin(request,True)
    if not __import__('re').fullmatch(r'[0-9a-f]{32}',key):raise HTTPException(400,'Invalid job')
    store.revoke(key);return {'state':'revoked'}
