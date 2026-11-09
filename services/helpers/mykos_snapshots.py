"""Private, atomic snapshots; intentional snapshots are never auto-pruned."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import mykos_vm as vm
from mykos_runtime import shopcode, target
from mykos_criu_cgroups import capture_context as native_cgroup_context
from mykos_criu_cgroups import restore_context as restore_cgroup_context

from mykos_criu_binary import binary as criu_binary


def allocated_bytes(folder):
    total=0
    for directory, dirs, files in os.walk(folder, followlinks=False):
        dirs[:]=[name for name in dirs if not (Path(directory)/name).is_symlink()]
        for name in files:
            path=Path(directory)/name
            if not path.is_symlink() and path.is_file():
                total+=path.stat().st_blocks*512
    return total


def public_metadata(value):
    return {key:value[key] for key in ('format','id','code','name','os','created_at','capture_mode','image','path','source_running','size_bytes','deletion_pending') if key in value}


def root():
    return vm.storage_root()/'snapshots'


def initialize():
    vm.safe_dir(vm.storage_root()); vm.safe_dir(root())
    return str(root())


def list_snapshots(shop=None):
    if shop is None:
        if not root().exists():return []
        vm.safe_dir(root())
        return [item for folder in sorted(root().iterdir()) if folder.is_dir() and not folder.is_symlink() and folder.name.isascii() and folder.name.isdigit() and len(folder.name)==8 for item in list_snapshots(folder.name)]
    shopcode(shop)
    folder = root()/shop
    if not folder.exists(): return []
    vm.safe_dir(folder)
    result=[]
    deleting={}
    for marker in folder.glob('.deleting-*.json'):
        identifier=marker.name[len('.deleting-'):-len('.json')]
        snapshot_id(identifier)
        _,metadata=read_snapshot(shop,identifier,allow_deleting=True)
        deleting[identifier]=metadata
        result.append(public_metadata(dict(metadata,deletion_pending=True)))
    for entry in sorted(folder.iterdir(), reverse=True):
        if entry.name in deleting:continue
        if not re.fullmatch(r'[0-9]{10,}-[0-9a-f]{12}',entry.name) or entry.is_symlink() or not entry.is_dir(): continue
        path=entry/'metadata.json'
        if path.is_symlink() or not path.is_file(): continue
        _,metadata=read_snapshot(shop,entry.name)
        if 'size_bytes' not in metadata:
            metadata['size_bytes']=allocated_bytes(entry)
            descriptor, temporary=tempfile.mkstemp(prefix='.metadata-',dir=entry)
            try:
                with os.fdopen(descriptor,'w') as out: json.dump(metadata,out)
                os.chmod(temporary,0o600);os.replace(temporary,path)
            finally:
                if os.path.exists(temporary):os.unlink(temporary)
        result.append(public_metadata(metadata))
    return result


def capture_vm(shop):
    shopcode(shop)
    with vm.locked():
        value=vm.read(shop)
        if value.get('role')!='instance' or not value.get('ready'):
            raise ValueError('Select an existing persistent container')
        if target(shop)!=value['name']: raise ValueError('Container identity mismatch')
        state=vm.inspect(value['name'])
        if not state.get('State',{}).get('Running'): raise ValueError('Start the container before taking a snapshot')
        initialize()
        folder=root()/shop; vm.safe_dir(folder)
        identifier=str(int(time.time()))+'-'+os.urandom(6).hex()
        final=folder/identifier
        temporary=Path(tempfile.mkdtemp(prefix='.capture-',dir=str(folder)))
        reference='localhost/mykos-snapshots/'+shop+':'+identifier
        pinned=False;published=False;captured=False
        try:
            vm.capture_live(value,vm.storage(shop)/'storage',temporary/'storage')
            captured=True
            if not vm.inspect(value['name']).get('State',{}).get('Running'):
                raise RuntimeError('Container is no longer running; snapshot was not published')
            vm.run([vm.PODMAN,'tag',value['image'],reference]);pinned=True
            metadata={'format':1,'id':identifier,'code':shop,'name':value['name'],
                      'os':value['os'],'created_at':int(time.time()),'capture_mode':'memory',
                      'image':value['image'],'image_ref':reference,'configuration':value,
                      'path':str(final),'source_running':True}
            metadata['size_bytes']=allocated_bytes(temporary)
            path=temporary/'metadata.json';path.write_text(json.dumps(metadata,indent=2)+'\n');path.chmod(0o600)
            os.replace(temporary,final);published=True
            return public_metadata(metadata)
        finally:
            if not published:
                shutil.rmtree(temporary,ignore_errors=True)
                if pinned: vm.run([vm.PODMAN,'untag',value['image'],reference])
            if captured:
                # The published copy retains its RAM; the running source needs no duplicate.
                source=vm.storage(shop)/'storage/.mykos-checkpoint'
                if source.exists():
                    vm.safe_dir(source);shutil.rmtree(source)


def check_native_timezone(state, proc=Path('/proc')):
    labels=state.get('Config',{}).get('Labels') or {}
    if labels.get('io.mykos.os') not in ('debian13','archlinux','sles15sp7','amazon2023','alpine'):return
    timezone=proc/str(int(state['State']['Pid']))/'root/etc/localtime'
    if os.path.lexists(timezone):
        raise ValueError('This native snapshot profile requires its default UTC configuration; custom guest timezones are not yet qualified. No snapshot was created; the container was left running.')


def check_native_compatibility(state, proc=Path('/proc')):
    """Reject known CRIU 3.x blockers before seizing any source processes."""
    check_native_timezone(state,proc)
    version=vm.run([criu_binary(),'--version'],timeout=10)
    match=re.search(r'Version:\s*(\d+)\.(\d+)',version)
    if not match:raise ValueError('Cannot determine checkpoint tool capabilities')
    supports_pidfd=tuple(map(int,match.groups()))>=(4,1)
    init=proc/str(int(state['State']['Pid']))
    namespace=(init/'ns/pid').readlink()
    uts=(init/'ns/uts').readlink()
    for process in proc.iterdir():
        if not process.name.isdigit(): continue
        try:
            if (process/'ns/pid').readlink()!=namespace: continue
            for descriptor in (process/'fd').iterdir():
                try:target_name=str(descriptor.readlink())
                except FileNotFoundError:continue
                if target_name.startswith('/dev/input/'):
                    name=(process/'comm').read_text().strip()
                    raise ValueError('Native RAM snapshot unavailable: '+name+' holds '+target_name+
                        ', which CRIU cannot save. Release that device before retrying; the container was left running.')
                if target_name=='anon_inode:[pidfd]' and not supports_pidfd:
                    raise ValueError('Native RAM snapshot unavailable: this container uses PIDFD handles. Configure CRIU 4.1 or newer; the container was left running.')
                if target_name=='anon_inode:bpf-prog':
                    raise ValueError('Native RAM snapshot unavailable: this container holds BPF programs that this checkpoint profile cannot save. No snapshot was created; the container was left running. Use a checkpoint-compatible container profile/runtime.')
            if (process/'ns/uts').readlink()!=uts:
                name=(process/'comm').read_text().strip()
                raise ValueError('Native RAM snapshot unavailable: '+name+' uses a nested hostname namespace unsupported by this checkpoint profile. No snapshot was created; the container was left running.')
        except (FileNotFoundError,ProcessLookupError):continue


def wait_native_network(state,proc=Path('/proc'),timeout=45):
    """Let transient boot services release private netns without changing units."""
    init=proc/str(int(state['State']['Pid']))
    identity=(init/'stat').read_text().rsplit(')',1)[1].split()[19]
    pidns=(init/'ns/pid').readlink();netns=(init/'ns/net').readlink()
    deadline=time.monotonic()+timeout;announced=False
    while True:
        if (init/'stat').read_text().rsplit(')',1)[1].split()[19]!=identity:
            raise ValueError('Container changed during snapshot preflight')
        blocked=[]
        for process in proc.iterdir():
            if not process.name.isdigit():continue
            try:
                if (process/'ns/pid').readlink()==pidns and (process/'ns/net').readlink()!=netns:
                    blocked.append((process/'comm').read_text().strip())
            except (FileNotFoundError,ProcessLookupError):continue
        if not blocked:return
        if time.monotonic()>=deadline:
            raise ValueError('Native RAM snapshot unavailable: '+', '.join(sorted(set(blocked)))+
                ' still uses a nested network namespace. No snapshot was created; the container was left running.')
        if not announced:print('Snapshot: waiting for startup network isolation to settle',flush=True);announced=True
        time.sleep(.5)


def prepare_native_mounts(name, state, proc=Path('/proc')):
    """Remove unused optional hugetlbfs copies only in this container's namespaces."""
    init = proc / str(int(state['State']['Pid']))
    pid_namespace = (init/'ns/pid').readlink()
    host_pid = proc/'self/ns/pid'
    host_mount = proc/'self/ns/mnt'
    if host_pid.is_symlink() and pid_namespace == host_pid.readlink():
        raise ValueError('A host PID namespace cannot be prepared for a container snapshot')
    seen = set()
    candidates = []
    optional = {'/dev/hugepages': ('hugetlbfs', 'dev-hugepages.mount'),
                '/sys/kernel/config': ('configfs', 'sys-kernel-config.mount')}
    for process in [init, *proc.iterdir()]:
        if not process.name.isdigit():
            continue
        try:
            if (process/'ns/pid').readlink() != pid_namespace:
                continue
            namespace = (process/'ns/mnt').readlink()
            if host_mount.is_symlink() and namespace == host_mount.readlink():
                raise ValueError('A host mount namespace cannot be prepared for a container snapshot')
            if namespace in seen:
                continue
            seen.add(namespace)
            lines = (process/'mountinfo').read_text().splitlines()
            for mountpoint, (kind, unit) in optional.items():
                mounts = [line for line in lines if len(line.split()) > 5 and line.split()[4] == mountpoint]
                if not mounts:
                    continue
                if len(mounts) != 1 or ' - '+kind+' ' not in mounts[0]:
                    raise ValueError('Unexpected '+mountpoint+' mount; container left unchanged')
                if any((process/'root'/mountpoint.lstrip('/')).iterdir()):
                    raise ValueError('Optional mount files are in use: '+mountpoint+'; cannot safely capture')
                candidates.append((process, namespace, mountpoint, unit))
        except (FileNotFoundError, ProcessLookupError):
            continue
    for process, namespace, mountpoint, unit in candidates:
        try:
            if (process/'ns/pid').readlink() != pid_namespace or (process/'ns/mnt').readlink() != namespace:
                raise RuntimeError('Container namespaces changed; retry the snapshot')
        except (FileNotFoundError, ProcessLookupError):
            continue
        # A previous unmount can propagate into a service's slave namespace.
        # Read it again rather than acting on the initial, now stale inventory.
        current = [line for line in (process/'mountinfo').read_text().splitlines()
                   if len(line.split()) > 5 and line.split()[4] == mountpoint]
        if not current:
            continue
        kind = optional[mountpoint][0]
        if len(current) != 1 or ' - '+kind+' ' not in current[0]:
            raise ValueError('Optional mount changed during snapshot preparation')
        if any((process/'root'/mountpoint.lstrip('/')).iterdir()):
            raise ValueError('Optional mount files are in use: '+mountpoint+'; cannot safely capture')
        # Normal unmount refuses open files, mappings and working directories.
        # Never force/lazy unmount, enter a host namespace, or restart services.
        try:
            vm.run(['/usr/bin/nsenter', '--mount='+str(process/'ns/mnt'),
                    '--root='+str(process/'root'), '--wd=/', '--',
                    '/usr/bin/umount', mountpoint], timeout=15)
        except RuntimeError:
            remaining = [line for line in (process/'mountinfo').read_text().splitlines()
                         if len(line.split()) > 5 and line.split()[4] == mountpoint]
            if remaining:
                raise
    if candidates:
        vm.run([vm.PODMAN, 'exec', name, 'systemctl', 'mask',
                *sorted({unit for _, _, _, unit in candidates})], timeout=15)


def native_data_mounts(state):
    """Select mounted disk data; CRIU/runc own kernel cgroup state."""
    mounts=[]
    for mount in state.get('Mounts') or []:
        if mount.get('Type') not in ('bind','volume') or not mount.get('Source'):continue
        if (mount.get('Type')=='bind' and mount.get('Source') in ('cgroup','cgroup2') and
                mount.get('Destination')=='/sys/fs/cgroup'):
            # Podman 5 describes its runtime-created cgroup filesystem as a bind
            # with a symbolic source. It is not a relative host disk directory.
            continue
        source=str(Path(mount['Source']).resolve(strict=True))
        if (mount.get('Type')=='bind' and source=='/sys/fs/cgroup' and
                mount.get('Destination')=='/sys/fs/cgroup' and not mount.get('RW',False)):
            # The read-only host controller hierarchy is not disk data. Reading
            # write-only devices.allow/devices.deny files fails even as root.
            continue
        mounts.append({'source':source,'destination':mount['Destination'],'rw':mount.get('RW',False)})
    return mounts


def capture_native(shop):
    import mykos_linux
    shopcode(shop)
    with vm.locked():
        name,state=mykos_linux.inspect(shop)
        if not state.get('State',{}).get('Running'):raise ValueError('Start the container before taking a snapshot')
        criu_binary()
        check_native_compatibility(state)
        wait_native_network(state)
        vm.run(['criu','check'],timeout=60)
        prepare_native_mounts(name, state)
        driver=state.get('GraphDriver',{})
        if driver.get('Name')!='overlay':raise ValueError('Native live snapshots currently require Podman overlay storage')
        upper=Path(driver.get('Data',{}).get('UpperDir',''))
        if not upper.is_absolute() or not upper.is_dir():raise ValueError('Cannot locate the writable container layer')
        initialize();folder=root()/shop;vm.safe_dir(folder)
        identifier=str(int(time.time()))+'-'+os.urandom(6).hex()
        final=folder/identifier;temporary=Path(tempfile.mkdtemp(prefix='.capture-',dir=str(folder)))
        reference='localhost/mykos-snapshots/'+shop+':'+identifier
        pinned=False;published=False;pending=None
        try:
            mounts=native_data_mounts(state)
            context={'upper':str(upper.resolve(strict=True)),'mounts':mounts,'directory':str(temporary)}
            if (state.get('Config',{}).get('Labels') or {}).get('io.mykos.os') in ('debian13','archlinux','sles15sp7','amazon2023','alpine'):context['portable_paths']=True
            cgroup=native_cgroup_context(state)
            if cgroup:context['cgroup']=cgroup
            context_path=temporary/'hook-context.json';context_path.write_text(json.dumps(context));context_path.chmod(0o600)
            configure_native_hook()
            annotation=(state.get('Config',{}).get('Annotations') or {}).get('org.criu.config')
            if annotation:raise ValueError('A custom CRIU config overrides MYKOS capture hooks')
            pid=int(state['State']['Pid'])
            pending=HOOK_RUN/(str(pid)+'.json')
            if pending.is_symlink():raise ValueError('Unsafe CRIU request')
            pending.write_text(json.dumps({'context':str(context_path),'expires':time.time()+1800,
                'process_start':Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]}))
            pending.chmod(0o600)
            # runc resolves its CRIU executable through PATH. Keep the wrapper private
            # to this one checkpoint; unrelated operators use the vendor binary.
            binary=criu_binary()
            launcher=temporary/'bin';vm.safe_dir(launcher)
            script=launcher/'criu'
            settings={'MYKOS_CRIU_BINARY':binary,'MYKOS_CRIU_PID':str(pid),
                      'MYKOS_CRIU_CONTEXT':str(context_path)}
            # Podman sanitizes runtime environment variables. The root-private
            # launcher carries this operation's fixed settings itself.
            script.write_text('#!'+sys.executable+'\nimport os,sys\nos.environ.update('+
                repr(settings)+')\nsys.path.insert(0,'+repr(str(Path(__file__).parent))+
                ')\nfrom mykos_criu import main\nmain()\n')
            script.chmod(0o700)
            environment=dict(os.environ, PATH=str(launcher)+os.pathsep+os.environ.get('PATH',''))
            result=subprocess.run([vm.PODMAN,'container','checkpoint','--leave-running','--tcp-established','--file-locks',
                '--ignore-rootfs','--ignore-volumes','--compress','gzip','--export',str(temporary/'checkpoint.tar.gz'),name],
                env=environment,capture_output=True,text=True,timeout=900)
            if result.returncode:
                error=(temporary/'hook.error').read_text() if (temporary/'hook.error').exists() else (result.stderr or result.stdout)[-3000:]
                raise RuntimeError('CRIU live snapshot failed: '+error)
            if not (temporary/'hook.complete').is_file():
                raise RuntimeError('CRIU did not execute the filesystem capture hook; no snapshot was published')
            if not (temporary/'checkpoint.tar.gz').is_file() or not (temporary/'checkpoint.tar.gz').stat().st_size:
                raise RuntimeError('CRIU did not produce a memory archive')
            if not mykos_linux.inspect(shop)[1].get('State',{}).get('Running'):
                raise RuntimeError('Container is not running after live capture')
            image=state['Image'];vm.run([vm.PODMAN,'tag',image,reference]);pinned=True
            image_name=state.get('ImageName','')
            if re.fullmatch(r'(?:sha256:)?[0-9a-f]{12,64}',image_name):
                tags=json.loads(vm.run([vm.PODMAN,'image','inspect',image]))[0].get('RepoTags') or []
                image_name=next((tag for tag in tags if re.fullmatch(r'localhost/(?:mssql(?:2022|2025)|rh[0-9]+|(?:rocky|alma|oracle)(?:8|9|10)|centos(?:8|9|10)|fedora(?:43|44)|ubuntu[0-9.]*):[^/]+',tag)),image_name)
            os_match=re.search(r'(?:^|/)(mssql(?:2022|2025)|rh[0-9]+|(?:rocky|alma|oracle)(?:8|9|10)|centos(?:8|9|10)|fedora(?:43|44)|ubuntu[0-9.]*)(?=:|$)',image_name)
            metadata={'format':1,'id':identifier,'code':shop,'name':name,'os':os_match.group(1) if os_match else '', 'created_at':int(time.time()),
                      'capture_mode':'criu-memory','image':image,'image_ref':reference,'path':str(final),
                      'source_running':True,'configuration':state,'mounts':mounts,
                      'filesystem_source':'rootfs-upper','external_writers_frozen':False}
            if cgroup:metadata['cgroup']=json.loads(context_path.read_text())['cgroup']
            metadata['size_bytes']=allocated_bytes(temporary)
            path=temporary/'metadata.json';path.write_text(json.dumps(metadata,indent=2)+'\n');path.chmod(0o600)
            os.replace(temporary,final);published=True
            return public_metadata(metadata)
        finally:
            if pending is not None:pending.unlink(missing_ok=True)
            if not published:
                shutil.rmtree(temporary,ignore_errors=True)
                if pinned:vm.run([vm.PODMAN,'untag',state['Image'],reference])


def capture(shop):
    shopcode(shop)
    return capture_vm(shop) if vm.record_path(shop).is_file() else capture_native(shop)

import re
import tarfile


def snapshot_id(value):
    if not re.fullmatch(r'[0-9]{10,}-[0-9a-f]{12}', value):raise ValueError('Invalid snapshot selection')
    return value


def read_snapshot(shop, identifier, *, allow_deleting=False):
    shopcode(shop);snapshot_id(identifier)
    folder=root()/shop/identifier
    marker=folder.parent/('.deleting-'+identifier+'.json')
    if marker.exists() or marker.is_symlink():
        if marker.is_symlink() or marker.stat().st_uid!=os.geteuid() or marker.stat().st_mode & 0o077:raise ValueError('Unsafe snapshot deletion marker')
        value=json.loads(marker.read_text())
        if value.get('format')!=1 or value.get('code')!=shop or value.get('id')!=identifier:raise ValueError('Snapshot identity mismatch')
        if not allow_deleting:raise ValueError('Snapshot deletion pending; retry Delete')
        return folder.parent/('.deleting-'+identifier),value
    if not folder.is_dir():raise ValueError('Snapshot not found')
    vm.safe_dir(folder)
    path=folder/'metadata.json'
    if path.is_symlink() or not path.is_file():raise ValueError('Snapshot not found')
    status=path.stat()
    if status.st_uid!=os.geteuid() or status.st_mode & 0o077:raise ValueError('Unsafe snapshot metadata')
    value=json.loads(path.read_text())
    if value.get('format')!=1 or value.get('code')!=shop or value.get('id')!=identifier:raise ValueError('Snapshot identity mismatch')
    return folder,value


def restore_vm(shop,identifier):
    with vm.locked():
        folder,metadata=read_snapshot(shop,identifier)
        current=vm.read(shop);captured=metadata['configuration']
        if metadata.get('capture_mode')!='memory' or current.get('role')!='instance' or not current.get('ready') or current['os']!=captured['os']:
            raise ValueError('Snapshot does not match this persistent VM')
        if target(shop)!=current['name'] or metadata['name']!=current['name']:raise ValueError('Container identity mismatch')
        state=vm.inspect(current['name'])
        if (state.get('Config',{}).get('Labels') or {}).get('io.mykos.vm.os')!=current['os']:raise ValueError('Container identity mismatch')
        from mykos_runtime import require_container_capacity
        require_container_capacity(current['name'])
        value={**current,'image':metadata['image'],'image_name':metadata['image_ref'],'cpu_env':captured.get('cpu_env',{}),'capture_mode':'memory'}
        vm.restore_manifest(value,folder/'storage')
        parent=vm.storage(shop);vm.safe_dir(parent)
        transaction=Path(tempfile.mkdtemp(prefix='.restore-',dir=str(parent)))
        replacement=transaction/'replacement';previous=transaction/'previous'
        backup_name='mykos-restore-'+os.urandom(8).hex()
        running=bool(state.get('State',{}).get('Running'))
        renamed=False;swapped=False;launched=False
        recovery={'kind':'vm','shop':shop,'current':current,'original_id':state.get('Id'),
                  'backup_name':backup_name,'running':running,'phase':'prepared'}
        recovery_record(transaction,recovery)
        try:
            print('Restore: preparing saved disks',flush=True)
            vm.tree_copy(folder/'storage',replacement)
            request=replacement/'.mykos-checkpoint/restore.request'
            if request.is_symlink():raise ValueError('Symlink restore request rejected')
            request.write_text('restore once\n');request.chmod(0o600)
            if running:
                print('Restore: pausing and flushing current VM',flush=True)
                vm.stop_for_restore(current,parent/'storage')
            if vm.inspect(current['name']).get('State',{}).get('Running'):raise RuntimeError('Original container did not stop')
            durable_tree(replacement)
            vm.run([vm.PODMAN,'rename',current['name'],backup_name]);renamed=True
            os.replace(parent/'storage',previous);fsync_directory(parent);fsync_directory(transaction)
            try:os.replace(replacement,parent/'storage')
            except BaseException:
                os.replace(previous,parent/'storage');raise
            fsync_directory(parent);fsync_directory(transaction)
            swapped=True;launched=True
            print('Restore: loading saved RAM and device state',flush=True)
            vm.launch(shop,value);vm.wait_for_restore(value,parent/'storage')
            print('Restore: saved VM state resumed',flush=True)
            value.update(ready=True,last_start='snapshot-restore',restored_snapshot=identifier);vm.save(shop,value)
        except BaseException as original:
            try:
                if launched:
                    try:failed=vm.inspect(current['name'])
                    except RuntimeError:failed=None
                    if failed:
                        if failed.get('State',{}).get('Running'):vm.run([vm.PODMAN,'kill','--signal','KILL',current['name']],timeout=30)
                        vm.run([vm.PODMAN,'rm',current['name']],timeout=30)
                if swapped:
                    os.replace(parent/'storage',transaction/'failed');os.replace(previous,parent/'storage')
                vm.save(shop,current)
                if renamed:vm.run([vm.PODMAN,'rename',backup_name,current['name']])
                if running:vm.run([vm.PODMAN,'start',current['name']],timeout=120)
            except BaseException as recovery:
                raise RuntimeError('Restore failed; recovery files retained at '+str(transaction)+': '+str(recovery)) from original
            finish_transaction(transaction);raise
        recovery['phase']='committed';recovery_record(transaction,recovery)
        try:
            vm.run([vm.PODMAN,'rm',backup_name],timeout=30)
            vm.prune_checkpoints(shop);finish_transaction(transaction)
        except (RuntimeError,OSError) as error:
            return {'shop':shop,'state':'restored','snapshot':identifier,'warning':'Verified restore; recovery cleanup deferred: '+str(error)}
        return {'shop':shop,'state':'restored','snapshot':identifier}


def extract_checkpoint(archive,destination):
    vm.safe_dir(destination)
    allowed={'checkpoint','artifacts','network.status','devshm-checkpoint.tar','stats-dump','dump.log'}
    with tarfile.open(archive,'r:*') as handle:
        members=handle.getmembers()
        for item in members:
            parts=Path(item.name).parts
            if not parts or Path(item.name).is_absolute() or '..' in parts or item.issym() or item.islnk() or not (item.isfile() or item.isdir()):raise ValueError('Unsafe checkpoint archive member')
        for item in members:
            if Path(item.name).parts[0] in allowed:handle.extract(item,destination)
    if not (destination/'checkpoint/inventory.img').is_file():raise ValueError('Incomplete CRIU snapshot')


def restore_native(shop,identifier):
    import mykos_linux
    with vm.locked():
        folder,metadata=read_snapshot(shop,identifier)
        if metadata.get('capture_mode')!='criu-memory':raise ValueError('Not a native memory snapshot')
        name,state=mykos_linux.inspect(shop);mykos_linux.require_persistent(state)
        from mykos_runtime import require_container_capacity
        require_container_capacity(name)
        if name!=metadata['name'] or state['Id']!=metadata['configuration']['Id'] or state['Image']!=metadata['image']:raise ValueError('Native snapshot requires its original container and image')
        if state.get('GraphDriver',{}).get('Name')!='overlay':raise ValueError('Overlay storage is required')
        cgroup=restore_cgroup_context(state,metadata)
        upper=Path(state['GraphDriver']['Data']['UpperDir']);vm.safe_dir(upper)
        static=Path(state['StaticDir']);vm.safe_dir(static)
        mounts=metadata.get('mounts',[])
        current={(m['source'],m['destination'],bool(m['rw'])) for m in native_data_mounts(state)}
        if any((m['source'],m['destination'],bool(m['rw'])) not in current for m in mounts):raise ValueError('Mounted data configuration changed since capture')
        targets=[(folder/'rootfs-upper',upper)]
        for index,mount in enumerate(mounts):
            if not mount['rw']:continue
            source=folder/'mounts'/str(index)/'data';dest=Path(mount['source'])
            if source.is_symlink() or dest.is_symlink() or source.is_dir()!=dest.is_dir() or not source.exists() or not dest.exists():raise ValueError('Snapshot mount type changed')
            permitted=[vm.POS/('.'+shop),(Path('/backups')/('tfrsync-'+shop)).resolve()]
            volume_sources={str(Path(m['Source']).resolve()) for m in state.get('Mounts',[]) if m.get('Type')=='volume'}
            if str(dest) not in volume_sources and not any(dest==p or p in dest.parents for p in permitted):raise ValueError('Refusing restore of unmanaged shared host data')
            targets.append((source,dest))
        from mykos_containers import all_containers, unshared
        unshared([dest for _,dest in targets],all_containers(),state['Id'])
        initialize();transaction=Path(tempfile.mkdtemp(prefix='.native-restore-',dir=str(root())))
        try:
            unpacked=transaction/'checkpoint';extract_checkpoint(folder/'checkpoint.tar.gz',unpacked)
            from mykos_criu_images import external_files,normalize_files
            if re.search(r'Version:\s*3\.',vm.run(['criu','--version'],timeout=10)):
                normalize_files(unpacked/'checkpoint',external_files(state))
            environment=None
            if cgroup:
                context=transaction/'restore-context.json'
                context.write_text(json.dumps({'operation':'restore','cgroup':cgroup}));context.chmod(0o600)
                launcher=transaction/'bin';vm.safe_dir(launcher)
                binary=criu_binary()
                settings={'MYKOS_CRIU_BINARY':binary,'MYKOS_CRIU_PID':'0','MYKOS_CRIU_CONTEXT':str(context)}
                script=launcher/'criu'
                script.write_text('#!'+sys.executable+'\nimport os,sys\nos.environ.update('+repr(settings)+')\nsys.path.insert(0,'+repr(str(Path(__file__).parent))+')\nfrom mykos_criu import main\nmain()\n');script.chmod(0o700)
                environment=dict(os.environ,PATH=str(launcher)+os.pathsep+os.environ.get('PATH',''))
        except Exception:
            finish_transaction(transaction);raise
        running=bool(state.get('State',{}).get('Running'));changed=False;backed=[]
        recovery={'kind':'native','shop':shop,'name':name,'original_id':state['Id'],'running':running,'changed':False,'backed':[],'phase':'prepared'}
        recovery_record(transaction,recovery)
        def sync(source,dest):
            if source.is_dir():vm.run(['/usr/bin/rsync','-aHAX','--numeric-ids','--delete',str(source)+'/',str(dest)+'/'],timeout=900)
            else:vm.run(['/usr/bin/cp','--preserve=all','--reflink=auto',str(source),str(dest)],timeout=900)
        try:
            mykos_linux.remember(shop,name,state)
            if running:vm.run([vm.PODMAN,'stop','--time','120',name],timeout=150)
            if mykos_linux.inspect(shop)[1].get('State',{}).get('Running'):raise RuntimeError('Container did not stop')
            vm.run([vm.PODMAN,'unmount',name],timeout=30)
            merged=state['GraphDriver']['Data'].get('MergedDir')
            if merged and os.path.ismount(merged):raise ValueError('Container layer is still mounted')
            unshared([dest for _,dest in targets],all_containers(),state['Id'])
            for index,(source,dest) in enumerate(targets):
                if source.is_symlink() or not source.exists() or source.is_dir()!=dest.is_dir():raise ValueError('Snapshot filesystem is missing')
                backup=transaction/('data-'+str(index))
                vm.run(['/usr/bin/cp','-a','--reflink=auto','--sparse=auto',str(dest),str(backup)],timeout=900)
                durable_tree(backup)
                backed.append((backup,dest))
                recovery['backed'].append({'backup':backup.name,'destination':str(dest)})
                recovery_record(transaction,recovery)
            recovery['changed']=True;recovery_record(transaction,recovery)
            changed=True
            for source,dest in targets:sync(source,dest)
            for entry in unpacked.iterdir():
                dest=static/entry.name
                if dest.is_symlink():raise ValueError('Unsafe checkpoint destination')
                if dest.is_dir():shutil.rmtree(dest)
                elif dest.exists():dest.unlink()
                if entry.is_dir():shutil.copytree(entry,dest)
                else:shutil.copy2(entry,dest)
            vm.run([vm.PODMAN,'container','restore','--tcp-established','--file-locks',name],timeout=900,env=environment)
            if not mykos_linux.inspect(shop)[1].get('State',{}).get('Running'):raise RuntimeError('CRIU did not resume the container')
            for _,dest in targets:durable_tree(dest)
        except BaseException as original_error:
            try:
                if changed:
                    if mykos_linux.inspect(shop)[1].get('State',{}).get('Running'):vm.run([vm.PODMAN,'stop','--time','120',name],timeout=150)
                    vm.run([vm.PODMAN,'unmount',name],timeout=30)
                    for backup,dest in reversed(backed):
                        sync(backup,dest);durable_tree(dest)
                if running:vm.run([vm.PODMAN,'start',name],timeout=120)
            except BaseException as recovery:raise RuntimeError('Restore failed; recovery retained at '+str(transaction)+': '+str(recovery)) from original_error
            finish_transaction(transaction);raise
        recovery['phase']='committed';recovery_record(transaction,recovery)
        finish_transaction(transaction)
        return {'shop':shop,'state':'restored','snapshot':identifier}


def restore(shop,identifier):
    _,value=read_snapshot(shop,identifier)
    return restore_vm(shop,identifier) if value['capture_mode']=='memory' else restore_native(shop,identifier)


HOOK_RUN=Path('/run/mykos-criu')
CRIU_CONFIGURATION=Path('/etc/criu/runc.conf')


def configure_native_hook():
    """Remove the legacy shell hook; runc uses scoped RPC notification capture."""
    vm.safe_dir(HOOK_RUN)
    remove_native_hook()


def remove_native_hook():
    if not CRIU_CONFIGURATION.exists():return
    if CRIU_CONFIGURATION.is_symlink():raise ValueError('Unsafe CRIU configuration')
    status=CRIU_CONFIGURATION.stat()
    if status.st_uid!=os.geteuid() or status.st_mode & 0o022:raise ValueError('Unsafe CRIU configuration')
    lines=CRIU_CONFIGURATION.read_text().splitlines(keepends=True)
    kept=[line for line in lines if line.strip()!='action-script /usr/local/sbin/mykos-criu-hook']
    if kept!=lines:
        descriptor,temporary=tempfile.mkstemp(prefix='.mykos-unhook-',dir=str(CRIU_CONFIGURATION.parent))
        try:
            with os.fdopen(descriptor,'w') as handle:handle.write(''.join(kept))
            os.chmod(temporary,status.st_mode & 0o777);os.replace(temporary,CRIU_CONFIGURATION)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)



def delete_snapshot(shop,identifier):
    """Delete saved state with a durable, visible and retryable tombstone."""
    with vm.locked():
        folder,metadata=read_snapshot(shop,identifier,allow_deleting=True)
        reference='localhost/mykos-snapshots/'+shop+':'+identifier
        if metadata.get('image_ref')!=reference:raise ValueError('Snapshot image reference mismatch')
        marker=folder.parent/('.deleting-'+identifier+'.json')
        pending=folder.parent/('.deleting-'+identifier)
        if not marker.exists():durable_json(marker,metadata)
        original=folder.parent/identifier
        if original.exists():
            if pending.exists():raise ValueError('Ambiguous snapshot deletion; manual inspection required')
            os.replace(original,pending);fsync_directory(folder.parent)
        try:
            if pending.exists():
                vm.safe_dir(pending);shutil.rmtree(pending)
            # A retry may reach this point after the data or tag was removed.
            try:vm.run([vm.PODMAN,'untag',metadata['image'],reference],timeout=30)
            except RuntimeError:
                tags=vm.run([vm.PODMAN,'images','--format','{{.Repository}}:{{.Tag}}'],timeout=30).splitlines()
                if reference in tags:raise
        except (OSError,RuntimeError) as error:
            raise RuntimeError('Snapshot deletion incomplete; retry Delete for '+identifier+': '+str(error)) from error
        marker.unlink();fsync_directory(marker.parent)
        return {'shop':shop,'state':'deleted','snapshot':identifier}


def durable_json(path,value):
    """Publish private recovery state before a destructive operation."""
    fd,temporary=tempfile.mkstemp(prefix='.journal-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as out:
            json.dump(value,out);out.flush();os.fsync(out.fileno())
        os.replace(temporary,path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def fsync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def durable_tree(path):
    if path.is_symlink():return
    if path.is_dir():
        for entry in path.iterdir():durable_tree(entry)
        fsync_directory(path)
    elif path.is_file():
        with path.open('rb') as handle:os.fsync(handle.fileno())


def recovery_record(transaction,value):
    value=dict(format=1,**value)
    durable_json(transaction/'recovery.json',value)
    index=vm.ROOT/'restore-recovery';vm.safe_dir(index)
    durable_json(index/(transaction.name+'.json'),{'journal':str(transaction/'recovery.json'),'recovery':value})


def finish_transaction(transaction):
    index=vm.ROOT/'restore-recovery'/(transaction.name+'.json')
    if index.exists():
        value=json.loads(index.read_text());value['cleanup_only']=True
        durable_json(index,value)
    if transaction.exists():shutil.rmtree(transaction)
    if index.exists():index.unlink();fsync_directory(index.parent)


def recover_locked():
    """Reconcile indexed restores under the global lifecycle lock.

    The private index avoids consulting storage configuration on ordinary locks.
    New transactions publish this index before their first destructive action.
    Older unjournaled recovery directories remain available for manual review.
    """
    index=vm.ROOT/'restore-recovery'
    if not index.exists():return
    vm.safe_dir(index)
    for entry in sorted(index.glob('*.json')):
        if entry.is_symlink() or entry.stat().st_uid!=os.geteuid() or entry.stat().st_mode & 0o077:
            raise ValueError('Unsafe restore recovery index')
        record=json.loads(entry.read_text());journal=Path(record['journal']);transaction=journal.parent;data=record['recovery']
        shopcode(data['shop'])
        if journal.name!='recovery.json' or entry.name!=transaction.name+'.json' or data.get('format')!=1:
            raise ValueError('Invalid restore recovery index')
        expected=vm.storage(data['shop']) if data['kind']=='vm' else root()
        prefix='.restore-' if data['kind']=='vm' else '.native-restore-'
        if transaction.parent!=expected or not transaction.name.startswith(prefix):raise ValueError('Recovery path mismatch')
        if transaction.exists():vm.safe_dir(transaction)
        try:
            if record.get('cleanup_only'):
                finish_transaction(transaction)
            elif data['kind']=='vm':recover_vm(transaction,data)
            elif data['kind']=='native':recover_native(transaction,data)
            else:raise ValueError('Unknown restore recovery kind')
        except BaseException as error:
            raise RuntimeError('Unfinished snapshot restore; recovery retained at '+str(transaction)+': '+str(error)) from error


def maybe_inspect(name):
    try:return vm.inspect(name)
    except RuntimeError:return None


def recover_vm(transaction,data):
    shop=data['shop'];parent=vm.storage(shop);current=data['current'];name=current['name']
    if transaction.parent!=parent or not re.fullmatch(r'mykos-restore-[0-9a-f]{16}',data['backup_name']):raise ValueError('Recovery path mismatch')
    backup=maybe_inspect(data['backup_name']);active=maybe_inspect(name)
    if backup and backup.get('Id')!=data['original_id']:raise ValueError('Original VM identity changed')
    if data['phase']=='committed':
        if backup:
            if backup.get('State',{}).get('Running'):raise ValueError('Recovery backup unexpectedly running')
            vm.run([vm.PODMAN,'rm',data['backup_name']],timeout=30)
        finish_transaction(transaction);return
    if backup:
        if active:
            labels=active.get('Config',{}).get('Labels') or {}
            if active.get('Id')==data['original_id'] or labels.get('io.mykos.vm.os')!=current['os'] or labels.get('io.mykos.vm.role')!='instance':raise ValueError('Replacement VM identity changed')
            if active.get('State',{}).get('Running'):vm.run([vm.PODMAN,'kill','--signal','KILL',name],timeout=30)
            vm.run([vm.PODMAN,'rm',name],timeout=30)
    elif not active or active.get('Id')!=data['original_id']:
        raise ValueError('Original VM unavailable; manual recovery required')
    previous=transaction/'previous'
    if previous.exists():
        if not backup and active.get('State',{}).get('Running'):raise ValueError('Refusing disk recovery under a running VM')
        if (parent/'storage').exists():
            failed=transaction/'failed-recovery'
            if failed.exists():shutil.rmtree(failed)
            os.replace(parent/'storage',failed)
        os.replace(previous,parent/'storage');fsync_directory(parent);fsync_directory(transaction)
    vm.save(shop,current)
    if backup:vm.run([vm.PODMAN,'rename',data['backup_name'],name])
    if data['running'] and not (maybe_inspect(name) or {}).get('State',{}).get('Running'):
        vm.run([vm.PODMAN,'start',name],timeout=120)
    finish_transaction(transaction)


def sync_recovery(source,dest):
    if source.is_dir():vm.run(['/usr/bin/rsync','-aHAX','--numeric-ids','--delete',str(source)+'/',str(dest)+'/'],timeout=900)
    else:vm.run(['/usr/bin/cp','--preserve=all','--reflink=auto',str(source),str(dest)],timeout=900)


def recover_native(transaction,data):
    import mykos_linux
    from mykos_containers import all_containers,unshared
    if transaction.parent!=root():raise ValueError('Native recovery path mismatch')
    name,state=mykos_linux.inspect(data['shop']);mykos_linux.require_persistent(state)
    if state['Id']!=data['original_id'] or name!=data['name']:raise ValueError('Native recovery identity changed')
    if data['phase']=='committed':finish_transaction(transaction);return
    allowed={str(Path(state['GraphDriver']['Data']['UpperDir']))}
    allowed.update(m['source'] for m in native_data_mounts(state) if m['rw'])
    backed=[]
    for entry in data['backed']:
        dest=Path(entry['destination']);backup=transaction/entry['backup']
        if str(dest) not in allowed or backup.parent!=transaction or not re.fullmatch(r'data-[0-9]+',entry['backup']) or backup.is_symlink() or not backup.exists() or dest.is_symlink():raise ValueError('Native recovery data changed')
        backed.append((backup,dest))
    unshared([dest for _,dest in backed],all_containers(),state['Id'])
    if data['changed']:
        if state.get('State',{}).get('Running'):vm.run([vm.PODMAN,'stop','--time','120',name],timeout=150)
        if mykos_linux.inspect(data['shop'])[1].get('State',{}).get('Running'):raise ValueError('Native recovery container did not stop')
        vm.run([vm.PODMAN,'unmount',name],timeout=30)
        merged=state['GraphDriver']['Data'].get('MergedDir')
        if merged and os.path.ismount(merged):raise ValueError('Native recovery layer remains mounted')
        for backup,dest in reversed(backed):
            sync_recovery(backup,dest);durable_tree(dest)
    if data['running'] and not mykos_linux.inspect(data['shop'])[1].get('State',{}).get('Running'):
        vm.run([vm.PODMAN,'start',name],timeout=120)
    finish_transaction(transaction)

