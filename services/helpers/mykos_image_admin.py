"""Admin image catalog and guarded asynchronous removal; no forced image deletion."""
import fcntl,json,os,shutil,subprocess,time,re
from datetime import datetime,timezone
from pathlib import Path
import mykos_vm as vm
from mykos_runtime import NATIVE_OS_NAMES,PODMAN,run

STATE=Path('/var/lib/mykos/image-admin')

def selection(value):
    if value not in (*NATIVE_OS_NAMES,*vm.VERSIONS):raise ValueError('Unsupported image OS')
    return value

def reference(os_name):
    selection(os_name)
    return vm.template_image(os_name) if os_name in vm.VERSIONS else 'localhost/'+os_name+':latest'

def job(os_name):
    selection(os_name);path=STATE/(os_name+'.json')
    if path.is_symlink():raise ValueError('Unsafe image job')
    value=json.loads(path.read_text()) if path.exists() else {'os':os_name,'state':'idle'}
    if value.get('state') in ('queued','deleting','resetting') and time.time()-value.get('updated_at',0)>(7230 if value.get('operation')=='reset' else 930):
        value.update(state='failed',error='Removal worker timed out; inspect its journal before retrying')
        save(os_name,value)
    return value

def save(os_name,value):
    vm.safe_dir(STATE)
    from mykos_image_build import write
    write(STATE/(selection(os_name)+'.json'),json.dumps(value)+'\n')

def produced_at(value):
    try:
        stamp=str(value).replace('Z','+00:00')
        # Podman emits nanoseconds; Python 3.9 accepts only 3/6 fractional digits.
        stamp=re.sub(r'\.(\d+)(?=[+-]|$)',lambda m:'.'+(m[1]+'000000')[:6],stamp)
        created=datetime.fromisoformat(stamp)
        if created.tzinfo is None:created=created.replace(tzinfo=timezone.utc)
        return int(created.timestamp())
    except (ValueError,TypeError,OverflowError):return None


def catalog():
    names=set(run([PODMAN,'images','--format','{{.Repository}}:{{.Tag}}'],timeout=20).splitlines())
    selected={reference(k):k for k in (*NATIVE_OS_NAMES,*vm.VERSIONS) if reference(k) in names}
    if not selected:return []
    rows=json.loads(run([PODMAN,'image','inspect',*selected],timeout=30));result=[]
    by_tag={tag:row for row in rows for tag in row.get('RepoTags',[])}
    for tag,key in selected.items():
        row=by_tag.get(tag)
        if row is None:continue
        labels=row.get('Labels') or row.get('Config',{}).get('Labels') or {}
        pending=job(key)
        result.append({'os':key,'image':tag,'image_id':row['Id'],'size_bytes':row.get('Size',0),'created_at':produced_at(row.get('Created')),
                       'version':key[5:] if key.startswith('mssql') else labels.get('io.mykos.os.version') or key.lstrip('abcdefghijklmnopqrstuvwxyz'),
                       'delete_state':pending.get('state') if pending.get('operation')!='reset' else 'idle','delete_error':pending.get('error','') if pending.get('operation')!='reset' else '',
                       'reset_state':pending.get('state') if pending.get('operation')=='reset' else 'idle','reset_error':pending.get('error','') if pending.get('operation')=='reset' else ''})
    return result

def queue(os_name,confirmation):
    selection(os_name)
    if confirmation!=os_name:raise ValueError('Confirm the exact image OS')
    vm.safe_dir(STATE)
    lock=STATE/(os_name+'.lock')
    with os.fdopen(os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600),'w') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Image removal already active')
        if job(os_name).get('state') in ('queued','deleting','resetting'):raise ValueError('Image removal already active')
        item=next((i for i in catalog() if i['os']==os_name),None)
        if item is None:raise ValueError('Prepared image not found')
        value={'os':os_name,'state':'queued','image_id':item['image_id'],'updated_at':int(time.time())}
        save(os_name,value)
        try:
            run(['/usr/bin/systemd-run','--unit=mykos-image-remove-'+os_name+'-'+str(time.time_ns()),
                 '--property=RuntimeMaxSec=900','--property=UMask=0077',
                 '/usr/local/sbin/mykos-image-admin',os_name,'--worker'],timeout=15)
        except Exception:
            value.update(state='failed',error='Could not start image removal worker');save(os_name,value);raise
        return value

def remove(os_name,expected_id):
    selection(os_name)
    with vm.locked():
        image=json.loads(run([PODMAN,'image','inspect',reference(os_name)],timeout=20))[0]
        if image['Id']!=expected_id:raise ValueError('Image changed since removal was requested')
        if set(image.get('RepoTags') or [])-{reference(os_name)}:raise ValueError('Image has additional tags; shared aliases were retained')
        import mykos_prune
        for process in mykos_prune.process_table().values():
            args=process['args']
            if any(Path(arg).name=='podman' for arg in args[:2]) and any(arg in ('build','commit') for arg in args[1:3]) and any(arg in (os_name,os_name+':latest',reference(os_name)) for arg in args):
                raise ValueError('Native image build or commit is still active')
        if any(any('/var/tmp/'+os_name+'.ks' in arg for arg in proc['args']) for proc in mykos_prune.builders().values()):
            raise ValueError('Native image build is still active')
        active=subprocess.run(['systemctl','is-active','mykos-image-build@'+os_name+'.service'],capture_output=True,text=True,timeout=10)
        if active.stdout.strip() in ('active','activating','deactivating','reloading'):raise ValueError('Image build is still active')
        from mykos_containers import all_containers,unshared
        containers=all_containers();base=None
        ident=lambda x:str(x or '').removeprefix('sha256:')
        for container in containers:
            name=container.get('Name','').lstrip('/')
            if name==os_name:
                if ident(container.get('Image'))!=ident(expected_id):
                    labels=container.get('Config',{}).get('Labels') or {}
                    # Native commit retags the resulting child image; its running
                    # generic base remains attached to the original parent.
                    committed_native=(os_name not in vm.VERSIONS and
                        labels.get('io.mykos.os')==os_name and
                        container.get('ImageName')==reference(os_name) and
                        bool(container.get('Image')) and
                        ident(image.get('Parent'))==ident(container.get('Image')))
                    if not committed_native:raise ValueError('Generic base points to another image')
                if os_name in vm.VERSIONS:
                    labels=container.get('Config',{}).get('Labels') or {}
                    if labels.get('io.mykos.vm.os')!=os_name or labels.get('io.mykos.vm.role')!='template':raise ValueError('Generic base identity changed')
                base=container
            elif ident(container.get('Image'))==ident(expected_id):raise ValueError('Image is used by container '+name)
        # Snapshot and instance metadata may retain a dependency even without a live Podman container.
        import mykos_snapshots as snapshots
        for snap in snapshots.list_snapshots():
            if snap.get('os')==os_name or ident(snap.get('image'))==ident(expected_id):raise ValueError('Image is referenced by an intentional snapshot')
        for path in vm.ROOT.glob('[0-9]*.json'):
            record=json.loads(path.read_text())
            if record.get('os')==os_name:raise ValueError('Image is referenced by a saved instance')
        private=None
        if os_name in vm.VERSIONS:
            value=vm.read(os_name)
            if value.get('role')!='template' or not value.get('ready') or value.get('pending_commit'):raise ValueError('Only a completed captured image can be removed here')
            private=vm.storage(os_name);vm.safe_dir(private)
            unshared([private],containers,base['Id'] if base else '')
        if base:
            if base.get('State',{}).get('Running'):
                if os_name in vm.VERSIONS:vm.stop_container(os_name)
                else:run([PODMAN,'stop','--time','30',base['Id']],timeout=45)
            # Native generic bases use --rm and may have disappeared after a clean stop.
            if any(c['Id']==base['Id'] for c in all_containers()):run([PODMAN,'rm',base['Id']],timeout=300)
        # Never --force: Podman remains the final dependency check for other tags/layers.
        run([PODMAN,'rmi',expected_id],timeout=300)
        if private:
            unshared([private],all_containers(),'')
            shutil.rmtree(private);vm.record_path(os_name).unlink()
            build=Path('/var/lib/mykos/image-builds')/os_name
            if build.exists():vm.safe_dir(build);shutil.rmtree(build)
        return {'os':os_name,'state':'deleted','snapshots_retained':True,'instances_retained':True}

def worker(os_name):
    value=job(os_name)
    if value.get('state')!='queued':raise ValueError('Image removal is not queued')
    try:
        if value.get('operation')=='reset':
            from mykos_base_session import acquire
            deadline=time.monotonic()+10
            while True:
                try:session=acquire(os_name,operation=True);break
                except ValueError:
                    if time.monotonic()>=deadline:raise
                    time.sleep(.1)
            try:
                value.update(state='resetting');save(os_name,value)
                result=reset_base(os_name,None if value.get('follow_capture') else value['image_id'])
            finally:os.close(session)
        else:
            value.update(state='deleting');save(os_name,value)
            result=remove(os_name,value['image_id'])
        value.update(result);value.pop('error',None)
    except Exception as error:
        value.update(state='failed',error=str(error));save(os_name,value);raise
    save(os_name,value);return value


def base_container(os_name):
    """Resolve only the named managed base, never an ID-specific instance."""
    selection(os_name)
    state=vm.inspect(os_name)
    labels=state.get('Config',{}).get('Labels') or {}
    if state.get('Name','').lstrip('/')!=os_name:
        raise ValueError('Generic base identity mismatch')
    if not state.get('State',{}).get('Running') or state['State'].get('Paused'):
        raise ValueError('Start the generic base before connecting or capturing')
    if os_name in vm.VERSIONS:
        value=vm.read(os_name)
        if (value.get('name')!=os_name or value.get('role')!='template' or not value.get('ready') or value.get('pending_commit') or
            labels.get('io.mykos.vm.os')!=os_name or labels.get('io.mykos.vm.role')!='template'):
            raise ValueError('Select a completed generic VM base')
    elif labels.get('io.mykos.os')!=os_name or state.get('ImageName')!=reference(os_name):
        raise ValueError('Generic native base identity mismatch')
    return state


def capture(os_name,confirmation):
    selection(os_name)
    if confirmation!=os_name:raise ValueError('Confirm the exact image OS')
    if job(os_name).get('state') in ('queued','deleting','resetting'):raise ValueError('An image operation is in progress')
    if os_name in vm.VERSIONS:
        value=vm.read(os_name)
        if value.get('ready'):base_container(os_name)
        return vm.prepare(os_name,replace=bool(value.get('ready')),keep_running=True,embed_disk=True)
    with vm.locked():
        base=base_container(os_name)
        if job(os_name).get('state') in ('queued','deleting','resetting'):
            raise ValueError('Image removal is active')
        import mykos_prune
        if any(any('/var/tmp/'+os_name+'.ks' in arg for arg in proc['args']) for proc in mykos_prune.builders().values()):
            raise ValueError('Native image build is still active')
        for process in mykos_prune.process_table().values():
            args=process['args']
            if any(Path(arg).name=='podman' for arg in args[:2]) and any(arg in ('build','commit') for arg in args[1:3]) and any(arg in (os_name,os_name+':latest',reference(os_name)) for arg in args):
                raise ValueError('Native image build or capture is still active')
        # Commit pauses filesystem writes briefly and leaves this base running.
        image_id=vm.run([PODMAN,'commit','--pause=true',base['Id'],reference(os_name)],timeout=3600)
        return {'os':os_name,'image':reference(os_name),'image_id':image_id,'base_running':True}


def queue_reset(os_name,confirmation,*,closing_session=False):
    selection(os_name)
    if confirmation!=os_name:raise ValueError('Confirm the exact image OS')
    vm.safe_dir(STATE)
    with os.fdopen(os.open(STATE/(os_name+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600),'w') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        pending=job(os_name)
        if pending.get('state') in ('queued','deleting','resetting'):raise ValueError('An image operation is already active')
        if not closing_session:
            from mykos_base_session import acquire
            session=acquire(os_name);os.close(session)
        item=next((item for item in catalog() if item['os']==os_name),None)
        if item is None:raise ValueError('Captured image not found')
        value={'os':os_name,'operation':'reset','state':'queued','image_id':item['image_id'],'follow_capture':closing_session,'updated_at':int(time.time())}
        save(os_name,value)
        try:
            run(['/usr/bin/systemd-run','--unit=mykos-image-reset-'+os_name+'-'+str(time.time_ns()),
                 '--property=RuntimeMaxSec=7200','--property=UMask=0077',
                 '/usr/local/sbin/mykos-image-admin',os_name,'--worker'],timeout=15)
        except Exception:
            value.update(state='failed',error='Could not start base reset worker');save(os_name,value);raise
        return value


def launch_native(os_name):
    import importlib.machinery,importlib.util
    loader=importlib.machinery.SourceFileLoader('mykos_native_starter',str(Path(__file__).with_name('mykos-native-image-start')))
    spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    return module.launch(os_name)


def reset_base(os_name,expected_id=None):
    selection(os_name)
    with vm.locked():
        image=json.loads(run([PODMAN,'image','inspect',reference(os_name)],timeout=20))[0]
        if expected_id and image['Id']!=expected_id:raise ValueError('Captured image changed since reset was requested')
        import mykos_prune
        if any(any('/var/tmp/'+os_name+'.ks' in arg for arg in proc['args']) for proc in mykos_prune.builders().values()):raise ValueError('Image build is still active')
        for proc in mykos_prune.process_table().values():
            args=proc['args']
            if any(Path(arg).name=='podman' for arg in args[:2]) and any(arg in ('build','commit') for arg in args[1:3]) and any(arg in (os_name,os_name+':latest',reference(os_name)) for arg in args):raise ValueError('Image build or commit is still active')
        active=subprocess.run(['systemctl','is-active','mykos-image-build@'+os_name+'.service'],capture_output=True,text=True,timeout=10)
        if active.stdout.strip() in ('active','activating','deactivating','reloading'):raise ValueError('Image build is still active')
        from mykos_containers import all_containers,unshared
        items=all_containers();base=next((c for c in items if c.get('Name','').lstrip('/')==os_name),None)
        private=None
        if os_name in vm.VERSIONS:
            value=vm.read(os_name)
            if value.get('role')!='template' or value.get('name')!=os_name or not value.get('ready') or value.get('pending_commit'):
                raise ValueError('Finish the image capture before resetting its base')
            if str(value.get('image','')).removeprefix('sha256:')!=image['Id'].removeprefix('sha256:'):raise ValueError('VM image and capture identity do not match')
            golden=vm.storage(os_name)/'golden';vm.restore_manifest(value,golden)
            private=vm.storage(os_name)/'storage';vm.safe_dir(private)
            unshared([private],items,base['Id'] if base else '')
        if base:
            labels=base.get('Config',{}).get('Labels') or {}
            if os_name in vm.VERSIONS:
                if labels.get('io.mykos.vm.os')!=os_name or labels.get('io.mykos.vm.role')!='template':raise ValueError('Generic base identity mismatch')
            elif labels.get('io.mykos.os')!=os_name or base.get('ImageName')!=reference(os_name):raise ValueError('Generic native base identity mismatch')
            if base.get('State',{}).get('Paused'):run([PODMAN,'unpause',base['Id']],timeout=20)
            if base.get('State',{}).get('Running'):
                if private:vm.stop_container(base['Id'],grace_seconds=2)
                else:run([PODMAN,'stop','--time','30',base['Id']],timeout=45)
            if any(c['Id']==base['Id'] for c in all_containers()):run([PODMAN,'rm',base['Id']],timeout=300)
        if private:
            # Keep the previous private working tree until the captured base restarts successfully.
            previous=private.with_name('.reset-before-'+os.urandom(8).hex())
            os.replace(private,previous)
            try:
                vm.tree_copy(golden,private);vm.restore_manifest(value,private)
                vm.launch(os_name,value)
                vm.wait_for_restore(value,private)
            except BaseException:
                # Do not swap a disk back under a partially started QEMU process.
                try:
                    remaining=vm.inspect(os_name)
                    if remaining.get('State',{}).get('Running'):
                        raise RuntimeError('Reset base did not finish; both private disk trees retained at '+str(previous))
                except RuntimeError as error:
                    if 'both private' in str(error):raise
                if private.exists():shutil.rmtree(private)
                os.replace(previous,private)
                raise
            shutil.rmtree(previous)
        else:launch_native(os_name)
        return {'os':os_name,'state':'reset','base_running':True,'instances_retained':True,'snapshots_retained':True}
