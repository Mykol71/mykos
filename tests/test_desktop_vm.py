"""Desktop lifecycle tests use temporary disks and mocked Podman only."""
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_vm as vm


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(vm, 'host_config', lambda: {})
    monkeypatch.setattr(vm, 'publish_started', lambda shop: None)
    monkeypatch.setattr(vm, 'verify_windows_installation', lambda value: None)
    monkeypatch.setattr(vm.network, 'arguments', lambda cfg, name: ['--network', 'mykos-lan', '--ip', '192.168.22.2'])
    monkeypatch.setattr(vm, 'POS', tmp_path)
    monkeypatch.setattr(vm, 'ROOT', tmp_path/'.vm')
    monkeypatch.setattr(vm, 'storage_root', lambda: vm.ROOT)
    monkeypatch.setattr(vm, 'preflight', lambda os, ram_mib=2048: None)
    monkeypatch.setattr(vm, 'ubuntu_iso', lambda version:'https://example.org/ubuntu.iso')
    monkeypatch.setattr(vm, 'settings', lambda: ('192.0.2.10','127.0.0.1'))
    monkeypatch.setattr(vm, 'allocate', lambda bind: (18006,23000))
    calls=[]; stopped=set(); removed=set()
    def run(args, **kwargs):
        calls.append(args)
        if args[1]=='stop': stopped.add(args[-1])
        if args[1]=='start': stopped.discard(args[-1])
        if args[1]=='rm': removed.add(args[-1])
        if args[1]=='run':
            stopped.discard(args[args.index('--name')+1]);removed.discard(args[args.index('--name')+1])
        if args[1:3] == ['ps','-a'] or args[1]=='images': return '[]'
        if args[1:3] == ['image','inspect']: return '[{"Id":"sha256:abc"}]'
        if args[1:3]==['container','inspect']:
            if args[-1] in removed:raise RuntimeError('No such container')
            key=args[-1] if args[-1] in vm.VERSIONS else args[-1].split('.')[0];path=vm.ROOT/(key+'.json')
            value=json.loads(path.read_text()) if path.exists() else {'os':key,'role':'template'}
            return json.dumps([{'Id':'id-'+args[-1],'State':{'Running':args[-1] not in stopped,'ExitCode':0},'Config':{'Labels':{'io.mykos.vm.os':value['os'],'io.mykos.vm.role':value['role']}}}])
        return 'container-id'
    monkeypatch.setattr(vm, 'run', run)
    class Monitor:
        def __init__(self,value): self.paused=False; self.value=value; self.migrated=False
        def command(self,command):
            calls.append(['monitor',command])
            if command=='stop': self.paused=True
            if command=='cont': self.paused=False
            if command.startswith('migrate -d'):
                self.migrated=True;self.paused=True
                folder=vm.storage(self.value['os'])/'storage/.mykos-checkpoint'
                folder.mkdir(exist_ok=True);(folder/'memory.gz').write_bytes(b'compressed memory')
            if command=='info migrate':return 'Status: completed' if self.migrated else ''
            if command=='info registers':return 'RIP=0000abc CR3=0000def'
            if command=='info version':return 'QEMU test version'
            if command=='migrate_cancel':self.migrated=False
            if command=='info status':return 'VM status: paused' if self.paused else 'VM status: running'
            if command=='info block-jobs':return 'No active jobs'
            if command=='info block':return 'data3 (#block1): /storage/data.img (raw)'
            return ''
        def close(self): pass
    monkeypatch.setattr(vm,'QemuMonitor',Monitor)
    return tmp_path,calls


def test_template_remains_running_and_credentials_not_in_metadata(desktop):
    root,calls=desktop
    result=vm.create('win11','operator','private-password')
    assert vm.read('win11')['ram_mib']==2048
    oem=root/'.vm/win11/oem'
    assert '-SizeMiB 12288' in (oem/'install.bat').read_text()
    assert r'C:\OEM\mykos-pagefile.ps1' in (oem/'install.bat').read_text()
    assert result['console_url']=='/mykos/console/win11/'
    assert result['direct_console_url']=='http://192.0.2.10:18006/'
    assert result['login_url']=='rdp://192.0.2.10:23000'
    assert result['ready'] is False
    launch=next(command for command in calls if command[1]=='run')
    assert '--rm' not in launch and '--privileged' not in launch
    assert '--device=/dev/kvm' in launch and '--stop-timeout=120' in launch
    assert '--restart=no' in launch
    assert 'QGA=/dev/shm/mykos-qga.sock' in launch
    assert '127.0.0.1:23000:3389/udp' in launch
    assert 'private-password' not in repr(calls)
    assert 'private-password' not in (root/'.vm/win11.json').read_text()
    assert (root/'.vm/win11/windows.env').stat().st_mode & 0o777 == 0o600


def test_live_capture_stops_and_commits_after_snapshot(desktop, monkeypatch):
    root,calls=desktop
    vm.create('mac15')
    events=[]
    (root/'.vm/mac15/storage/data.img').write_bytes(b'installed guest')
    def copy(source,dest,reflink):
        assert reflink=='always'
        assert ['monitor','qemu-io data3 \"flush\"'] in calls
        import shutil
        shutil.copytree(source,dest); (dest/'disk.img').write_bytes(b'installed guest')
        events.append('copied')
    monkeypatch.setattr(vm,'tree_copy',copy)
    result=vm.prepare('mac15')
    assert events==['copied'] and result['ready'] is True
    assert ['monitor','cont'] in calls
    assert [vm.PODMAN,'stop','--time','120','mac15'] in calls
    assert calls.index([vm.PODMAN,'stop','--time','120','mac15']) > calls.index(['monitor','cont'])
    assert (root/'.vm/mac15/golden/disk.img').read_bytes()==b'installed guest'
    assert [vm.PODMAN,'commit','mac15','localhost/macos15:latest'] in calls
    assert [vm.PODMAN,'rm','mac15'] in calls
    assert vm.info('mac15')['running'] is False
    assert vm.read('mac15')['image_name']=='localhost/macos15:latest'


def test_capture_failure_resumes_without_marking_ready(desktop,monkeypatch):
    root,calls=desktop; vm.create('mac15')
    (root/'.vm/mac15/storage/data.img').write_bytes(b'installed guest')
    def fail(*args,**kwargs): raise OSError('disk full')
    monkeypatch.setattr(vm,'tree_copy',fail)
    with pytest.raises(OSError): vm.prepare('mac15')
    assert calls[-2]==['monitor','cont']
    assert calls[-1]==['monitor','info status']
    assert not (root/'.vm/mac15/golden').exists()
    assert not list((root/'.vm/mac15').glob('.capture-*'))
    assert vm.read('mac15')['ready'] is False


def test_instance_uses_independent_prepared_storage(desktop,monkeypatch):
    root,calls=desktop; vm.create('mac15')
    with vm.locked():
        value=vm.read('mac15'); value['ready']=True;vm.save('mac15',value)
    golden=root/'.vm/mac15/golden';golden.mkdir();(golden/'disk.img').write_text('golden')
    import shutil
    monkeypatch.setattr(vm,'tree_copy',shutil.copytree)
    monkeypatch.setattr(vm,'allocate',lambda bind:(18007,23001))
    result=vm.start('01234567','mac15')
    assert result['console_port']==18007 and result['desktop_port']==23001
    assert result['protocol']=='vnc'
    assert (root/'.01234567.container').exists()
    (root/'.vm/01234567/storage/disk.img').write_text('changed')
    assert (golden/'disk.img').read_text()=='golden'
    before=(root/'.vm/01234567.json').read_bytes();vm.stop('01234567')
    assert (root/'.vm/01234567.json').read_bytes()==before
    assert (root/'.vm/01234567/storage/disk.img').read_text()=='changed'


def test_ubuntu_uses_qemu_and_version_matched_boot(desktop,monkeypatch):
    root,calls=desktop
    monkeypatch.setattr(vm,'ubuntu_iso',lambda version:'https://releases.ubuntu.com/'+version+'/installer.iso')
    vm.create('ubuntu24.04')
    assert any(command[1:]==['pull','docker.io/qemux/qemu:latest'] for command in calls)
    launch=next(command for command in calls if command[1]=='run')
    assert 'BOOT=https://releases.ubuntu.com/24.04/installer.iso' in launch
    assert '127.0.0.1:23000:5900/tcp' in launch


def test_reject_bad_selection_and_symlink_storage(desktop):
    root,calls=desktop
    for key in ['../escape','--all','1234','win-unknown']:
        with pytest.raises(ValueError): vm.read(key)
    source=root/'real';source.mkdir();(root/'linked').symlink_to(source)
    with pytest.raises(ValueError): vm.tree_copy(root/'linked',root/'clone')
    assert calls==[]


def test_capture_rejects_external_disk_before_pause(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    original=vm.QemuMonitor
    class External(original):
        def command(self,command):
            if command=='info block':return 'data3 (#block1): /other/data.img (raw)'
            return super().command(command)
    monkeypatch.setattr(vm,'QemuMonitor',External)
    with pytest.raises(ValueError,match='outside template storage'):vm.prepare('mac15')
    assert ['monitor','stop'] not in calls
    assert vm.read('mac15')['ready'] is False


def test_flush_failure_resumes_and_does_not_copy(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    (root/'.vm/mac15/storage/data.img').write_bytes(b'installed guest')
    original=vm.QemuMonitor
    class BadFlush(original):
        def command(self,command):
            if command.startswith('qemu-io'):return 'flush: Input/output error'
            return super().command(command)
    monkeypatch.setattr(vm,'QemuMonitor',BadFlush)
    with pytest.raises(RuntimeError,match='flush failed'):vm.prepare('mac15')
    assert ['monitor','cont'] in calls
    assert not (root/'.vm/mac15/golden').exists()
    assert vm.read('mac15')['ready'] is False


def test_macos_version_subdirectory_capture_flushes_data_and_firmware(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    directory=root/'.vm/mac15/storage/15';directory.mkdir()
    (directory/'data.img').write_bytes(b'macOS installed disk')
    (directory/'macos.vars').write_bytes(b'firmware state')
    (directory/'boot.img').write_bytes(b'bootloader')
    original=vm.QemuMonitor
    class MacMonitor(original):
        def command(self,command):
            if command=='info block':return ('data3 (#block389): /storage/15/data.img (raw)\n'
                '    Cache mode: writeback\n\n'
                'OpenCore (#block510): /storage/15/boot.img (raw, read-only)\n\n'
                'pflash1 (#block998): /storage/15/macos.vars (raw)')
            return super().command(command)
    monkeypatch.setattr(vm,'QemuMonitor',MacMonitor)
    import shutil
    def copy(source,destination,reflink):
        assert reflink=='always'
        assert ['monitor','qemu-io data3 "flush"'] in calls
        assert ['monitor','qemu-io pflash1 "flush"'] in calls
        shutil.copytree(source,destination)
    monkeypatch.setattr(vm,'tree_copy',copy)
    assert vm.prepare('mac15')['ready']
    assert (root/'.vm/mac15/golden/15/data.img').read_bytes()==b'macOS installed disk'
    assert (root/'.vm/mac15/golden/15/macos.vars').read_bytes()==b'firmware state'
    assert ['monitor','cont'] in calls
    assert [vm.PODMAN,'stop','--time','120','mac15'] in calls
    assert calls.index([vm.PODMAN,'stop','--time','120','mac15']) > calls.index(['monitor','cont'])


@pytest.mark.parametrize('filename',['/storage/15/../../escape/data.img','/storage/linked/data.img'])
def test_nested_capture_rejects_traversal_and_directory_symlinks(desktop,monkeypatch,filename):
    root,calls=desktop;vm.create('mac15')
    outside=root/'outside';outside.mkdir();(outside/'data.img').write_bytes(b'outside')
    (root/'.vm/mac15/storage/linked').symlink_to(outside,target_is_directory=True)
    original=vm.QemuMonitor
    class BadMonitor(original):
        def command(self,command):
            if command=='info block':return 'data3 (#block1): '+filename+' (raw)'
            return super().command(command)
    monkeypatch.setattr(vm,'QemuMonitor',BadMonitor)
    with pytest.raises(ValueError):vm.prepare('mac15')
    assert ['monitor','stop'] not in calls
    assert vm.read('mac15')['ready'] is False


def test_disk_template_upgrades_and_discards_superseded_capture(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    (root/'.vm/mac15/storage/data.img').write_bytes(b'current disk')
    old=root/'.vm/mac15/golden';old.mkdir();(old/'disk.img').write_bytes(b'previous capture')
    value=vm.read('mac15');value.update(ready=True,capture_mode='live-disk');vm.save('mac15',value)
    import shutil
    monkeypatch.setattr(vm,'tree_copy',lambda source,destination,**kwargs:shutil.copytree(source,destination))
    assert vm.prepare('mac15')['capture_mode']=='memory'
    assert not list((root/'.vm/mac15').glob('.golden-previous-*'))
    manifest=json.loads((old/'.mykos-checkpoint/manifest.json').read_text())
    assert manifest['registers']=={'RIP':'0000abc','CR3':'0000def'}
    assert (old/'.mykos-checkpoint/memory.gz').is_file()


def test_new_instance_requests_memory_restore_once(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    (root/'.vm/mac15/storage/data.img').write_bytes(b'installed disk')
    import shutil
    monkeypatch.setattr(vm,'tree_copy',lambda source,destination,**kwargs:shutil.copytree(source,destination))
    vm.prepare('mac15')
    restored=[]
    def restore(value,disk):
        vm.restore_manifest(value,disk)
        request=disk/'.mykos-checkpoint/restore.request';assert request.is_file()
        request.rename(request.with_name('restore.consumed'));restored.append(value['name'])
    monkeypatch.setattr(vm,'wait_for_restore',restore)
    vm.start('01234567','mac15')
    assert len(restored)==1 and vm.read('01234567')['last_start']=='memory-restore'
    vm.start('01234567')
    assert vm.read('01234567')['last_start']=='memory-restore'
    vm.stop('01234567');vm.start('01234567')
    assert len(restored)==1 and vm.read('01234567')['last_start']=='boot'
    assert not (root/'.vm/01234567/storage/.mykos-checkpoint/restore.request').exists()


@pytest.mark.parametrize('mismatch',[False,True])
def test_restore_verifies_cpu_state_before_resuming(desktop,monkeypatch,mismatch):
    root,calls=desktop;vm.create('mac15')
    disk=root/'.vm/mac15/storage';checkpoint=disk/'.mykos-checkpoint';checkpoint.mkdir()
    (checkpoint/'memory.gz').write_bytes(b'memory')
    manifest={'format':1,'os':'mac15','image':'sha256:abc','cpu_env':{'CPU_FLAGS':'-invtsc'},'memory_bytes':6,
              'registers':{'RIP':'wrong' if mismatch else '0000abc','CR3':'0000def'}}
    (checkpoint/'manifest.json').write_text(json.dumps(manifest))
    original=vm.QemuMonitor
    class Restored(original):
        def __init__(self,value):super().__init__(value);self.migrated=True;self.paused=True
    monkeypatch.setattr(vm,'QemuMonitor',Restored)
    if mismatch:
        with pytest.raises(RuntimeError,match='CPU state'):vm.wait_for_restore(vm.read('mac15'),disk)
        assert ['monitor','cont'] not in calls
    else:
        vm.wait_for_restore(vm.read('mac15'),disk)
        assert ['monitor','cont'] in calls


def test_cpu_profile_mismatch_rejects_checkpoint(desktop):
    root,calls=desktop;vm.create('win11','operator','private-password')
    disk=root/'.vm/win11/storage';checkpoint=disk/'.mykos-checkpoint';checkpoint.mkdir()
    (checkpoint/'memory.gz').write_bytes(b'memory')
    manifest={'format':1,'os':'win11','image':'sha256:abc','cpu_env':{},'memory_bytes':6}
    (checkpoint/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='CPU profile'):vm.restore_manifest(vm.read('win11'),disk)


def test_enable_checkpoint_restarts_legacy_windows_and_retains_container(desktop):
    root,calls=desktop;vm.create('win11','operator','private-password')
    value=vm.read('win11');value.pop('cpu_env');vm.save('win11',value)
    vm.checkpoint_enable('win11')
    updated=vm.read('win11')
    assert updated['cpu_env']=={'CPU_MODEL':'host','CPU_FLAGS':'migratable=on,-invtsc','HV':'N'}
    assert updated['previous_container'].startswith('win11.before-checkpoint-')
    assert any(command[1]=='rename' and command[2]=='win11' for command in calls)
    launch=[command for command in calls if command[1]=='run'][-1]
    assert 'CPU_FLAGS=migratable=on,-invtsc' in launch and 'HV=N' in launch


def test_enable_checkpoint_failure_restores_previous_container_and_profile(desktop,monkeypatch):
    root,calls=desktop;vm.create('win11','operator','private-password')
    value=vm.read('win11');value.pop('cpu_env');vm.save('win11',value)
    def fail(*args):raise RuntimeError('replacement failed')
    monkeypatch.setattr(vm,'launch',fail)
    with pytest.raises(RuntimeError,match='replacement failed'):vm.checkpoint_enable('win11')
    assert vm.read('win11')==value
    assert calls[-1]==[vm.PODMAN,'start','win11']


def test_failed_memory_migration_cancels_and_resumes_source(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    (root/'.vm/mac15/storage/data.img').write_bytes(b'installed disk')
    original=vm.QemuMonitor
    class Failed(original):
        def command(self,command):
            if command=='info migrate' and self.migrated:return 'Status: failed'
            return super().command(command)
    monkeypatch.setattr(vm,'QemuMonitor',Failed)
    with pytest.raises(RuntimeError,match='Memory checkpoint failed'):vm.prepare('mac15')
    assert ['monitor','migrate_cancel'] in calls
    assert ['monitor','cont'] in calls
    assert not (root/'.vm/mac15/golden').exists()
    assert vm.read('mac15')['ready'] is False


def test_commit_failure_keeps_previous_checkpoint_and_metadata(desktop, monkeypatch):
    root,calls=desktop;vm.create('mac15')
    (vm.storage('mac15')/'storage/data.img').write_bytes(b'disk')
    import shutil
    monkeypatch.setattr(vm,'tree_copy',lambda source,destination,**kwargs:shutil.copytree(source,destination))
    vm.prepare('mac15')
    before=vm.record_path('mac15').read_bytes()
    golden=(vm.storage('mac15')/'golden/.mykos-checkpoint/manifest.json').read_bytes()
    vm.resume('mac15')
    before=vm.record_path('mac15').read_bytes()
    original=vm.run
    def fail(args,**kwargs):
        if args[1]=='commit':raise RuntimeError('commit failed')
        return original(args,**kwargs)
    monkeypatch.setattr(vm,'run',fail)
    with pytest.raises(RuntimeError,match='commit failed'):vm.prepare('mac15',replace=True)
    after=vm.read('mac15');pending=after.pop('pending_commit')
    assert after==json.loads(before)
    assert (vm.storage('mac15')/pending['directory']/'golden/.mykos-checkpoint/manifest.json').is_file()
    assert (vm.storage('mac15')/'golden/.mykos-checkpoint/manifest.json').read_bytes()==golden


def test_launch_uses_readable_name_and_retains_old_checkpoint_image(desktop, monkeypatch):
    root,calls=desktop;vm.create('mac15')
    launch=next(command for command in calls if command[1]=='run')
    assert 'localhost/macos15:latest' in launch and 'sha256:abc' not in launch
    original=vm.run
    def moved(args,**kwargs):
        if args[1:3]==['image','inspect']:return '[{"Id":"sha256:new"}]'
        return original(args,**kwargs)
    monkeypatch.setattr(vm,'run',moved)
    assert vm.launch_image(vm.read('mac15'))=='localhost/macos15:checkpoint-abc'
    assert [vm.PODMAN,'tag','sha256:abc','localhost/macos15:checkpoint-abc'] in calls


def test_windows_commit_clears_private_credentials_and_updates_manifest(desktop, monkeypatch):
    root,calls=desktop;vm.create('win11','operator','private-password')
    value=vm.read('win11');value.update(ram_mib=2048,runtime_memory_prepared=True);vm.save('win11',value)
    (vm.storage('win11')/'storage/data.img').write_bytes(b'disk')
    import shutil
    monkeypatch.setattr(vm,'tree_copy',lambda source,destination,**kwargs:shutil.copytree(source,destination))
    original=vm.run
    def committed(args,**kwargs):
        if args[1:3]==['image','inspect'] and any(c[1]=='commit' for c in calls):
            return '[{"Id":"sha256:committed"}]'
        return original(args,**kwargs)
    monkeypatch.setattr(vm,'run',committed)
    vm.prepare('win11')
    command=next(c for c in calls if c[1]=='commit')
    assert command[-2:]==['win11','localhost/win11:latest']
    assert 'ENV PASSWORD=' in command and 'ENV USERNAME=' in command
    assert 'private-password' not in repr(calls)
    value=vm.read('win11');assert value['image']=='sha256:committed'
    vm.restore_manifest(value,vm.storage('win11')/'golden')


def test_saved_template_selectable_after_builder_removed(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    (vm.storage('mac15')/'storage/data.img').write_bytes(b'disk')
    import shutil
    monkeypatch.setattr(vm,'tree_copy',lambda source,destination,**kwargs:shutil.copytree(source,destination))
    vm.prepare('mac15')
    assert [vm.PODMAN,'rm','mac15'] in calls
    assert vm.prepared_templates()==[{'os':'mac15','image':'sha256:abc'}]
    assert vm.info('mac15')['running'] is False
    vm.resume('mac15')
    assert vm.info('mac15')['running'] is True
    assert (vm.storage('mac15')/'golden/.mykos-checkpoint/memory.gz').is_file()


@pytest.mark.parametrize('running,owned',[(True,True),(False,False)])
def test_cleanup_refuses_running_or_unrelated_builder(desktop,monkeypatch,running,owned):
    root,calls=desktop;vm.create('mac15')
    value=vm.read('mac15');value.update(ready=True,committed=True)
    monkeypatch.setattr(vm,'inspect',lambda name:{'State':{'Running':running},'Config':{'Labels':{
        'io.mykos.vm.os':'mac15','io.mykos.vm.role':'template' if owned else 'instance'}}})
    with pytest.raises(ValueError):vm.cleanup_template(value)
    assert not any(c[1]=='rm' for c in calls)


def test_unused_upstream_removed_without_force_or_volumes(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    value=vm.read('mac15');value.update(ready=True,committed=True,image='sha256:committed')
    vm.save('mac15',value)
    vm.stop('mac15')
    vm.cleanup('mac15')
    assert [vm.PODMAN,'rm','mac15'] in calls
    assert [vm.PODMAN,'image','rm','docker.io/dockurr/macos:latest'] in calls
    assert not any('--force' in c or '--volumes' in c or 'prune' in c for c in calls)


def test_capture_failure_does_not_remove_builder(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    monkeypatch.setattr(vm,'capture_live',lambda *args:(_ for _ in ()).throw(RuntimeError('capture failed')))
    with pytest.raises(RuntimeError,match='capture failed'):vm.prepare('mac15')
    assert not any(c[1]=='rm' or c[1:3]==['image','rm'] for c in calls)


def test_shared_upstream_layers_only_lose_vendor_tag(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    value=vm.read('mac15');value.update(ready=True,committed=True,image='sha256:committed');vm.save('mac15',value)
    vm.stop('mac15')
    original=vm.run
    def shared(args,**kwargs):
        if args[1:3]==['image','rm']:raise RuntimeError('image has dependent child images')
        return original(args,**kwargs)
    monkeypatch.setattr(vm,'run',shared)
    vm.cleanup('mac15')
    assert [vm.PODMAN,'untag','sha256:abc','docker.io/dockurr/macos:latest'] in calls


def test_upstream_pinned_by_other_checkpoint_is_retained(desktop):
    root,calls=desktop;vm.create('mac15')
    value=vm.read('mac15');value.update(ready=True,committed=True,image='sha256:committed');vm.save('mac15',value)
    other={**value,'os':'mac14','image':'sha256:abc'};vm.save('mac14',other)
    vm.stop('mac15');vm.cleanup('mac15')
    assert not any(c[1:3]==['image','rm'] or c[1]=='untag' for c in calls)


def test_instance_discards_consumed_ram_but_preserves_pending_restore(desktop):
    root,calls=desktop;vm.create('mac15')
    value=vm.read('mac15');value.update(role='instance',ready=True)
    vm.save('01234567',value)
    checkpoint=vm.storage('01234567')/'storage/.mykos-checkpoint';checkpoint.mkdir(parents=True)
    (checkpoint/'memory.gz').write_bytes(b'RAM')
    (checkpoint/'restore.consumed').touch()
    (checkpoint/'restore.request').touch()
    disk=checkpoint.parent/'data.img';disk.write_bytes(b'current instance disk')
    vm.prune_checkpoints('01234567')
    assert (checkpoint/'memory.gz').exists()
    (checkpoint/'restore.request').unlink()
    vm.prune_checkpoints('01234567')
    assert not checkpoint.exists() and disk.read_bytes()==b'current instance disk'


def test_installing_images_exposes_only_running_unfinished_verified_builds(desktop, monkeypatch):
    root, calls = desktop
    monkeypatch.setattr(vm, 'ubuntu_iso', lambda version: 'https://example.org/ubuntu.iso')
    vm.create('ubuntu24.04')
    assert vm.installing_images() == [{'os':'ubuntu24.04', 'name':'ubuntu24.04', 'unattended':False,
                                      'console_url':'/mykos/console/ubuntu24.04/',
                                      'direct_console_url':'http://192.0.2.10:18006/'}]
    assert vm.prepared_templates() == []
    value = vm.read('ubuntu24.04')
    value['ready'] = True; vm.save('ubuntu24.04', value)
    assert vm.installing_images() == []
    value['ready'] = False; vm.save('ubuntu24.04', value)
    vm.run([vm.PODMAN, 'stop', value['name']])
    assert vm.installing_images() == []
    monkeypatch.setattr(vm, 'inspect', lambda name: {'State':{'Running':True}, 'Config':{'Labels':{'io.mykos.vm.os':'win11','io.mykos.vm.role':'template'}}})
    assert vm.installing_images() == []


@pytest.mark.parametrize('running,pid,allowed', [(False,0,True), (True,0,False), (False,__import__('os').getpid(),False)])
def test_lost_monitor_stop_requires_guest_exit(monkeypatch,running,pid,allowed):
    monkeypatch.setattr(vm.Path,'exists',lambda p: pid != 0 and str(p) == '/proc/' + str(pid))
    states=iter([{'State':{'Running':True,'Pid':pid}},
                 {'State':{'Running':running,'Status':'running' if running else 'exited'}},
                 {'State':{'Running':False,'Status':'exited'}}])
    monkeypatch.setattr(vm,'inspect',lambda name:next(states))
    def fail(*args,**kwargs): raise RuntimeError('conmon exited prematurely: internal libpod error')
    monkeypatch.setattr(vm,'run',fail)
    if allowed:vm.stop_container('guest')
    else:
        with pytest.raises(RuntimeError,match='storage was left intact'):vm.stop_container('guest')


def test_stop_does_not_swallow_unrelated_podman_errors(monkeypatch):
    monkeypatch.setattr(vm,'inspect',lambda name:{'State':{'Running':True}})
    def fail(*args,**kwargs):raise RuntimeError('permission denied')
    monkeypatch.setattr(vm,'run',fail)
    with pytest.raises(RuntimeError,match='permission denied'):vm.stop_container('guest')


def test_snapshot_replacement_pauses_flushes_then_uses_short_stop(desktop):
    root,calls=desktop
    vm.create('mac15');value=vm.read('mac15');disk=vm.storage('mac15')/'storage'
    (disk/'data.img').write_bytes(b'guest disk')
    vm.stop_for_restore(value,disk)
    pause=calls.index(['monitor','stop'])
    flush=calls.index(['monitor','qemu-io data3 "flush"'])
    stop=next(i for i,c in enumerate(calls) if c[:2]==[vm.PODMAN,'stop'])
    assert pause < flush < stop
    assert calls[stop]==[vm.PODMAN,'stop','--time','2','mac15']
    assert not vm.inspect('mac15')['State']['Running']


def test_failed_restore_flush_resumes_original_without_stopping(desktop,monkeypatch):
    root,calls=desktop
    vm.create('mac15');value=vm.read('mac15');disk=vm.storage('mac15')/'storage'
    (disk/'data.img').write_bytes(b'guest disk')
    original=vm.QemuMonitor
    class FailedFlush(original):
        def command(self,command):
            if command.startswith('qemu-io '):raise RuntimeError('flush failed')
            return super().command(command)
    monkeypatch.setattr(vm,'QemuMonitor',FailedFlush)
    with pytest.raises(RuntimeError,match='flush failed'):vm.stop_for_restore(value,disk)
    assert ['monitor','cont'] in calls
    assert not any(c[:2]==[vm.PODMAN,'stop'] for c in calls)
    assert vm.inspect('mac15')['State']['Running']


@pytest.mark.parametrize('os_name', ['ubuntu20.04','ubuntu22.04','ubuntu24.04'])
def test_ubuntu_build_cpu_supports_capture_and_legacy_recovery(desktop,monkeypatch,os_name):
    root,calls=desktop
    monkeypatch.setattr(vm,'ubuntu_iso',lambda version:'https://example.org/ubuntu.iso')
    vm.create(os_name)
    profile={'CPU_MODEL':'host','CPU_FLAGS':'migratable=on,-invtsc'}
    assert vm.read(os_name)['cpu_env']==profile
    launch=next(c for c in calls if c[1]=='run')
    assert 'CPU_MODEL=host' in launch and 'CPU_FLAGS=migratable=on,-invtsc' in launch
    assert 'HV=N' not in launch
    value=vm.read(os_name);value['cpu_env']={};vm.save(os_name,value)
    vm.checkpoint_enable(os_name)
    updated=vm.read(os_name)
    assert updated['cpu_env']==profile
    assert updated['previous_container'].startswith(os_name+'.before-checkpoint-')
    count=len(calls);vm.checkpoint_enable(os_name);assert len(calls)==count


def test_explicit_nested_virtualization_setting_survives_launch(desktop):
    root,calls=desktop
    vm.create('win11','operator','private-password')
    value=vm.read('win11')
    value['cpu_env']={'VMX':'Y'}
    vm.save('win11',value)
    vm.launch('win11',vm.read('win11'))
    launch=[command for command in calls if command[1]=='run'][-1]
    assert 'VMX=Y' in launch
    assert 'CPU_FLAGS=+vmx' not in launch


def test_old_image_ram_rejected_before_creating_instance(desktop):
    root,calls=desktop
    vm.create('mac15')
    value=vm.read('mac15');value.update(ready=True,ram_mib=4096);vm.save('mac15',value)
    with pytest.raises(ValueError,match='Recapture.*2048'):vm.start('01234567','mac15')
    assert not vm.record_path('01234567').exists()
    assert not vm.storage('01234567').exists()


@pytest.mark.parametrize('os_name', ['win10', 'win11', 'wns2022', 'mac15', 'ubuntu22.04'])
def test_image_build_nice_10_does_not_leak_to_instances(desktop, os_name):
    root, calls = desktop
    if os_name.startswith(('win', 'wns')):
        vm.create(os_name, 'operator', 'private-password')
    else:
        vm.create(os_name)
    command = next(c for c in calls if c[1] == 'run')
    windows = os_name.startswith(('win', 'wns'))
    assert 'MYKOS_BUILD_NICE=10' in command
    assert ('--cap-add=SYS_NICE' in command) == windows
    value = vm.read(os_name)
    value.update(role='instance', name='12345678.test', ready=True, ram_mib=2048)
    calls.clear()
    vm.launch('12345678', value)
    command = next(c for c in calls if c[1] == 'run')
    assert 'MYKOS_BUILD_NICE=0' in command
    assert '--cap-add=SYS_NICE' not in command


def runtime_builder(desktop, monkeypatch):
    import mykos_commands
    root,calls=desktop
    vm.create('win10','operator','private-password')
    value=vm.read('win10');value['ram_mib']=8192;vm.save('win10',value)
    source=vm.storage('win10')/'storage'
    (source/'data.img').write_bytes(b'installed Windows disk')
    old_inspect=vm.inspect
    def inspect(name):
        state=old_inspect(name)
        state['State']['Pid']=123
        return state
    monkeypatch.setattr(vm,'inspect',inspect)
    stat=Path.stat
    def safe_stat(path,*args,**kwargs):
        if str(path)=='/proc/123/root/storage': return stat(source)
        return stat(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',safe_stat)
    def powershell(value,script,timeout,**kwargs):
        assert kwargs=={'allow_image_builder':True}
        if 'Set-CimInstance' in script:
            assert 'AutomaticManagedPagefile=$true' in script
            calls.append(['pagefile','automatic'])
        else:
            assert 'TotalPhysicalMemory' in script
            calls.append(['pagefile','verify'])
        return {'exit_code':0,'timed_out':False,'output':'Automatic paging enabled'}
    monkeypatch.setattr(mykos_commands,'powershell',powershell)
    calls.clear()
    return root,calls


def test_windows_build_restarts_at_two_gib_before_capture(desktop,monkeypatch):
    root,calls=runtime_builder(desktop,monkeypatch)
    result=vm.prepare('win10')
    assert result['capture_pending'] and not result['ready']
    value=vm.read('win10')
    assert value['ram_mib']==2048 and value['runtime_pagefile']=='automatic'
    assert value['name']=='win10' and value['runtime_memory_prepared']
    command=next(c for c in calls if c[1]=='run')
    assert 'RAM_SIZE=2048M' in command and 'MANUAL=Y' in command and 'SHUTDOWN=N' in command
    assert not any(c[1]=='commit' for c in calls)
    assert not (vm.storage('win10')/'golden').exists()
    assert calls.index(['pagefile','automatic']) < calls.index([vm.PODMAN,'stop','--time','120','win10'])
    assert any(c[1]=='rm' and c[-1].startswith('mykos-recovery-win10-runtime-') and '--force' not in c for c in calls)
    import shutil
    monkeypatch.setattr(vm,'tree_copy',lambda src,dst,**kwargs:shutil.copytree(src,dst))
    calls.clear()
    result=vm.prepare('win10')
    assert result['ready']
    assert ['pagefile','automatic'] not in calls
    manifest=json.loads((vm.storage('win10')/'golden/.mykos-checkpoint/manifest.json').read_text())
    assert manifest['ram_mib']==2048
    assert any(c[1]=='commit' for c in calls)


def test_windows_paging_failure_does_not_restart_or_publish(desktop,monkeypatch):
    import mykos_commands
    root,calls=runtime_builder(desktop,monkeypatch)
    monkeypatch.setattr(mykos_commands,'powershell',lambda *args,**kwargs:{'exit_code':1,'output':'Access denied'})
    with pytest.raises(RuntimeError,match='Could not enable Windows-managed paging'):
        vm.prepare('win10')
    assert vm.read('win10')['ram_mib']==8192
    assert not any(c[1] in ('stop','rename','rm','commit') for c in calls)


def test_windows_runtime_restart_failure_restores_previous_builder(desktop,monkeypatch):
    root,calls=runtime_builder(desktop,monkeypatch)
    original=vm.run
    def fail(args,**kwargs):
        if args[1]=='run': raise RuntimeError('Launch failed')
        return original(args,**kwargs)
    monkeypatch.setattr(vm,'run',fail)
    with pytest.raises(RuntimeError,match='Launch failed'): vm.prepare('win10')
    assert vm.read('win10')['ram_mib']==8192
    assert [vm.PODMAN,'start','win10'] in calls
    assert (vm.storage('win10')/'storage/data.img').read_bytes()==b'installed Windows disk'


def test_windows_desktop_build_ram_cannot_leak_to_runtime(desktop):
    root,calls=desktop
    for name in ('win10','win11'):
        assert vm.image_memory(name)==2048
    for name in ('wns2022','mac15','ubuntu22.04'):
        assert vm.image_memory(name)==2048
    vm.create('win10','operator','private-password')
    command=next(c for c in calls if c[1]=='run')
    assert 'RAM_SIZE=2048M' in command
    value=vm.read('win10');value.update(role='instance',name='12345678.test',ready=True,ram_mib=8192)
    calls.clear()
    with pytest.raises(ValueError,match='runtime containers require 2048'): vm.launch('12345678',value)
    assert calls==[]


def test_ubuntu_live_installer_is_rejected_before_capture(desktop,monkeypatch):
    import mykos_ubuntu_vpn
    root,calls=desktop
    vm.create('ubuntu22.04')
    launch=next(c for c in calls if c[1]=='run')
    assert 'QGA=/dev/shm/mykos-qga.sock' in launch
    def reject(value,path,args=(),**kwargs):
        assert 'findmnt' in args[1] and "('ext4','xfs')" in args[1]
        raise ValueError('Install Ubuntu to disk before capture')
    monkeypatch.setattr(mykos_ubuntu_vpn,'execute',reject)
    calls.clear()
    with pytest.raises(ValueError,match='Install Ubuntu'):vm.prepare('ubuntu22.04')
    assert not any(c[0]=='monitor' or c[1] in ('stop','commit') for c in calls)
    assert not vm.read('ubuntu22.04')['ready']


def test_windows_finish_rejects_setup_before_capture(monkeypatch):
    import mykos_commands
    monkeypatch.setattr(mykos_commands, 'powershell', lambda *a, **kw: {'exit_code':1, 'output':'Setup is in progress'})
    with pytest.raises(ValueError, match='not ready.*Setup is in progress'):
        vm.verify_windows_installation({'os':'wns2022'})


def test_windows_finish_accepts_installed_guest(monkeypatch):
    import mykos_commands
    def check(value, script, timeout, **kwargs):
        assert 'SystemSetupInProgress' in script and 'OOBEInProgress' in script
        assert kwargs == {'allow_image_builder':True}
        return {'exit_code':0, 'output':'Installed Windows verified'}
    monkeypatch.setattr(mykos_commands, 'powershell', check)
    vm.verify_windows_installation({'os':'win10'})


def test_unified_commit_contains_installed_disk_and_leaves_one_running_base(desktop,monkeypatch):
    import mykos_image_build as build
    import shutil
    root,calls=desktop
    vm.create('mac15')
    (vm.storage('mac15')/'storage/data.img').write_bytes(b'installed disk and applications')
    mount=root/'wrapper-root';mount.mkdir()
    original=vm.run
    def run(args,**kwargs):
        if args[0]=='/usr/bin/tar':
            import subprocess
            import sys
            command=[a for a in args if a!='--sparse'] if sys.platform=='darwin' else args
            subprocess.run(command,check=True,capture_output=True);return ''
        if args[1]=='mount':
            calls.append(args);return str(mount)
        return original(args,**kwargs)
    monkeypatch.setattr(vm,'run',run)
    monkeypatch.setattr(vm,'tree_copy',lambda src,dst,**kwargs:shutil.copytree(src,dst))
    result=vm.prepare('mac15',keep_running=True,embed_disk=True)
    assert result['ready'] and vm.inspect('mac15')['State']['Running']
    import tarfile
    with tarfile.open(mount/'opt/mykos-image/golden.tar.gz') as archive:
        assert archive.extractfile('./data.img').read()==b'installed disk and applications'
    assert (vm.storage('mac15')/'golden/.mykos-checkpoint/memory.gz').exists()
    commit=next(i for i,c in enumerate(calls) if c[1]=='commit')
    assert next(i for i,c in enumerate(calls) if c[1]=='mount')<commit
    assert sum(c[1]=='run' for c in calls)==2  # Original builder, then the same named base.
    assert [c for c in calls if c[1]=='run'][-1][4]=='mac15'


def test_unattended_windows_provisions_agent_paging_then_completion(desktop,monkeypatch):
    import mykos_image_build as build
    root,calls=desktop
    monkeypatch.setattr(build,'stage',lambda *args:None)
    vm.create('win11','mykos','PrivateTest9!long',unattended=True)
    script=(vm.storage('win11')/'oem/install.bat').read_text()
    assert script.index('mykos-agent.ps1')<script.index('mykos-pagefile.ps1')<script.index('mykos-finish.ps1')
    assert 'SizeMiB 12288' in script
    assert '\nexit /b' not in script[:script.index('mykos-finish.ps1')]
    assert 'MYKOS_UNATTENDED=windows' in next(c for c in calls if c[1]=='run')
    value=vm.read('win11');value.update(ready=True,installation_complete=True)
    vm.launch('win11',value)
    command=[c for c in calls if c[1]=='run'][-1]
    assert 'MYKOS_UNATTENDED=' in command and 'MYKOS_UNATTENDED=windows' not in command


def test_unattended_build_rows_include_host_side_failure_status(desktop,monkeypatch):
    import mykos_image_build as build
    root,calls=desktop
    vm.create('mac15')
    value=vm.read('mac15');value['unattended']=True;vm.save('mac15',value)
    monkeypatch.setattr(build,'STATE',root/'build-status')
    build.report('mac15','failed','Retained disks and diagnostics')
    row=vm.installing_images()[0]
    assert row['build_state']=='failed' and row['build_message']=='Retained disks and diagnostics'
    assert row['console_url']=='/mykos/console/mac15/'


def test_mac_unattended_install_ram_cannot_leak_into_runtime(desktop,monkeypatch):
    import mykos_image_build
    root,calls=desktop
    monkeypatch.setattr(mykos_image_build,'stage',lambda *a:None)
    monkeypatch.setattr(mykos_image_build,'launch_arguments',lambda *a:[])
    vm.create('mac15','operator','private-password',unattended=True)
    value=vm.read('mac15')
    assert value['ram_mib']==4096
    assert 'RAM_SIZE=4096M' in next(c for c in calls if c[1]=='run')
    for update in ({'ready':True},{'installation_complete':True},{'role':'instance'}):
        with pytest.raises(ValueError,match='2048'):
            vm.launch('mac15',{**value,**update})
    value.update(installation_complete=True,ram_mib=2048)
    vm.launch('mac15',value)
    assert 'RAM_SIZE=2048M' in [c for c in calls if c[1]=='run'][-1]


def test_mac_checkpoint_profile_keeps_vendor_cpu_model():
    assert vm.checkpoint_cpu_profile('mac15')=={'CPU_FLAGS':'-invtsc'}


def test_mac_installed_boot_drops_build_ram_and_installer_devices(desktop,monkeypatch):
    import mykos_image_build as build
    root,calls=desktop
    monkeypatch.setattr(build,'stage',lambda *a:None)
    monkeypatch.setattr(build,'launch_arguments',lambda *a:[])
    monkeypatch.setattr(build,'arm_second_boot',lambda os_name:None)
    monkeypatch.setattr(build,'folder',lambda os_name:root)
    vm.create('mac15','operator','private-password',unattended=True)
    disk=vm.storage('mac15')/'storage'
    (disk/'base.dmg').write_bytes(b'installer')
    (disk/'data.img').write_bytes(b'installed-disk')
    build.boot_installed('mac15')
    value=vm.read('mac15')
    assert value['ram_mib']==2048 and value['installation_complete']
    assert value['cpu_env']=={'CPU_FLAGS':'-invtsc'}
    assert not (disk/'base.dmg').exists()
    assert (disk/'data.img').read_bytes()==b'installed-disk'
    assert 'RAM_SIZE=2048M' in [c for c in calls if c[1]=='run'][-1]


def test_saved_base_retry_waits_for_slow_stopped_builder_cleanup(desktop,monkeypatch):
    import subprocess
    root,calls=desktop
    vm.create('mac15')
    value=vm.read('mac15');value.update(ready=True,committed=True,capture_mode='memory');vm.save('mac15',value)
    manifest=vm.storage('mac15')/'golden/.mykos-checkpoint/manifest.json';manifest.parent.mkdir(parents=True);manifest.write_text('{}')
    vm.stop_container('mac15')
    original=vm.run
    def slow_remove(args,**kwargs):
        if args[1]=='rm' and kwargs.get('timeout',0)<180:
            raise subprocess.TimeoutExpired(args,30)
        return original(args,**kwargs)
    monkeypatch.setattr(vm,'run',slow_remove)
    assert vm.ensure_prepared_base('mac15')['ready']
    assert vm.inspect('mac15')['State']['Running']
    assert not any(c[1]=='commit' for c in calls)


def test_started_row_is_published_before_memory_restore_finishes(desktop,monkeypatch):
    import shutil
    monkeypatch.setattr(vm,'tree_copy',lambda src,dst,**kwargs:shutil.copytree(src,dst))
    vm.create('mac15')
    (vm.storage('mac15')/'storage/data.img').write_bytes(b'installed disk')
    vm.prepare('mac15')
    events=[]
    monkeypatch.setattr(vm,'publish_started',lambda key:events.append(('published',key)))
    monkeypatch.setattr(vm,'wait_for_restore',lambda *args:events.append(('restored',args[0]['name'])))
    result=vm.start('90909090','mac15')
    assert result['ready'] and result['last_start']=='memory-restore'
    assert events[0]==('published','90909090') and events[1][0]=='restored'


def test_commit_retry_reuses_completed_capture_without_guest_calls(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    (vm.storage('mac15')/'storage/data.img').write_bytes(b'installed disk')
    import shutil,mykos_image_build as build
    monkeypatch.setattr(vm,'tree_copy',lambda s,d,**kw:shutil.copytree(s,d))
    embedded=[];monkeypatch.setattr(build,'embed_capture',lambda v,p:embedded.append(p))
    original=vm.run
    def fail(args,**kwargs):
        if args[1]=='commit':raise RuntimeError('no space left on device')
        return original(args,**kwargs)
    monkeypatch.setattr(vm,'run',fail)
    with pytest.raises(RuntimeError,match='no space'):vm.prepare('mac15',keep_running=True,embed_disk=True)
    pending=vm.read('mac15')['pending_commit'];saved=vm.storage('mac15')/pending['directory']/'golden'
    assert (saved/'data.img').read_bytes()==b'installed disk'
    assert (saved/'.mykos-checkpoint/memory.gz').is_file()
    assert pending['embedded'] and len(embedded)==1
    monkeypatch.setattr(vm,'run',original)
    monkeypatch.setattr(vm,'capture_live',lambda *a:pytest.fail('Retry recaptured the VM'))
    monkeypatch.setattr(vm,'verify_windows_installation',lambda *a:pytest.fail('Retry queried the guest'))
    assert vm.prepare('mac15',keep_running=True,embed_disk=True)['ready']
    assert 'pending_commit' not in vm.read('mac15') and not saved.parent.exists()
    assert (vm.storage('mac15')/'golden/data.img').read_bytes()==b'installed disk'
    assert len(embedded)==1


def test_pending_commit_rejects_replaced_builder(desktop,monkeypatch):
    root,calls=desktop;vm.create('mac15')
    (vm.storage('mac15')/'storage/data.img').write_bytes(b'installed disk')
    import shutil
    monkeypatch.setattr(vm,'tree_copy',lambda s,d,**kw:shutil.copytree(s,d))
    original=vm.run
    def fail(args,**kwargs):
        if args[1]=='commit':raise RuntimeError('commit failed')
        return original(args,**kwargs)
    monkeypatch.setattr(vm,'run',fail)
    with pytest.raises(RuntimeError,match='commit failed'):vm.prepare('mac15')
    previous=vm.inspect
    def replaced(name):
        result=previous(name);result['Id']='another-container';return result
    monkeypatch.setattr(vm,'inspect',replaced)
    with pytest.raises(ValueError,match='identity changed'):vm.prepare('mac15')
    assert vm.pending_capture(vm.read('mac15')).is_dir()
