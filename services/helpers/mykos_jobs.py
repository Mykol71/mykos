"""Host-local build/sync history. Never store commands, credentials or raw logs."""
import contextlib
import contextvars
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import socket
import stat
import subprocess
import time

UNIT_CACHE=contextvars.ContextVar('jobs_unit_cache',default=None)
ROOT=Path('/var/lib/mykos/activity-jobs')
POS=Path('/usr/local/share/mykos/pos')
SYNC=Path('/usr/local/share/mykos/repo/bin/sync_repos.py')
WORKER='/usr/local/sbin/mykos-jobs-worker'
ID=re.compile(r'[0-9a-f]{32}\Z')
NATIVE=frozenset(('rh6','rh7','rh8','rh9','rh10','oracle6','oracle7','centos6','centos7','scientific6','scientific7','springdale6','springdale7','debian13','archlinux','sles15sp7','amazon2023','alpine','rocky8','rocky9','rocky10','alma8','alma9','alma10','oracle8','oracle9','oracle10','centos8','centos9','centos10','fedora43','fedora44','mssql2022','mssql2025'))
REPOS=frozenset(('all','6','7','8','9','10','centos6','centos7','oracle6','oracle7','scientific6','scientific7','springdale6','springdale7'))
VM_BUILDS=frozenset(('fedora43vm','fedora44vm','ubuntu20.04','ubuntu22.04','ubuntu24.04','mac12','mac13','mac14','mac15','win10','win11','win8e','wns2016','wns2019','wns2022','wns2025'))
TERMINAL=frozenset(('succeeded','failed','interrupted'))
PHASES={'starting':'Starting','downloading':'Downloading vendor packages','building':'Building image','configuring':'Configuring image','validating':'Verifying packages and metadata','publishing':'Publishing validated repositories','capturing':'Capturing image','complete':'Complete'}

def boot_id():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def process_start(pid):
    try:return Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19]
    except (OSError,IndexError):return None

def safe_dir(path):
    path=Path(path)
    for parent in reversed((path,*path.parents)):
        if parent.is_symlink():raise ValueError('Unsafe job directory')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    value=path.stat()
    if value.st_uid!=os.geteuid() or stat.S_IMODE(value.st_mode)!=0o700:raise ValueError('Jobs directory must be private and root-owned')
    return path

@contextlib.contextmanager
def locked():
    safe_dir(ROOT)
    with os.fdopen(os.open(ROOT/'jobs.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600),'a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX);yield

def identifier(key):
    if not isinstance(key,str) or not ID.fullmatch(key):raise ValueError('Invalid job identifier')
    return key

def read(key):
    with os.fdopen(os.open(ROOT/(identifier(key)+'.json'),os.O_RDONLY|os.O_NOFOLLOW)) as handle:
        st=os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077 or st.st_size>65536:raise ValueError('Unsafe job record')
        value=json.load(handle)
    if value.get('id')!=key or value.get('format')!=1:raise ValueError('Invalid job record')
    return value

def save(value):
    import tempfile
    safe_dir(ROOT);fd,name=tempfile.mkstemp(prefix='.job-',dir=ROOT)
    try:
        with os.fdopen(fd,'w') as out:json.dump(value,out);out.flush();os.fsync(out.fileno())
        os.replace(name,ROOT/(identifier(value['id'])+'.json'))
        directory=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)

def selection(kind,target):
    if kind not in ('repository-sync','native-build'):raise ValueError('Unsupported job kind')
    if target not in (REPOS if kind=='repository-sync' else NATIVE):raise ValueError('Unsupported job selection')
    return target

def unit_state(unit):
    if not re.fullmatch(r'mykos-jobs-[0-9a-f]{32}\.service',unit):raise ValueError('Invalid job unit')
    cached=UNIT_CACHE.get()
    if cached is not None:return cached.get(unit,'inactive')
    result=subprocess.run(['systemctl','show',unit,'-p','ActiveState','--value'],capture_output=True,text=True,timeout=10)
    return result.stdout.strip() if result.returncode==0 else 'unknown'

def alive(value):
    if value.get('boot_id')!=boot_id():return False
    if value.get('pid'):
        current=process_start(value['pid'])
        if not value.get('process_start'):return None if current is not None else False
        return current is not None and current==value['process_start']
    return None

def reconcile(value):
    if value['state'] in TERMINAL:return value
    running=alive(value)
    if running is True:return value
    state=unit_state(value['unit']) if value.get('unit') else 'inactive'
    if state in ('active','activating','deactivating','reloading','unknown'):return value
    if value['state']=='queued' and time.time()-value['created_at']<30:return value
    value.update(state='interrupted',revision=value['revision']+1,finished_at=time.time(),message='Worker stopped; retry is manual. Saved repository downloads or image disks have not been discarded.')
    save(value);return value

def records():
    safe_dir(ROOT);paths=list(ROOT.glob('*.json'))
    if len(paths)>1000:raise ValueError('Job history limit exceeded; inspect root-owned records')
    return [reconcile(read(p.stem)) for p in paths]

def settled(value):
    if alive(value) is True:return False
    if value.get('boot_id')==boot_id():
        if value.get('pid') and not value.get('process_start') and process_start(value['pid']) is not None:return False
        if value.get('child_pid'):
            current=process_start(value['child_pid'])
            if current is not None and (not value.get('child_start') or current==value['child_start']):return False
    if value.get('unit') and unit_state(value['unit']) not in ('inactive','failed'):return False
    return True

def public(value):
    keys=('id','kind','target','state','phase','message','created_at','started_at','activity_at','finished_at','revision','progress','exit_code','retry_of','retry_child','events','component','cache_verified','cache_rejected','cache_restored','cache_saved')
    result={k:value[k] for k in keys if k in value}
    if value['state'] not in TERMINAL and alive(value) is not True and value.get('unit') and unit_state(value['unit'])=='unknown':result.update(state='unknown',message='Worker status unavailable; recovery is disabled')
    result['retry_allowed']=value['state'] in ('failed','interrupted') and not value.get('retry_child') and settled(value)
    return result

def update(key,**fields):
    with locked():
        value=read(key);value.update(fields);value['revision']+=1;save(value);return value

def prune():
    # History only: never remove caches, images, disks, or running job records.
    values=sorted((v for v in records() if v['state'] in TERMINAL),key=lambda v:v['created_at'],reverse=True)
    for value in values[100:]:
        p=ROOT/(value['id']+'.json')
        if not p.is_symlink():p.unlink()

def new(kind,target,retry_of=None):
    selection(kind,target);now=time.time();key=secrets.token_hex(16)
    value={'format':1,'id':key,'kind':kind,'target':target,'state':'queued','phase':'starting','created_at':now,'activity_at':now,'revision':1,'boot_id':boot_id(),'events':[]}
    if retry_of:value['retry_of']=retry_of
    save(value);return value

def admission(kind,target):
    from mykos_host_maintenance import admission as host_admission
    host_admission()
    if kind=='repository-sync':
        from mykos_admin import repository_source,repo_sync_state
        if repository_source():raise ValueError('Repositories are managed on the repository host')
        if repo_sync_state() in ('active','activating','deactivating','reloading','unknown'):raise ValueError('Repository sync is already active or its status is unavailable')
    else:
        import mykos_prune
        if mykos_prune.builders():raise ValueError('An image build is already active; wait for it to finish')

def retry(key,revision):
    with locked():
        value=reconcile(read(identifier(key)))
        if value.get('retry_child') and value.get('retry_revision')==revision:return public(read(value['retry_child']))
        if type(revision) is not int or value['revision']!=revision:raise ValueError('Job changed; refresh before retrying')
        if value.get('retry_child'):return public(read(value['retry_child']))
        if value['state'] not in ('failed','interrupted'):raise ValueError('Only failed or interrupted jobs may be retried')
        if not settled(value):raise ValueError('Worker is still running or stopping; retry is unavailable')
        kind,target=value['kind'],value['target'];selection(kind,target);admission(kind,target)
        if any((v['state'] not in TERMINAL or not settled(v)) and v['kind']==kind for v in records()):raise ValueError('A job of this kind is already active')
        if kind=='native-build' and subprocess.run(['podman','image','exists','localhost/'+target+':latest'],capture_output=True).returncode==0:raise ValueError('An image already exists for this failed build. Review it with image controls; it will not be replaced.')
        child=new(kind,target,value['id']);child['unit']='mykos-jobs-'+child['id']+'.service';save(child)
        value.update(retry_child=child['id'],retry_revision=revision,revision=value['revision']+1);save(value)
        try:
            args=['systemd-run','--no-block','--unit='+child['unit'],'--property=Type=oneshot','--property=Nice=15','--property=IOSchedulingClass=best-effort','--property=IOSchedulingPriority=7','--property=CPUWeight=10','--property=IOWeight=10','--property=OOMScoreAdjust=500','--property=UMask=0077','--property=TimeoutStartSec=24h','--property=KillMode=control-group']
            if kind=='repository-sync':args+=['--slice=mykosreposync.slice']
            subprocess.run([*args,WORKER,'--job',child['id']],check=True,capture_output=True,text=True,timeout=15)
        except (OSError,subprocess.SubprocessError):
            child.update(state='failed',finished_at=time.time(),message='Could not start the worker. No existing user workload was stopped.',revision=child['revision']+1);save(child)
        return public(child)

def progress(line,value):
    if line.startswith('MYKOS_JOB_COMPONENT '):
        component=line.strip().split()[-1]
        if re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}',component):value.update(component=component,progress=None)
    marker=re.fullmatch(r'MYKOS_JOB_CACHE_(VERIFIED|REJECTED|RESTORED|SAVED) ([0-9]{1,16})(?: ([0-9]{1,12}))?',line.strip())
    if marker:
        name=marker[1].lower();value['cache_'+name]=int(marker[3] if name in ('saved','restored') and marker[3] else marker[2])
    if line.startswith('MYKOS_JOB_PHASE '):
        phase=line.strip().split()[-1]
        if phase in PHASES:value.update(phase=phase,message=PHASES[phase],progress=None)
    found=re.search(r'\(([0-9]{1,8})/([0-9]{1,8})\)',line)
    if found:
        done,total=map(int,found.groups())
        if 0<=done<=total and total>0:value.update(phase='downloading',message=PHASES['downloading'],progress={'completed':done,'total':total,'unit':'packages'})
    for needle,message in [('No more mirrors to try','A vendor package download failed. Verified saved downloads can be reused on retry.'),('Checksum mismatch:','Repository checksum verification failed; nothing incomplete was published.'),('GPG','Vendor signature verification needs attention.'),('Image creation failed','The image installer failed. Inspect its local build diagnostics before retrying.')]:
        if needle in line:value['failure_hint']=message

def require_root():
    if os.geteuid()!=0:raise ValueError('Jobs require root')

def worker(kind=None,target=None,key=None):
    import sys
    require_root()
    from mykos_host_maintenance import admission as host_admission
    host_admission()
    with locked():
        if key:
            value=read(identifier(key));kind,target=value['kind'],value['target'];selection(kind,target)
            if value['state']!='queued':raise ValueError('Job has already been claimed')
        else:
            selection(kind,target)
            if any((v['state'] not in TERMINAL or not settled(v)) and v['kind']==kind for v in records()):raise ValueError('This job kind is already active')
            value=new(kind,target)
        token=process_start(os.getpid())
        if not token:raise ValueError('Cannot establish worker process identity')
        value.update(state='running',started_at=time.time(),activity_at=time.time(),pid=os.getpid(),process_start=token,boot_id=boot_id(),revision=value['revision']+1);save(value)
    env=os.environ.copy()
    if kind=='repository-sync':command=[sys.executable,str(SYNC)]+([] if target=='all' else [target]);env['MYKOS_JOB_SYNC_MANAGED']='1';env['MYKOS_JOB_ID']=value['id']
    else:command=['/usr/bin/bash',str(POS/'pos'),'--build-image',target];env['MYKOS_JOB_NATIVE_MANAGED']=target
    process=None;last_save=0;buffer='';code=None
    previous={}
    def interrupted(signum,frame):raise InterruptedError('Job interrupted')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):previous[sig]=signal.signal(sig,interrupted)
    try:
        process=subprocess.Popen(command,cwd=str(POS) if kind=='native-build' else '/',env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
        value.update(child_pid=process.pid,child_start=process_start(process.pid));save(value)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                for entry,_ in selector.select(1):
                    data=os.read(entry.fileobj.fileno(),65536)
                    if not data:selector.unregister(entry.fileobj);continue
                    sys.stdout.buffer.write(data);sys.stdout.buffer.flush();value['activity_at']=time.time()
                    buffer+=data.decode('utf-8','replace')
                    lines=re.split(r'[\r\n]',buffer);buffer=lines.pop()[-8192:]
                    for line in lines:
                        before=value.get('phase');progress(line,value)
                        if value.get('phase')!=before:value['events']=(value.get('events',[])+[{'at':time.time(),'message':PHASES.get(value['phase'],'Working')}])[-20:]
                if time.monotonic()-last_save>=1:
                    value['revision']+=1;save(value);last_save=time.monotonic()
        if buffer:progress(buffer,value)
        code=process.wait();value.update(state='succeeded' if code==0 else 'failed',exit_code=code,message='Completed' if code==0 else value.get('failure_hint','Worker failed. Inspect the local build or sync log before retrying.'))
        if code==0:value.update(phase='complete',progress=None)
        return code
    except BaseException:
        value.update(state='interrupted',message='Worker interrupted; recovery requires an explicit retry.');raise
    finally:
        if process is not None and process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=30)
            except ProcessLookupError:pass
            except subprocess.TimeoutExpired:
                # A timed-out worker remains uncertain. Never report success.
                value.update(state='interrupted',message='Worker is still stopping. Wait and inspect its local status before retrying.')
        for sig,old in previous.items():signal.signal(sig,old)
        value.update(finished_at=time.time(),revision=value['revision']+1);value.pop('failure_hint',None);save(value)
        with locked():prune()

def cache_module():
    import importlib.util
    spec=importlib.util.spec_from_file_location('mykos_jobs_sync',SYNC);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def saved_downloads():
    if not SYNC.is_file():return []
    sync=cache_module()
    return sync.CACHE.status(sync.ROOT)

def discard_cache(key,revision):
    identifier(key)
    sync=cache_module()
    with locked():
        value=reconcile(read(key))
        if value['revision']!=int(revision):raise ValueError('Job changed; refresh before discarding downloads')
        if value['state'] not in TERMINAL or not settled(value):raise ValueError('Worker is still active; saved downloads cannot be discarded')
        with os.fdopen(os.open(sync.LOCK,os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o644),'a') as handle:
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('Repository sync is active; saved downloads cannot be discarded')
            removed=sync.CACHE.discard(sync.ROOT,key)
        return {'removed_selections':removed}


def observed_jobs(values):
    """Read-only views of work started before tracking was installed."""
    out=[];active={v['kind'] for v in values if v['state'] not in TERMINAL}
    if 'repository-sync' not in active:
        result=subprocess.run(['systemctl','list-units','--state=active,activating,deactivating','--plain','--no-legend','--type=service','mykos-repo-sync.service','mykos-repo-sync-cli.service'],capture_output=True,text=True,timeout=5)
        if result.returncode==0 and result.stdout.strip():out.append({'id':'observed-sync','kind':'repository-sync','target':'existing selection','state':'running','phase':'starting','message':'Existing repository sync is active. It started before job tracking; inspect its local console for details.','observed':True,'retry_allowed':False})
    if 'native-build' not in active:
        import mykos_prune
        from mykos_runtime import native_build_os
        for pid,value in mykos_prune.builders().items():
            target=native_build_os(value['args'])
            if target not in NATIVE:continue
            out.append({'id':'observed-native-'+str(pid),'kind':'native-build','target':target,'state':'running','phase':'building','message':'Existing image builder is active. It started before job tracking; inspect its local console for details.','observed':True,'retry_allowed':False})
            break
    return out

def history():
    units=None
    try:
        result=subprocess.run(['systemctl','list-units','--all','--plain','--no-legend','--type=service','mykos-jobs-*.service'],capture_output=True,text=True,timeout=5)
        if result.returncode==0:units={parts[0]:parts[2] for line in result.stdout.splitlines() if len(parts:=line.split())>=4 and re.fullmatch(r'mykos-jobs-[0-9a-f]{32}\.service',parts[0])}
    except (OSError,subprocess.SubprocessError):pass
    token=UNIT_CACHE.set(units)
    try:
        with locked():values=[public(v) for v in sorted(records(),key=lambda v:v['created_at'],reverse=True)[:100]]
    finally:UNIT_CACHE.reset(token)
    try:
        caches=saved_downloads()
        for value in values:
            saved=[c for c in caches if c.get('job_id')==value['id']]
            if saved:value['saved_downloads']={'packages':sum(c['saved_packages'] for c in saved),'bytes':sum(c['saved_bytes'] for c in saved)}
    except (OSError,ValueError):
        for value in values:
            if value['kind']=='repository-sync':value['cache_unavailable']=True
    return {'hostname':socket.gethostname(),'jobs':values+vm_jobs()+observed_jobs(values),'collected_at':time.time()}

def dispatch(args):
    require_root()
    if args==['jobs-list']:return history()
    if len(args)==3 and args[0]=='jobs-cache-discard':return discard_cache(args[1],args[2])
    if len(args)==3 and args[0]=='jobs-retry':
        if args[1].startswith('vm-'):return retry_vm(args[1][3:],args[2])
        return retry(args[1],int(args[2]))
    raise ValueError('Unsupported jobs operation')


def vm_revision(value):
    import hashlib
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def vm_status(os_name):
    import mykos_image_build as image
    if os_name not in VM_BUILDS:raise ValueError('Unsupported unattended image build')
    image.selection(os_name)
    folder=image.STATE/os_name
    if folder.is_symlink():raise ValueError('Unsafe image build status directory')
    path=folder/'status.json'
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW)) as handle:
        st=os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077 or st.st_size>65536:raise ValueError('Unsafe image build status')
        value=json.load(handle)
        if not isinstance(value,dict) or value.get('os')!=os_name:raise ValueError('Invalid image build status')
        return value

def vm_jobs():
    import mykos_image_build as image
    out=[]
    if image.STATE.is_symlink():raise ValueError('Unsafe image builds directory')
    if not image.STATE.exists():return out
    units=vm_unit_states()
    for folder in sorted(image.STATE.iterdir()):
        if folder.name not in VM_BUILDS:continue
        try:image.selection(folder.name)
        except ValueError:continue
        try:value=vm_status(folder.name)
        except FileNotFoundError:continue
        state=value.get('state','unknown');phase=state if state in PHASES else 'starting'
        active=(units or {}).get(folder.name,'inactive') if units is not None else 'unknown'
        observed='succeeded' if state=='ready' else state if state in ('failed','queued') else 'running' if active in ('active','activating') else 'unknown'
        out.append({'id':'vm-'+folder.name,'kind':'vm-build','target':folder.name,'state':observed,'phase':phase,'message':('Worker status unavailable; refresh before taking action' if observed=='unknown' else PHASES.get(state,'VM image build needs attention' if state=='failed' else 'Image build queued')),'activity_at':value.get('updated_at'),'revision':vm_revision(value),'retry_allowed':state=='failed' and active in ('inactive','failed'),'observed':True})
    return out

def retry_vm(os_name,revision):
    import mykos_image_build as image
    with locked():
        value=vm_status(os_name)
        if vm_revision(value)!=revision:raise ValueError('Image build changed; refresh before resuming')
        if value.get('state')!='failed':raise ValueError('Only a failed VM image build may be resumed')
        units=vm_unit_states()
        if units is None or units.get(os_name,'inactive') not in ('inactive','failed'):raise ValueError('Image worker is active or unavailable; refresh before resuming')
        result=image.queue(os_name,expected_job_revision=revision)
        return {'id':'vm-'+os_name,'state':result['state'],'message':'Resume queued using the saved build and its existing credentials'}


def vm_unit_states():
    try:
        result=subprocess.run(['systemctl','list-units','--all','--plain','--no-legend','--type=service','mykos-image-build@*.service'],capture_output=True,text=True,timeout=5)
        if result.returncode:return None
    except (OSError,subprocess.SubprocessError):return None
    out={}
    for row in result.stdout.splitlines():
        fields=row.split()
        if len(fields)>=4 and re.fullmatch(r'mykos-image-build@[a-z0-9.]+\.service',fields[0]):out[fields[0][18:-8]]=fields[2]
    return out
