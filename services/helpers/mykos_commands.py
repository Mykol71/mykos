"""Private, at-most-once command queue; only the admin-key API may submit jobs."""
import base64
import contextlib
from datetime import datetime, timezone
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
import tempfile
import time

ROOT = Path('/var/lib/mykos/commands')
LIMIT = 64 * 1024
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
ACTIVE = ('starting', 'running')

class QueueError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message); self.status = status


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value): raise QueueError('Invalid command ID')
    return value


def validate(data):
    if not isinstance(data, dict) or set(data) - {'target','command','run_at','timeout_seconds'}:
        raise QueueError('Unexpected command fields')
    target = data.get('target', 'host')
    if target != 'host' and (not isinstance(target,str) or not re.fullmatch(r'[0-9]{8}',target)):
        raise QueueError('Target must be host or an eight-digit container ID')
    script = data.get('command')
    if not isinstance(script,str) or not script.strip() or '\x00' in script or len(script.encode()) > 16000:
        raise QueueError('Command must be nonempty, without NUL, and at most 16000 UTF-8 bytes')
    timeout = data.get('timeout_seconds',300)
    if type(timeout) is not int or not 1 <= timeout <= 3600: raise QueueError('Timeout must be 1–3600 seconds')
    at = data.get('run_at')
    if at is not None:
        try:
            dt = datetime.fromisoformat(at.replace('Z','+00:00'))
            if dt.utcoffset() is None: raise ValueError()
            at = dt.astimezone(timezone.utc).isoformat()
        except (ValueError,TypeError,AttributeError): raise QueueError('run_at must include a timezone')
    return dict(target=target,command=script,run_at=at,timeout_seconds=timeout)


def init():
    for parent in [*reversed(ROOT.parents),ROOT]:
        if parent.is_symlink(): raise QueueError('Symlink queue path rejected')
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    st=ROOT.stat()
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise QueueError('Command queue must be private and owned by root')


@contextlib.contextmanager
def locked():
    init()
    fd=os.open(ROOT/'queue.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        yield


def read(key):
    path=ROOT/(identifier(key)+'.json')
    try: fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    except FileNotFoundError: raise QueueError('Command not found',404)
    with os.fdopen(fd) as handle:
        st=os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise QueueError('Unsafe command record')
        return json.load(handle)


def save(value):
    fd,name=tempfile.mkstemp(prefix='.command-',dir=ROOT)
    try:
        with os.fdopen(fd,'w') as handle:
            json.dump(value,handle);handle.flush();os.fsync(handle.fileno())
        os.replace(name,ROOT/(identifier(value['id'])+'.json'))
        fd=os.open(ROOT,os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(name): os.unlink(name)


def public(value, detail=True):
    return value if detail else {k:v for k,v in value.items() if k not in ('command','output')}


def dispatch(args):
    try:
        action=args[0]
        with locked():
            if action == 'command-list' and len(args)==1:
                return {'commands':[public(v,False) for v in sorted((read(p.stem) for p in ROOT.glob('*.json')),key=lambda v:v['created_at'],reverse=True)[:100]]}
            if action == 'command-get' and len(args)==2: return read(args[1])
            if action == 'command-put' and len(args)==3:
                key=identifier(args[1]);data=validate(json.loads(args[2]))
                try: previous=read(key)
                except QueueError as exc:
                    if exc.status != 404: raise
                    previous=None
                if previous:
                    if all(previous.get(k)==v for k,v in data.items()): return previous
                    if previous['state']!='pending': raise QueueError('Command already claimed; use a new ID',409)
                value={**data,'id':key,'state':'pending','created_at':previous['created_at'] if previous else time.time(),
                       'updated_at':time.time(),'due_at':datetime.fromisoformat(data['run_at']).timestamp() if data['run_at'] else time.time()}
                save(value);return value
            if action == 'command-cancel' and len(args)==2:
                value=read(args[1])
                if value['state']=='cancelled': return value
                if value['state']!='pending': raise QueueError('Only pending commands may be cancelled',409)
                value.update(state='cancelled',updated_at=time.time());save(value);return value
            raise QueueError('Invalid command operation')
    except QueueError as exc: return {'queue_error':str(exc),'status_code':exc.status}


def service_active(key):
    result=subprocess.run(['/usr/bin/systemctl','is-active','mykos-command@'+identifier(key)+'.service'],
                          capture_output=True,text=True,timeout=10)
    return result.stdout.strip() in ('active','activating','deactivating','reloading')


def collect():
    with locked():
        values=[read(p.stem) for p in sorted(ROOT.glob('*.json'))]
        for value in values:
            if value['state'] in ACTIVE and not service_active(value['id']) and time.time()-value['updated_at']>90:
                value.update(state='interrupted',updated_at=time.time(),error='Worker stopped; command will not be retried automatically')
                save(value)
        slots=max(0,4-sum(v['state'] in ACTIVE for v in values))
        busy={v['target'] for v in values if v['state'] in ACTIVE}
        for value in sorted(values,key=lambda v:v['due_at']):
            if slots==0: break
            if value['state']!='pending' or value['due_at']>time.time() or value['target'] in busy: continue
            busy.add(value['target'])
            value.update(state='starting',updated_at=time.time());save(value);slots-=1
            result=subprocess.run(['/usr/bin/systemctl','start','--no-block','mykos-command@'+value['id']+'.service'],
                                  capture_output=True,text=True,timeout=10)
            if result.returncode:
                value.update(state='failed',updated_at=time.time(),error='Could not start command worker');save(value)


def shell(args, timeout):
    """Drain output continuously while retaining a bounded private result."""
    proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True,
                          cwd='/',env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
    output=bytearray();truncated=False;timed_out=False
    deadline=time.monotonic()+timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout,selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic()>=deadline:
                    timed_out=True;break
                for key,_ in selector.select(min(.2,max(.01,deadline-time.monotonic()))):
                    chunk=os.read(key.fileobj.fileno(),65536)
                    if not chunk: selector.unregister(key.fileobj);continue
                    remaining=LIMIT-len(output);output.extend(chunk[:remaining])
                    truncated |= len(chunk)>remaining
            if not timed_out:
                try: proc.wait(timeout=max(.01,deadline-time.monotonic()))
                except subprocess.TimeoutExpired: timed_out=True
    finally:
        if timed_out or proc.poll() is None:
            try: os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        proc.wait();proc.stdout.close()
    return dict(exit_code=proc.returncode,output=output.decode('utf-8','replace'),truncated=truncated,timed_out=timed_out)


class GuestAgent:
    """Private Unix QGA connection; no guest/network credentials or exposed port."""
    def __init__(self,value,allow_image_builder=False,timeout=10):
        from mykos_vm import inspect
        state=inspect(value['name']);labels=state.get('Config',{}).get('Labels') or {}
        pid=state.get('State',{}).get('Pid',0)
        role = 'template' if allow_image_builder else 'instance'
        if allow_image_builder and (value.get('role') != 'template' or value.get('ready') or not value.get('os', '').startswith(('win','wns','ubuntu')) or value.get('name') != value.get('os')):
            raise QueueError('Select an unfinished Windows or Ubuntu image')
        if (not state.get('State',{}).get('Running') or not isinstance(pid,int) or pid<=0
            or labels.get('io.mykos.vm.os')!=value['os'] or labels.get('io.mykos.vm.role')!=role):
            raise QueueError('Windows guest must be a running managed instance')
        self.socket=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.socket.settimeout(timeout);self.buffer=b''
        try:
            self.socket.connect('/proc/%d/root/dev/shm/mykos-qga.sock'%pid)
            nonce=secrets.randbits(52)
            self.socket.sendall(b'\xff'+json.dumps({'execute':'guest-sync-delimited','arguments':{'id':nonce}}).encode()+b'\n')
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                if self.receive().get('return')==nonce: break
            else: raise QueueError('Windows guest agent did not synchronize')
        except BaseException:
            self.socket.close()
            raise QueueError('Guest agent unavailable; enable QGA and install/start qemu-guest-agent in the guest')

    def receive(self):
        while True:
            if b'\n' in self.buffer:
                line,self.buffer=self.buffer.split(b'\n',1)
                if b'\xff' in line: line=line.rsplit(b'\xff',1)[1]
                try: return json.loads(line)
                except ValueError: continue
            chunk=self.socket.recv(65536)
            if not chunk: raise QueueError('Windows guest agent disconnected')
            self.buffer+=chunk
            if len(self.buffer)>4*1024*1024: raise QueueError('Guest agent response too large')

    def call(self,command,arguments=None):
        self.socket.sendall(json.dumps({'execute':command,'arguments':arguments or {}}).encode()+b'\n')
        result=self.receive()
        if 'error' in result: raise QueueError('Guest agent rejected '+command+': '+str(result['error'].get('desc','Unknown error')))
        return result['return']

    def close(self): self.socket.close()


def powershell(value,script,timeout,*,allow_image_builder=False):
    agent=GuestAgent(value,allow_image_builder=True) if allow_image_builder else GuestAgent(value)
    try:
        encoded=base64.b64encode(script.encode('utf-16le')).decode()
        pid=agent.call('guest-exec',{'path':r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
                                  'arg':['-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                                  'capture-output':True})['pid']
        deadline=time.monotonic()+timeout
        while True:
            status=agent.call('guest-exec-status',{'pid':pid})
            if status.get('exited'):
                out=b'\n'.join(base64.b64decode(status.get(k,'')) for k in ('out-data','err-data'))
                return dict(exit_code=status.get('exitcode',-1),output=out[:LIMIT].decode('utf-8','replace'),
                            truncated=len(out)>LIMIT or status.get('out-truncated',False) or status.get('err-truncated',False),timed_out=False)
            if time.monotonic()>=deadline:
                agent.call('guest-exec',{'path':r'C:\Windows\System32\taskkill.exe','arg':['/PID',str(pid),'/T','/F']})
                return dict(exit_code=None,output='',truncated=False,timed_out=True)
            time.sleep(.25)
    finally: agent.close()


def execute(value):
    from mykos_runtime import PODMAN, target
    if value['target']=='host':
        return 'shell',shell(['/bin/bash','--noprofile','--norc','-c',value['command']],value['timeout_seconds'])
    from mykos_vm import record_path,read as vm_read,VERSIONS
    code=value['target']
    if record_path(code).exists():
        vm=vm_read(code)
        if VERSIONS[vm['os']][0]!='windows': raise QueueError('Scheduled commands for macOS/Ubuntu VMs are not supported yet')
        return 'powershell',powershell(vm,value['command'],value['timeout_seconds'])
    result=shell([PODMAN,'exec','--user','0',target(code),'/usr/bin/timeout','--signal=TERM','--kill-after=5s',str(value['timeout_seconds']),'/bin/bash','--noprofile','--norc','-c',value['command']],value['timeout_seconds']+10)
    if result['exit_code'] in (124,137): result['timed_out']=True
    return 'shell',result


def work(key):
    with locked():
        value=read(key)
        if value['state']!='starting': return
        value.update(state='running',started_at=time.time(),updated_at=time.time());save(value)
    try:
        interpreter,result=execute(value)
        value.update(result,interpreter=interpreter,state='timed_out' if result['timed_out'] else ('succeeded' if result['exit_code']==0 else 'failed'))
    except Exception as exc:
        value.update(state='failed',error=str(exc)[:2000])
    from mykos_refresh import best_effort
    best_effort(value['target'],value)
    with locked():
        value.update(finished_at=time.time(),updated_at=time.time());save(value)
