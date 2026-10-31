"""Snapshot transaction and persistent native lifecycle tests, no real guests."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tarfile
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_vm as vm
import mykos_linux as linux
import mykos_snapshots as snaps

@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setattr(vm,'ROOT',tmp_path/'pos/.vm');monkeypatch.setattr(vm,'POS',tmp_path/'pos')
    monkeypatch.setattr(vm,'storage_root',lambda:tmp_path/'disks')
    monkeypatch.setattr(snaps,'target',lambda code:code+'.test')
    monkeypatch.setattr(snaps,'prepare_native_mounts',lambda *args:None)
    monkeypatch.setattr(snaps,'criu_binary',lambda:'/usr/sbin/criu')
    calls=[]
    monkeypatch.setattr(vm,'run',lambda args,**kw:calls.append(args) or '')
    monkeypatch.setattr(vm,'stop_for_restore',lambda value,disk:vm.stop_container(value['name'],grace_seconds=2))
    return tmp_path,calls


def seed_vm():
    vm.safe_dir(vm.ROOT);vm.safe_dir(vm.storage('12345678')/'storage')
    value={'role':'instance','ready':True,'name':'12345678.test','os':'mac15','image':'sha256:abc','cpu_env':{}}
    vm.save('12345678',value)
    (vm.storage('12345678')/'storage/disk').write_text('current')
    return value


def seed_snapshot(value):
    identifier='1792900000-abcdef123456';folder=snaps.root()/'12345678'/identifier
    vm.safe_dir(folder/'storage/.mykos-checkpoint')
    (folder/'storage/disk').write_text('snapshot')
    data={'format':1,'id':identifier,'code':'12345678','name':value['name'],'os':'mac15','created_at':1792900000,
          'capture_mode':'memory','image':value['image'],'image_ref':'localhost/snapshot:test','configuration':value,'path':str(folder),'source_running':True}
    path=folder/'metadata.json';path.write_text(json.dumps(data));path.chmod(0o600)
    return identifier,folder


def test_vm_restore_commits_only_after_resume_verified(environment,monkeypatch):
    value=seed_vm();identifier,folder=seed_snapshot(value);running={'value':True}
    calls=environment[1]
    def run(args,**kw):
        calls.append(args)
        if args[1]=='stop':running['value']=False
        return ''
    monkeypatch.setattr(vm,'run',run)
    monkeypatch.setattr(vm,'inspect',lambda name:{'State':{'Running':running['value']},'Config':{'Labels':{'io.mykos.vm.os':'mac15'}}})
    monkeypatch.setattr(vm,'restore_manifest',lambda *args:None)
    import shutil
    monkeypatch.setattr(vm,'tree_copy',lambda a,b:shutil.copytree(a,b))
    monkeypatch.setattr(vm,'launch',lambda *args:running.update(value=True))
    def verify(*args):
        assert (vm.storage('12345678')/'storage/disk').read_text()=='snapshot'
        assert not any(command[1]=='rm' for command in calls)
    monkeypatch.setattr(vm,'wait_for_restore',verify)
    monkeypatch.setattr(vm,'prune_checkpoints',lambda code:None)
    assert snaps.restore_vm('12345678',identifier)['state']=='restored'
    assert vm.read('12345678')['last_start']=='snapshot-restore'
    assert (folder/'storage/disk').read_text()=='snapshot'
    assert not list(vm.storage('12345678').glob('.restore-*'))


def test_failed_vm_restore_rolls_back_original_disks(environment,monkeypatch):
    value=seed_vm();identifier,folder=seed_snapshot(value);running={'value':True};calls=environment[1]
    def run(args,**kw):
        calls.append(args)
        if args[1] in ('stop','kill'):running['value']=False
        if args[1]=='start':running['value']=True
        return ''
    monkeypatch.setattr(vm,'run',run)
    monkeypatch.setattr(vm,'inspect',lambda name:{'State':{'Running':running['value']},'Config':{'Labels':{'io.mykos.vm.os':'mac15'}}})
    monkeypatch.setattr(vm,'restore_manifest',lambda *args:None)
    import shutil
    monkeypatch.setattr(vm,'tree_copy',lambda a,b:shutil.copytree(a,b))
    monkeypatch.setattr(vm,'launch',lambda *args:running.update(value=True))
    monkeypatch.setattr(vm,'wait_for_restore',lambda *args:(_ for _ in ()).throw(RuntimeError('RAM incompatible')))
    with pytest.raises(RuntimeError,match='RAM incompatible'):snaps.restore_vm('12345678',identifier)
    assert running['value'] is True
    assert (vm.storage('12345678')/'storage/disk').read_text()=='current'
    assert vm.read('12345678')==value
    assert (folder/'storage/disk').read_text()=='snapshot'


def test_snapshot_list_hides_private_configuration_and_rejects_traversal(environment):
    value=seed_vm();identifier,folder=seed_snapshot(value)
    assert len(snaps.list_snapshots())==1
    assert 'configuration' not in snaps.list_snapshots('12345678')[0]
    assert 'image_ref' not in snaps.list_snapshots()[0]
    for bad in ('../etc','test','1792900000-abc/../../etc'):
        with pytest.raises(ValueError):snaps.read_snapshot('12345678',bad)
    (folder/'metadata.json').chmod(0o644)
    with pytest.raises(ValueError,match='Unsafe'):snaps.read_snapshot('12345678',identifier)


def test_checkpoint_archive_rejects_links_and_traversal(environment):
    base,_=environment
    for name,link in (('../escape',False),('checkpoint/link',True)):
        path=base/'bad.tar'
        with tarfile.open(path,'w') as handle:
            item=tarfile.TarInfo(name)
            if link:item.type=tarfile.SYMTYPE;item.linkname='/etc'
            else:item.size=1
            handle.addfile(item,None if link else io.BytesIO(b'x'))
        with pytest.raises(ValueError,match='Unsafe'):snaps.extract_checkpoint(path,base/'extracted')
    assert not (base/'escape').exists()


def test_native_stop_preserves_container_and_mac(environment,monkeypatch):
    state={'Image':'sha256:abc','HostConfig':{'AutoRemove':False},'State':{'Running':True}}
    monkeypatch.setattr(linux,'inspect',lambda shop:('12345678.test',state))
    monkeypatch.setattr(linux,'config',lambda:{})
    monkeypatch.setattr(linux,'network_device',lambda config:'eth0')
    monkeypatch.setattr(linux.host,'network',lambda state:{'mac':'00:11:22:33:44:55'})
    calls=[]
    def run(args,**kw):
        calls.append(args)
        return '00:11:22:33:44:55' if 'exec' in args else ''
    monkeypatch.setattr(linux,'run',run)
    assert linux.stop('12345678')['state']=='stopped'
    assert linux.saved('12345678')['mac']=='00:11:22:33:44:55'
    assert not any('rm' in command for command in calls)
    state['HostConfig']['AutoRemove']=True
    before=len(calls)
    with pytest.raises(ValueError,match='auto-remove'):linux.stop('12345678')
    assert len(calls)==before


def test_vm_capture_leaves_source_running_and_pins_image(environment,monkeypatch):
    value=seed_vm();calls=environment[1]
    monkeypatch.setattr(vm,'inspect',lambda name:{'State':{'Running':True}})
    def capture(value,source,dest):
        import shutil
        shutil.copytree(source,dest)
    monkeypatch.setattr(vm,'capture_live',capture)
    result=snaps.capture_vm('12345678')
    assert result['source_running'] is True
    assert not any(command[1] in ('stop','rm','kill') for command in calls)
    assert any(command[1]=='tag' for command in calls)
    folder=Path(result['path'])
    assert folder.stat().st_mode & 0o777==0o700
    assert (folder/'metadata.json').stat().st_mode & 0o777==0o600


def test_failed_capture_publishes_nothing(environment,monkeypatch):
    seed_vm();monkeypatch.setattr(vm,'inspect',lambda name:{'State':{'Running':True}})
    monkeypatch.setattr(vm,'capture_live',lambda *args:(_ for _ in ()).throw(RuntimeError('disk full')))
    with pytest.raises(RuntimeError,match='disk full'):snaps.capture_vm('12345678')
    assert snaps.list_snapshots()==[]
    assert not list((snaps.root()/'12345678').iterdir())



def test_cleanup_preserves_live_and_snapshot_base_references(environment,monkeypatch):
    vm.safe_dir(vm.ROOT)
    folder=snaps.root()/'12345678'/'1792900000-abcdef123456';vm.safe_dir(folder)
    (folder/'metadata.json').write_text(json.dumps({'image':'sha256:saved'}))
    calls=[]
    def run(args,**kw):
        calls.append(args)
        if args[1:3]==['ps','-a']:return json.dumps([{'Names':['live']}])
        if args[1]=='images':return json.dumps([{'Names':['localhost/win11:base','localhost/macos15:base','localhost/ubuntu24.04:base']}])
        if args[1:3]==['image','inspect']:
            return json.dumps([{'Id':{'localhost/win11:base':'sha256:live','localhost/macos15:base':'sha256:saved','localhost/ubuntu24.04:base':'sha256:unused'}[args[-1]]}])
        return ''
    monkeypatch.setattr(vm,'run',run);monkeypatch.setattr(vm,'inspect',lambda name:{'Image':'sha256:live'})
    assert vm.cleanup_unused_base_images()==['localhost/ubuntu24.04:base']
    assert not any('--force' in command for command in calls)
    assert [vm.PODMAN,'image','rm','localhost/ubuntu24.04:base'] in calls


def test_native_hook_initialization_preserves_operator_settings(environment,monkeypatch):
    base,_=environment;configuration=base/'criu/runc.conf';configuration.parent.mkdir()
    configuration.write_text('log-level 2\naction-script /usr/local/sbin/mykos-criu-hook\n');configuration.chmod(0o600)
    monkeypatch.setattr(snaps,'CRIU_CONFIGURATION',configuration);monkeypatch.setattr(snaps,'HOOK_RUN',base/'run')
    snaps.configure_native_hook();snaps.configure_native_hook()
    text=configuration.read_text()
    assert text.startswith('log-level 2\n')
    assert 'action-script /usr/local/sbin/mykos-criu-hook' not in text

    snaps.remove_native_hook()
    assert configuration.read_text()=='log-level 2\n'



def test_delete_selected_snapshot_keeps_other_states_and_live_disks(environment):
    value=seed_vm();identifier,folder=seed_snapshot(value)
    path=folder/'metadata.json';data=json.loads(path.read_text())
    data['image_ref']='localhost/mykos-snapshots/12345678:'+identifier
    path.write_text(json.dumps(data));path.chmod(0o600)
    other=folder.parent/'1792900001-abcdef123457'
    import shutil
    shutil.copytree(folder,other)
    result=snaps.delete_snapshot('12345678',identifier)
    assert result['state']=='deleted'
    assert not folder.exists() and other.exists()
    assert (vm.storage('12345678')/'storage/disk').read_text()=='current'
    assert environment[1]==[[vm.PODMAN,'untag','sha256:abc',data['image_ref']]]


def test_delete_refuses_mismatched_pin_without_mutation(environment):
    value=seed_vm();identifier,folder=seed_snapshot(value)
    with pytest.raises(ValueError,match='reference mismatch'):snaps.delete_snapshot('12345678',identifier)
    assert folder.exists() and not environment[1]


def test_delete_job_does_not_require_existing_container(environment,monkeypatch):
    value=seed_vm();identifier,folder=seed_snapshot(value)
    import importlib.machinery,importlib.util
    loader=importlib.machinery.SourceFileLoader('snapshot_job_helper',str(Path(__file__).resolve().parents[1]/'services/helpers/mykos-container-helper'))
    spec=importlib.util.spec_from_loader(loader.name,loader);helper=importlib.util.module_from_spec(spec);loader.exec_module(helper)
    monkeypatch.setattr(helper,'JOBS',environment[0]/'jobs')
    monkeypatch.setattr(helper,'active',lambda code:False)
    monkeypatch.setattr(helper,'save_job',lambda *args:None)
    monkeypatch.setattr(helper,'run',lambda *args:None)
    monkeypatch.setattr(helper,'target',lambda code:(_ for _ in ()).throw(AssertionError('Live container must not be touched')))
    job=helper.dispatch(['delete-snapshot','12345678',identifier])
    assert job['state']=='queued' and job['operation']=='delete-snapshot'
    assert folder.exists()


@pytest.mark.parametrize('blocker',['bpf','namespace','input'])
def test_native_preflight_rejects_known_criu3_blockers_before_capture(environment,monkeypatch,blocker):
    base,calls=environment;proc=base/'proc'
    for pid in ('42','43'):
        folder=proc/pid;(folder/'ns').mkdir(parents=True);(folder/'fd').mkdir()
        (folder/'ns/pid').symlink_to('pid:[10]');(folder/'ns/uts').symlink_to('uts:[20]')
        (folder/'comm').write_text('systemd-udevd\n')
    if blocker=='bpf':(proc/'43/fd/7').symlink_to('anon_inode:bpf-prog')
    elif blocker=='input':(proc/'43/fd/7').symlink_to('/dev/input/event1')
    else:
        (proc/'43/ns/uts').unlink();(proc/'43/ns/uts').symlink_to('uts:[21]')
    monkeypatch.setattr(vm,'run',lambda *a,**k:'Version: 3.18')
    with pytest.raises(ValueError,match={'bpf':'BPF','namespace':'hostname namespace','input':'input/event1'}[blocker]):
        snaps.check_native_compatibility({'State':{'Pid':42}},proc)
    assert not calls


@pytest.mark.parametrize('in_use', [False, True])
def test_hugepage_mount_cleanup_never_forces_used_mount(tmp_path, monkeypatch, in_use):
    init = tmp_path/'42';mount=init/'root/dev/hugepages';mount.mkdir(parents=True)
    (init/'ns').mkdir();(init/'ns/pid').symlink_to('pid:[42]');(init/'ns/mnt').symlink_to('mnt:[42]')
    info=init/'mountinfo'
    info.write_text('10 9 0:1 / /dev/hugepages rw - hugetlbfs hugetlbfs rw\n')
    calls=[]
    if in_use:(mount/'workload-pages').write_bytes(b'')
    def run(args, **kwargs):
        calls.append(args)
        if args[-2:]==['umount','/dev/hugepages']:info.write_text('')
        return ''
    monkeypatch.setattr(vm,'run',run)
    if in_use:
        with pytest.raises(ValueError,match='in use'):
            snaps.prepare_native_mounts('test',{'State':{'Pid':42}},tmp_path)
        assert not calls
    else:
        snaps.prepare_native_mounts('test',{'State':{'Pid':42}},tmp_path)
        assert calls[0][0]=='/usr/bin/nsenter' and calls[0][-2:]==['/usr/bin/umount','/dev/hugepages']
        assert calls[1]==[vm.PODMAN,'exec','test','systemctl','mask','dev-hugepages.mount']


def test_busy_hugepage_unmount_aborts_without_mask(tmp_path,monkeypatch):
    init=tmp_path/'42';(init/'root/dev/hugepages').mkdir(parents=True)
    (init/'ns').mkdir();(init/'ns/pid').symlink_to('pid:[42]');(init/'ns/mnt').symlink_to('mnt:[42]')
    (init/'mountinfo').write_text('10 9 0:1 / /dev/hugepages rw - hugetlbfs hugetlbfs rw\n')
    calls=[]
    def run(args,**kw):
        calls.append(args);raise RuntimeError('target is busy')
    monkeypatch.setattr(vm,'run',run)
    with pytest.raises(RuntimeError,match='busy'):
        snaps.prepare_native_mounts('test',{'State':{'Pid':42}},tmp_path)
    assert len(calls)==1 and 'mask' not in calls[0]



def test_hugepage_cleanup_includes_private_service_namespaces_and_excludes_host(tmp_path,monkeypatch):
    for pid,pid_ns,mnt_ns,has_mount in [(42,42,1,False),(43,42,2,True),(44,1,3,True)]:
        process=tmp_path/str(pid);(process/'root/dev/hugepages').mkdir(parents=True)
        (process/'ns').mkdir();(process/'ns/pid').symlink_to('pid:['+str(pid_ns)+']')
        (process/'ns/mnt').symlink_to('mnt:['+str(mnt_ns)+']')
        (process/'mountinfo').write_text('10 9 0:1 / /dev/hugepages rw - hugetlbfs hugetlbfs rw\n' if has_mount else '')
    calls=[];monkeypatch.setattr(vm,'run',lambda args,**kwargs:calls.append(args) or '')
    snaps.prepare_native_mounts('test',{'State':{'Pid':42}},tmp_path)
    assert len(calls)==2
    assert '--mount='+str(tmp_path/'43/ns/mnt') in calls[0]
    assert all(str(tmp_path/'44') not in ' '.join(call) for call in calls)



def test_configfs_cleanup_requires_empty_exact_type(tmp_path,monkeypatch):
    init=tmp_path/'42';(init/'root/sys/kernel/config').mkdir(parents=True)
    (init/'ns').mkdir();(init/'ns/pid').symlink_to('pid:[42]');(init/'ns/mnt').symlink_to('mnt:[42]')
    (init/'mountinfo').write_text('10 9 0:1 / /sys/kernel/config rw - configfs configfs rw\n')
    calls=[];monkeypatch.setattr(vm,'run',lambda args,**kwargs:calls.append(args) or '')
    snaps.prepare_native_mounts('test',{'State':{'Pid':42}},tmp_path)
    assert calls[0][-1]=='/sys/kernel/config'
    assert calls[1][-1]=='sys-kernel-config.mount'
    calls.clear();(init/'root/sys/kernel/config/active-subsystem').mkdir()
    with pytest.raises(ValueError,match='in use'):snaps.prepare_native_mounts('test',{'State':{'Pid':42}},tmp_path)
    assert not calls


def test_disk_size_uses_allocated_blocks_and_ignores_links(tmp_path):
    from mykos_snapshots import allocated_bytes
    disk=tmp_path/'disk';disk.write_bytes(b'x'*8192)
    outside=tmp_path.parent/'outside-size';outside.write_bytes(b'x'*16384)
    (tmp_path/'link').symlink_to(outside)
    assert allocated_bytes(tmp_path)==disk.stat().st_blocks*512


def test_native_data_mounts_excludes_only_readonly_kernel_cgroup(tmp_path,monkeypatch):
    original=Path.resolve
    def resolve(path,**kwargs):
        return Path('/sys/fs/cgroup') if str(path)=='/sys/fs/cgroup' else original(path,**kwargs)
    monkeypatch.setattr(Path,'resolve',resolve)
    data=tmp_path/'data';data.mkdir()
    config=tmp_path/'config';config.write_text('operator config')
    controller={'Type':'bind','Source':'/sys/fs/cgroup','Destination':'/sys/fs/cgroup','RW':False}
    state={'Mounts':[controller,
        {'Type':'bind','Source':str(data),'Destination':'/etc/ipsec.d','RW':True},
        {'Type':'bind','Source':str(config),'Destination':'/etc/config','RW':False}]}
    assert snaps.native_data_mounts(state)==[
        {'source':str(data),'destination':'/etc/ipsec.d','rw':True},
        {'source':str(config),'destination':'/etc/config','rw':False}]
    # Do not silently skip a different mapping or an unexpected writable mount.
    for change in ({'RW':True},{'Destination':'/saved-data'},{'Type':'volume'}):
        assert len(snaps.native_data_mounts({'Mounts':[{**controller,**change}]}))==1


@pytest.mark.parametrize('source',['cgroup','cgroup2'])
def test_native_data_mounts_skips_podman5_synthetic_cgroup_before_resolving(source,tmp_path):
    data=tmp_path/'data';data.mkdir()
    state={'Mounts':[
        {'Type':'bind','Source':source,'Destination':'/sys/fs/cgroup','RW':True},
        {'Type':'bind','Source':str(data),'Destination':'/etc/ipsec.d','RW':True}]}
    assert snaps.native_data_mounts(state)==[{'source':str(data),'destination':'/etc/ipsec.d','rw':True}]
    # Other relative/nonexistent sources remain invalid, rather than silently
    # dropping mounted application data from a successful snapshot.
    with pytest.raises(FileNotFoundError):
        snaps.native_data_mounts({'Mounts':[{'Type':'bind','Source':source,'Destination':'/data','RW':True}]})


def netns_fixture(tmp_path):
    for pid,pidns,netns in [(42,42,7),(43,42,8),(99,1,9)]:
        p=tmp_path/str(pid);(p/'ns').mkdir(parents=True)
        (p/'ns/pid').symlink_to('pid:['+str(pidns)+']');(p/'ns/net').symlink_to('net:['+str(netns)+']')
        (p/'comm').write_text('startup-service');(p/'stat').write_text(str(pid)+' (init) S '+'0 '*18+'12345')
    return {'State':{'Pid':42}}


def test_snapshot_waits_for_transient_guest_netns_without_touching_host(tmp_path,monkeypatch):
    state=netns_fixture(tmp_path);waited=[]
    def settle(seconds):
        waited.append(seconds);p=tmp_path/'43/ns/net';p.unlink();p.symlink_to('net:[7]')
    monkeypatch.setattr(snaps.time,'sleep',settle)
    snaps.wait_native_network(state,tmp_path,timeout=2)
    assert waited==[.5] and (tmp_path/'99/ns/net').readlink()==Path('net:[9]')


def test_persistent_guest_netns_aborts_before_any_capture(tmp_path):
    state=netns_fixture(tmp_path)
    with pytest.raises(ValueError,match='left running'):snaps.wait_native_network(state,tmp_path,timeout=0)


def test_optional_unmount_rechecks_service_namespace_after_propagation(tmp_path,monkeypatch):
    for pid in (42,43):
        process=tmp_path/str(pid);(process/'root/sys/kernel/config').mkdir(parents=True)
        (process/'ns').mkdir();(process/'ns/pid').symlink_to('pid:[42]');(process/'ns/mnt').symlink_to('mnt:['+str(pid)+']')
        (process/'mountinfo').write_text('10 9 0:1 / /sys/kernel/config rw - configfs configfs rw\n')
    calls=[]
    def execute(args,**kwargs):
        calls.append(args)
        if args[0]=='/usr/bin/nsenter':
            (tmp_path/'42/mountinfo').write_text('');(tmp_path/'43/mountinfo').write_text('')
        return ''
    monkeypatch.setattr(vm,'run',execute)
    snaps.prepare_native_mounts('test',{'State':{'Pid':42}},tmp_path)
    assert sum(call[0]=='/usr/bin/nsenter' for call in calls)==1
    assert calls[-1][-2:]==['mask','sys-kernel-config.mount']


def test_optional_unmount_rejects_host_namespace_before_writing(tmp_path,monkeypatch):
    for name in ('42','self'):
        p=tmp_path/name;(p/'ns').mkdir(parents=True);(p/'ns/pid').symlink_to('pid:[1]');(p/'ns/mnt').symlink_to('mnt:[1]')
    monkeypatch.setattr(vm,'run',lambda *a,**k:pytest.fail('Host namespace must not be modified'))
    with pytest.raises(ValueError,match='host PID namespace'):
        snaps.prepare_native_mounts('test',{'State':{'Pid':42}},tmp_path)


@pytest.mark.parametrize('version,supported',[('3.19',False),('4.0',False),('4.1',True),('4.2.1',True)])
def test_pidfd_capability_is_checked_before_seizing_a_container(environment,monkeypatch,version,supported):
    proc=environment[0]/'proc';p=proc/'42';(p/'ns').mkdir(parents=True);(p/'fd').mkdir()
    (p/'ns/pid').symlink_to('pid:[42]');(p/'ns/uts').symlink_to('uts:[42]');(p/'fd/7').symlink_to('anon_inode:[pidfd]')
    monkeypatch.setattr(vm,'run',lambda *a,**k:'Version: '+version)
    if supported:snaps.check_native_compatibility({'State':{'Pid':42}},proc)
    else:
        with pytest.raises(ValueError,match='CRIU 4.1'):
            snaps.check_native_compatibility({'State':{'Pid':42}},proc)
