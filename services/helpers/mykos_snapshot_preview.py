"""Leased, isolated snapshot previews. Original containers/snapshots stay immutable."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import subprocess
import time

import mykos_vm as vm
import mykos_snapshots as snapshots

STATE=Path('/var/lib/mykos/snapshot-previews')
LEASE=90
LIFETIME=1800
MAX_PREVIEWS=4


def token(value):
    if not re.fullmatch(r'[0-9a-f]{32}',value):raise ValueError('Invalid preview')
    return value


def actor(value):
    if not re.fullmatch(r'[0-9a-f]{64}',value):raise ValueError('Invalid preview session')
    return value


def directory(value):
    token(value);vm.safe_dir(STATE)
    p=STATE/value
    if p.is_symlink():raise ValueError('Unsafe preview directory')
    return p


def write(path,value):
    from mykos_image_build import write as atomic
    atomic(path,json.dumps(value)+'\n')


def read(value,owner=None):
    p=directory(value)/'status.json'
    if p.is_symlink() or not p.is_file():raise ValueError('Preview not found')
    if p.stat().st_uid!=os.geteuid() or p.stat().st_mode&0o077:raise ValueError('Unsafe preview metadata')
    data=json.loads(p.read_text())
    if data.get('token')!=value or (owner is not None and not secrets.compare_digest(data.get('owner',''),actor(owner))):
        raise ValueError('Preview not found')
    return data


def public(data):
    return {k:data[k] for k in ('token','state','code','snapshot','os','mode','expires','error') if k in data}


@contextlib.contextmanager
def locked(value):
    p=directory(value)
    with (p/'operation.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        yield


def heartbeat(value,owner):
    data=read(value,owner)
    if expired(data):raise ValueError('Preview expired')
    write(directory(value)/'lease.json',{'until':min(time.time()+LEASE,data['expires'])})
    return public(data)


def create(shop,identifier,owner):
    actor(owner);vm.safe_dir(STATE)
    with (STATE/'registry.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        active=[]
        for p in STATE.iterdir():
            if not re.fullmatch(r'[0-9a-f]{32}',p.name) or not p.is_dir():continue
            try:
                data=read(p.name)
                if data['state'] not in ('closed','failed'):
                    active.append(data)
                    if data['owner']==owner and data['code']==shop and data['snapshot']==identifier:
                        return heartbeat(data['token'],owner)
            except (ValueError,OSError):continue
        if len(active)>=MAX_PREVIEWS:raise ValueError('Close an existing snapshot preview before opening another')
        with vm.locked():
            _,saved=snapshots.read_snapshot(shop,identifier)
            mode='memory' if saved['capture_mode']=='memory' else 'filesystem' if saved['capture_mode']=='criu-memory' else None
            if mode is None:raise ValueError('Unsupported snapshot preview type')
            value=secrets.token_hex(16);p=directory(value);p.mkdir(mode=0o700)
            key='preview-'+value
            if vm.record_path(key).exists() or vm.storage(key).exists():raise ValueError('Preview storage already exists')
            data={'token':value,'owner':owner,'code':shop,'snapshot':identifier,'os':saved['os'],
                  'mode':mode,'state':'queued','key':key,'name':'mykos-preview-'+value,
                  'created':int(time.time()),'expires':int(time.time())+LIFETIME}
            write(p/'status.json',data);write(p/'lease.json',{'until':time.time()+LEASE})
        try:
            subprocess.run(['systemd-run','--unit=mykos-snapshot-preview-'+value,'--collect',
                '--property=RuntimeMaxSec=1800','--property=TimeoutStopSec=45','--property=UMask=0077',
                '/usr/local/sbin/mykos-snapshot-preview','worker',value],capture_output=True,text=True,check=True,timeout=15)
        except BaseException:
            data['state']='failed';data['error']='Could not start preview worker';write(p/'status.json',data);raise
        return public(data)


def verify_container(data):
    state=vm.inspect(data['name']);labels=state.get('Config',{}).get('Labels') or {}
    if labels.get('io.mykos.snapshot.preview')!=data['token'] or state.get('Name','').lstrip('/')!=data['name']:
        raise ValueError('Preview container identity changed')
    if data.get('container_id') and state['Id']!=data['container_id']:raise ValueError('Preview container was replaced')
    return state


def expired(data):
    p=directory(data['token'])
    if (p/'closed').exists() or time.time()>=data['expires']:return True
    try:return time.time()>json.loads((p/'lease.json').read_text())['until']
    except (OSError,ValueError,KeyError):return True


def target(value,owner,kind='desktop'):
    data=read(value,owner)
    if data['state']!='ready' or expired(data):raise ValueError('Preview is not available')
    if (kind=='desktop')!=(data['mode']=='memory'):raise ValueError('Use the matching preview console')
    state=verify_container(data)
    if not state.get('State',{}).get('Running'):raise ValueError('Preview has stopped')
    if kind=='shell':return {'name':data['name']}
    vm_value=vm.read(data['key'])
    mappings=state.get('NetworkSettings',{}).get('Ports',{}).get('8006/tcp') or []
    if not any(m.get('HostIp')=='127.0.0.1' and int(m.get('HostPort',0))==vm_value['console_port'] for m in mappings):
        raise ValueError('Preview console mapping changed')
    return {'host':'127.0.0.1','port':vm_value['console_port']}


def isolate_vm(data):
    # This is inside the new wrapper's netns, before saved guest CPUs resume.
    script='''set -eu
for cmd in iptables ip6tables; do
 "$cmd" -w 5 -I FORWARD 1 -j DROP
 "$cmd" -w 5 -N MYKOS_PREVIEW_OUT
 "$cmd" -w 5 -A MYKOS_PREVIEW_OUT -o lo -j ACCEPT
 "$cmd" -w 5 -A MYKOS_PREVIEW_OUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
 "$cmd" -w 5 -A MYKOS_PREVIEW_OUT -j REJECT
 "$cmd" -w 5 -I OUTPUT 1 -j MYKOS_PREVIEW_OUT
done
'''
    vm.run([vm.PODMAN,'exec',data['name'],'/bin/bash','-c',script],timeout=30)


def start_vm(data,folder,saved):
    key=data['key'];private=vm.storage(key);vm.safe_dir(private)
    value={**saved['configuration'],'name':data['name'],'role':'preview','preview_token':data['token'],
           'image':saved['image'],'image_name':saved['image_ref'],'cpu_env':saved['configuration'].get('cpu_env',{}),
           'host':'127.0.0.1','bind':'127.0.0.1','ready':True,'capture_mode':'memory'}
    value['console_port'],value['desktop_port']=vm.allocate('127.0.0.1')
    vm.restore_manifest(value,folder/'storage')
    vm.tree_copy(folder/'storage',private/'storage',reflink='always')
    request=private/'storage/.mykos-checkpoint/restore.request'
    request.write_text('restore once\n');request.chmod(0o600)
    if vm.VERSIONS[value['os']][0]=='windows':
        (private/'windows.env').write_text('USERNAME=\nPASSWORD=\n');(private/'windows.env').chmod(0o600)
    vm.save(key,value);vm.launch(key,value)
    data['container_id']=verify_container(data)['Id'];write(directory(data['token'])/'status.json',data)
    isolate_vm(data)
    if expired(data):raise ValueError('Preview was closed')
    vm.wait_for_restore(value,private/'storage')


def start_filesystem(data,folder,saved):
    private=vm.storage(data['key']);vm.safe_dir(private)
    args=[vm.PODMAN,'create','--name',data['name'],'--hostname','snapshot-preview',
          '--network=none','--restart=no','--image-volume=ignore','--memory=1g','--cpus=1','--pids-limit=256','--pull=never',
          '--security-opt=no-new-privileges','--security-opt=label=disable',
          '--label','io.mykos.snapshot.preview='+data['token'],'--entrypoint','/usr/bin/sleep']
    for index,mount in enumerate(saved.get('mounts',[])):
        destination=mount.get('destination','');parts=PurePosixPath(destination)
        if not parts.is_absolute() or '..' in parts.parts or ',' in destination or '\n' in destination or any(destination==x or destination.startswith(x+'/') for x in ('/','/proc','/sys','/dev')):
            raise ValueError('Unsupported snapshot mount destination')
        source=folder/'mounts'/str(index)/'data';dest=private/('mount-'+str(index))
        if source.is_symlink() or not source.exists():raise ValueError('Snapshot mounted data is missing')
        vm.run(['/usr/bin/cp','-a','--reflink=always','--sparse=auto',str(source),str(dest)],timeout=60)
        args+=['--mount','type=bind,source='+str(dest)+',destination='+destination+(',ro=true' if not mount.get('rw') else '')]
    vm.run(args+[saved['image'],'2147483647'],timeout=60)
    state=verify_container(data);data['container_id']=state['Id'];write(directory(data['token'])/'status.json',data)
    vm.run([vm.PODMAN,'mount',data['name']],timeout=30)
    try:
        state=verify_container(data)
        if state.get('GraphDriver',{}).get('Name')!='overlay':raise ValueError('Filesystem previews require overlay storage')
        upper=Path(state['GraphDriver']['Data']['UpperDir'])
    finally:vm.run([vm.PODMAN,'unmount',data['name']],timeout=30)
    source=folder/'rootfs-upper'
    if not source.is_dir() or source.is_symlink():raise ValueError('Snapshot filesystem is missing')
    vm.run(['/usr/bin/rsync','-aHAX','--numeric-ids','--delete',str(source)+'/',str(upper)+'/'],timeout=900)
    if expired(data):raise ValueError('Preview was closed')
    vm.run([vm.PODMAN,'start',data['name']],timeout=60)
    if not verify_container(data)['State']['Running']:raise ValueError('Filesystem preview did not start')


def cleanup(data):
    record=vm.record_path(data['key'])
    if record.exists():
        value=vm.read(data['key'])
        if value.get('role')!='preview' or value.get('preview_token')!=data['token']:raise ValueError('Preview registry identity changed')
    try:state=verify_container(data)
    except RuntimeError:state=None
    if state:
        if state.get('State',{}).get('Running'):vm.stop_container(data['name'],grace_seconds=2)
        vm.run([vm.PODMAN,'rm',state['Id']],timeout=60)
    from mykos_containers import unshared,all_containers
    private=vm.storage(data['key']);unshared([private],all_containers(),'')
    if private.exists():
        if private.is_symlink():raise ValueError('Unsafe preview storage')
        shutil.rmtree(private)
    if record.exists():
        record.unlink()


def worker(value):
    def terminate(*_):raise RuntimeError('Preview worker stopped')
    signal.signal(signal.SIGTERM,terminate)
    with locked(value):
        data=read(value)
        try:
            data['state']='starting';write(directory(value)/'status.json',data)
            with vm.locked():
                if expired(data):raise ValueError('Preview was closed')
                folder,saved=snapshots.read_snapshot(data['code'],data['snapshot'])
                if data['mode']=='memory':start_vm(data,folder,saved)
                else:start_filesystem(data,folder,saved)
            data['state']='ready';write(directory(value)/'status.json',data)
        except BaseException as error:
            try:
                with vm.locked():cleanup(data)
            except Exception as failure:
                data.update(state='cleanup_failed',error=str(failure));write(directory(value)/'status.json',data);raise
            data.update(state='failed',error=str(error));write(directory(value)/'status.json',data)


def close(value,owner):
    data=read(value,owner);(directory(value)/'closed').touch(mode=0o600)
    # The GC also handles interrupted workers and lost browsers.
    subprocess.run(['systemctl','start','--no-block','mykos-snapshot-preview-gc.service'],check=True,capture_output=True,timeout=10)
    return {'state':'closing'}


def collect():
    vm.safe_dir(STATE)
    for p in list(STATE.iterdir()):
        if not re.fullmatch(r'[0-9a-f]{32}',p.name) or not p.is_dir():continue
        try:
            data=read(p.name)
            if not expired(data):continue
            unit='mykos-snapshot-preview-'+p.name+'.service'
            subprocess.run(['systemctl','stop',unit],capture_output=True,timeout=50)
            with locked(p.name),vm.locked():
                current=read(p.name)
                if expired(current):cleanup(current);shutil.rmtree(p)
        except Exception as error:print('Preview cleanup deferred: '+p.name+': '+str(error),flush=True)
