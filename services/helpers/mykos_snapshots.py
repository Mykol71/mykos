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
    return {key:value[key] for key in ('format','id','code','name','os','created_at','capture_mode','image','path','source_running','size_bytes') if key in value}


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
    for entry in sorted(folder.iterdir(), reverse=True):
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


def check_native_compatibility(state, proc=Path('/proc')):
    """Reject known CRIU 3.x blockers before seizing any source processes."""
    version=vm.run(['criu','--version'],timeout=10)
    if not re.search(r'Version:\s*3\.',version): return
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
                if target_name=='anon_inode:bpf-prog':
                    raise ValueError('Native RAM snapshot unavailable: this container holds BPF programs that CRIU 3.x cannot save. No snapshot was created; the container was left running. Use a checkpoint-compatible container profile/runtime.')
            if (process/'ns/uts').readlink()!=uts:
                name=(process/'comm').read_text().strip()
                raise ValueError('Native RAM snapshot unavailable: '+name+' uses a nested hostname namespace that CRIU 3.x cannot save. No snapshot was created; the container was left running.')
        except (FileNotFoundError,ProcessLookupError):continue


def prepare_native_mounts(name, state, proc=Path('/proc')):
    """Remove unused optional hugetlbfs copies only in this container's namespaces."""
    init = proc / str(int(state['State']['Pid']))
    pid_namespace = (init/'ns/pid').readlink()
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
        # Normal unmount refuses open files, mappings and working directories.
        # Never force/lazy unmount, enter a host namespace, or restart services.
        vm.run(['/usr/bin/nsenter', '--mount='+str(process/'ns/mnt'),
                '--root='+str(process/'root'), '--wd=/', '--',
                '/usr/bin/umount', mountpoint], timeout=15)
    if candidates:
        vm.run([vm.PODMAN, 'exec', name, 'systemctl', 'mask',
                *sorted({unit for _, _, _, unit in candidates})], timeout=15)


def capture_native(shop):
    import mykos_linux
    shopcode(shop)
    with vm.locked():
        name,state=mykos_linux.inspect(shop)
        if not state.get('State',{}).get('Running'):raise ValueError('Start the container before taking a snapshot')
        if not shutil.which('criu'):raise ValueError('Install criu and runc to enable native Linux memory snapshots')
        check_native_compatibility(state)
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
            mounts=[]
            for mount in state.get('Mounts') or []:
                if mount.get('Type') in ('bind','volume') and mount.get('Source'):
                    mounts.append({'source':str(Path(mount['Source']).resolve(strict=True)),
                                   'destination':mount['Destination'],'rw':mount.get('RW',False)})
            context={'upper':str(upper.resolve(strict=True)),'mounts':mounts,'directory':str(temporary)}
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
            binary=str(Path(shutil.which('criu')).resolve(strict=True))
            if binary not in ('/usr/sbin/criu','/usr/bin/criu'):
                raise ValueError('Expected the vendor CRIU binary')
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


def read_snapshot(shop, identifier):
    shopcode(shop);snapshot_id(identifier)
    folder=root()/shop/identifier
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
        value={**current,'image':metadata['image'],'image_name':metadata['image_ref'],'cpu_env':captured.get('cpu_env',{}),'capture_mode':'memory'}
        vm.restore_manifest(value,folder/'storage')
        parent=vm.storage(shop);vm.safe_dir(parent)
        transaction=Path(tempfile.mkdtemp(prefix='.restore-',dir=str(parent)))
        replacement=transaction/'replacement';previous=transaction/'previous'
        backup_name='mykos-restore-'+os.urandom(8).hex()
        running=bool(state.get('State',{}).get('Running'))
        renamed=False;swapped=False;launched=False
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
            vm.run([vm.PODMAN,'rename',current['name'],backup_name]);renamed=True
            os.replace(parent/'storage',previous)
            try:os.replace(replacement,parent/'storage')
            except Exception:
                os.replace(previous,parent/'storage');raise
            swapped=True;launched=True
            print('Restore: loading saved RAM and device state',flush=True)
            vm.launch(shop,value);vm.wait_for_restore(value,parent/'storage')
            print('Restore: saved VM state resumed',flush=True)
            value.update(ready=True,last_start='snapshot-restore',restored_snapshot=identifier);vm.save(shop,value)
        except Exception as original:
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
            except Exception as recovery:
                raise RuntimeError('Restore failed; recovery files retained at '+str(transaction)+': '+str(recovery)) from original
            shutil.rmtree(transaction);raise
        try:
            vm.run([vm.PODMAN,'rm',backup_name],timeout=30)
            vm.prune_checkpoints(shop);shutil.rmtree(transaction)
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
        if name!=metadata['name'] or state['Id']!=metadata['configuration']['Id'] or state['Image']!=metadata['image']:raise ValueError('Native snapshot requires its original container and image')
        if state.get('GraphDriver',{}).get('Name')!='overlay':raise ValueError('Overlay storage is required')
        upper=Path(state['GraphDriver']['Data']['UpperDir']);vm.safe_dir(upper)
        static=Path(state['StaticDir']);vm.safe_dir(static)
        mounts=metadata.get('mounts',[])
        current={(str(Path(m['Source']).resolve(strict=True)),m['Destination'],bool(m.get('RW'))) for m in state.get('Mounts',[]) if m.get('Type') in ('bind','volume')}
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
        initialize();transaction=Path(tempfile.mkdtemp(prefix='.native-restore-',dir=str(root())))
        unpacked=transaction/'checkpoint';extract_checkpoint(folder/'checkpoint.tar.gz',unpacked)
        running=bool(state.get('State',{}).get('Running'));changed=False;backed=[]
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
            for index,(source,dest) in enumerate(targets):
                if source.is_symlink() or not source.is_dir():raise ValueError('Snapshot filesystem is missing')
                backup=transaction/('data-'+str(index))
                vm.run(['/usr/bin/cp','-a','--reflink=auto','--sparse=auto',str(dest),str(backup)],timeout=900)
                backed.append((backup,dest))
            changed=True
            for source,dest in targets:sync(source,dest)
            for entry in unpacked.iterdir():
                dest=static/entry.name
                if dest.is_symlink():raise ValueError('Unsafe checkpoint destination')
                if dest.is_dir():shutil.rmtree(dest)
                elif dest.exists():dest.unlink()
                if entry.is_dir():shutil.copytree(entry,dest)
                else:shutil.copy2(entry,dest)
            vm.run([vm.PODMAN,'container','restore','--tcp-established','--file-locks',name],timeout=900)
            if not mykos_linux.inspect(shop)[1].get('State',{}).get('Running'):raise RuntimeError('CRIU did not resume the container')
        except Exception as original_error:
            try:
                if changed:
                    if mykos_linux.inspect(shop)[1].get('State',{}).get('Running'):vm.run([vm.PODMAN,'stop','--time','120',name],timeout=150)
                    vm.run([vm.PODMAN,'unmount',name],timeout=30)
                    for backup,dest in reversed(backed):sync(backup,dest)
                if running:vm.run([vm.PODMAN,'start',name],timeout=120)
            except Exception as recovery:raise RuntimeError('Restore failed; recovery retained at '+str(transaction)+': '+str(recovery)) from original_error
            shutil.rmtree(transaction);raise
        shutil.rmtree(transaction)
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
    """Delete one explicit saved state; never stop or remove its live container."""
    with vm.locked():
        folder,metadata=read_snapshot(shop,identifier)
        reference='localhost/mykos-snapshots/'+shop+':'+identifier
        if metadata.get('image_ref')!=reference:raise ValueError('Snapshot image reference mismatch')
        pending=folder.parent/('.deleting-'+identifier+'-'+os.urandom(6).hex())
        os.replace(folder,pending)
        try:shutil.rmtree(pending)
        except OSError as error:
            raise RuntimeError('Snapshot deletion incomplete; remaining files retained at '+str(pending)+': '+str(error)) from error
        result={'shop':shop,'state':'deleted','snapshot':identifier}
        # Drop only this snapshot tag. Other snapshots, images and containers retain their own references.
        try:vm.run([vm.PODMAN,'untag',metadata['image'],reference],timeout=30)
        except RuntimeError as error:result['warning']='Snapshot deleted; image tag cleanup deferred: '+str(error)
        return result
