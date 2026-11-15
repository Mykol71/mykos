"""Public EL VM catalog and lifecycle invariants, independent of the RTI add-on."""
import pytest
import mykos_vm as vm
import mykos_runtime as runtime

NATIVE=[f+m for f in ('rh','rocky','alma','oracle','centos') for m in ('8','9','10')]

@pytest.mark.parametrize('native',NATIVE)
@pytest.mark.parametrize('desktop',[False,True])
def test_public_server_and_desktop_variants(native,desktop):
    name=native+('desktopvm' if desktop else 'vm')
    p=runtime.EL_VM_PROFILES[name]
    assert p['native']==native and p['desktop'] is desktop
    assert vm.VERSIONS[name]==('el-linux',p['major'])
    assert name in runtime.STAGE_OS_NAMES and name not in runtime.NATIVE_OS_NAMES
    assert vm.image_memory(name,None)==(4096 if desktop else 2048)
    if desktop:
        with pytest.raises(ValueError,match='4096|4 GiB'):vm.image_memory(name,2048)
    assert vm.image_memory(name,8192)==8192


def test_public_catalog_has_no_business_or_legacy_profiles():
    assert len(runtime.EL_VM_PROFILES)==30
    assert not any('rti' in name or p['major'] in ('6','7') for name,p in runtime.EL_VM_PROFILES.items())

@pytest.mark.parametrize('os_name,size',[('rocky9vm',6144),('alma10desktopvm',12288),('rh8vm',2304)])
def test_custom_ram_is_validated_not_limited_to_presets(os_name,size):
    assert vm.image_memory(os_name,size)==size

@pytest.mark.parametrize('size',[True,'6144',6144.0,4097,0,-2048,131072])
def test_invalid_ram_is_rejected(size):
    with pytest.raises(ValueError,match='RAM'):vm.image_memory('rocky9desktopvm',size)


def test_ram_override_never_changes_existing_saved_state(tmp_path,monkeypatch):
    import contextlib
    path=tmp_path/'record.json';path.write_text('{}')
    monkeypatch.setattr(vm,'locked',contextlib.nullcontext)
    monkeypatch.setattr(vm,'record_path',lambda key:path)
    monkeypatch.setattr(vm,'read',lambda key:{'os':'rocky9vm','runtime_ram_mib':4096,'ram_mib':4096})
    monkeypatch.setattr(vm,'run',lambda *a,**k:pytest.fail('Existing guest was touched'))
    with pytest.raises(ValueError,match='recorded size'):vm.start('97829101','rocky9vm',ram_mib=6144)


def test_old_vm_types_do_not_accept_new_instance_ram_override(monkeypatch):
    monkeypatch.setattr(vm,'locked',lambda:pytest.fail('No lock or guest action expected'))
    with pytest.raises(ValueError,match='EL VM'):vm.start('97829101','win11',ram_mib=6144)


@pytest.mark.parametrize('native',NATIVE)
def test_repository_configs_keep_vendor_signatures_and_real_ini_newlines(native,monkeypatch):
    import configparser
    from mykos_profiles import el_vm
    module=el_vm()
    if native.startswith('rh'):monkeypatch.setattr(module,'origin',lambda:'https://licensed-mirror.example')
    else:monkeypatch.setattr(module,'origin',lambda:pytest.fail('Community image must not need the RHEL mirror'))
    text=module.repository_config(native+'vm');config=configparser.ConfigParser();config.read_string(text)
    assert set(config.sections())=={'mykos-vm-baseos','mykos-vm-appstream'}
    assert '\\n' not in text
    for section in config.values():
        if not section.name or section.name=='DEFAULT':continue
        assert section['gpgcheck']=='1' and section['baseurl'].startswith('https://')
        assert section['gpgkey']=='file:///etc/pki/rpm-gpg/MYKOS-EL-VM-KEYS'
    if native=='centos8':assert 'vault.centos.org/8-stream/' in text


def test_public_el_builder_contains_no_business_adapter():
    from mykos_profiles import el_vm
    source=__import__('pathlib').Path(el_vm().__file__).read_text()
    import re
    assert not re.search(r'(?i)\brti\b|tfsupport|/usr2|rti_restore|restore_backup',source)

@pytest.mark.parametrize('native',NATIVE)
@pytest.mark.parametrize('desktop',[False,True])
def test_public_build_api_accepts_all_variants_and_forwards_custom_ram(native,desktop,monkeypatch):
    import json,image_build_api,api_v2
    monkeypatch.setattr(api_v2,'administrator',lambda request:None)
    seen=[]
    monkeypatch.setattr(image_build_api,'invoke',lambda *args:seen.append(args) or {'queued':True})
    os_name=native+('desktopvm' if desktop else 'vm')
    assert image_build_api.image_build(os_name,image_build_api.BuildRequest(ram_mib=6144),object())=={'queued':True}
    assert seen[0][:2]==('image-build-queue',os_name)
    assert json.loads(seen[0][2])['ram_mib']==6144


@pytest.mark.parametrize('size',[True,4097,1024,131072,'6144'])
def test_ram_api_rejects_invalid_values_before_queueing(size):
    from pydantic import ValidationError
    from image_build_api import BuildRequest
    with pytest.raises(ValidationError):BuildRequest(ram_mib=size)


def test_new_instance_uses_requested_ram_only_after_removing_base_memory(tmp_path,monkeypatch):
    import contextlib,json
    from types import SimpleNamespace
    import mykos_profiles,mykos_runtime
    os_name='rocky9desktopvm';code='97829101';events=[]
    monkeypatch.setattr(vm,'ROOT',tmp_path/'registry')
    monkeypatch.setattr(vm,'storage_root',lambda:tmp_path/'vms')
    vm.safe_dir(vm.ROOT);vm.safe_dir(vm.storage_root())
    vm.save(os_name,{'os':os_name,'name':os_name,'role':'template','ready':True,'ram_mib':4096,'runtime_ram_mib':4096,'capture_mode':'memory','image':'worker','image_name':'localhost/rocky9desktopvm:latest'})
    monkeypatch.setattr(vm,'locked',contextlib.nullcontext)
    monkeypatch.setattr(vm,'preflight',lambda selected,ram:events.append(('preflight',ram)))
    monkeypatch.setattr(vm,'settings',lambda:('192.0.2.1','127.0.0.1'))
    monkeypatch.setattr(vm,'allocate',lambda bind:(18099,59099))
    monkeypatch.setattr(vm,'run',lambda *a,**k:'[]')
    monkeypatch.setattr(vm,'publish_started',lambda *a:None)
    def copy(source,target):
        vm.safe_dir(target);(target/'data.qcow2').write_bytes(b'owned-test-fixture')
        (target/'.mykos-checkpoint').mkdir();(target/'.mykos-checkpoint/memory.gz').write_bytes(b'old 4096 MiB state')
    monkeypatch.setattr(vm,'tree_copy',copy)
    def provision(shop,value,disk):
        assert shop==code and value['ram_mib']==6144 and value['capture_mode']=='disk'
        assert not (disk.parent/'.mykos-checkpoint').exists()
        events.append(('provision',6144))
    monkeypatch.setattr(mykos_profiles,'el_vm',lambda:SimpleNamespace(provision_disk=provision,wait_ready=lambda value:events.append(('ready',value['ram_mib']))))
    monkeypatch.setattr(vm,'launch',lambda key,value:events.append(('launch',value['ram_mib'])))
    result=vm.start(code,os_name,ram_mib=6144)
    assert result['ram_mib']==6144 and result['last_start']=='boot'
    assert events==[('preflight',6144),('provision',6144),('launch',6144),('ready',6144)]
    assert vm.read(os_name)['ram_mib']==4096


def test_guestfish_encodes_multiline_content_as_one_command(tmp_path,monkeypatch):
    from mykos_profiles import el_vm
    import json
    module=el_vm();seen=[]
    monkeypatch.setattr(module,'run',lambda args,**kw:seen.append((args,kw)) or '')
    content='127.0.0.1 localhost\n::1 localhost\n'
    module.guestfish(tmp_path/'new disk.qcow2',[['write','/etc/hosts',content],['shutdown']],tmp_path)
    assert seen[0][0][:8]==['nice','-n','15','ionice','-c','2','-n','7']
    assert seen[0][1]['timeout']==1800
    lines=seen[0][1]['input'].splitlines()
    assert len(lines)==2 and lines[1]=='shutdown'
    decoder=json.JSONDecoder();path,end=decoder.raw_decode(lines[0][6:]);value,end2=decoder.raw_decode(lines[0][6+end:].lstrip())
    assert path=='/etc/hosts' and value==content
    with pytest.raises(ValueError):module.guestfish(tmp_path/'disk',[['write','/etc/hosts','bad\0value']],tmp_path)


def test_installed_image_build_cli_forwards_custom_ram(monkeypatch,capsys):
    import sys,runpy,types
    from pathlib import Path
    seen=[]
    fake=types.ModuleType('mykos_image_build')
    fake.build=lambda *a,**kw:seen.append((a,kw)) or {'status':'queued'}
    fake.worker=lambda *a:pytest.fail('Unexpected worker')
    fake.status=lambda *a:pytest.fail('Unexpected status')
    monkeypatch.setitem(sys.modules,'mykos_image_build',fake)
    monkeypatch.setattr(sys,'argv',['mykos-image-build','rh9vm','--ram-mib','3072'])
    runpy.run_path(str(Path(__file__).resolve().parents[1]/'services/helpers/mykos-image-build'),run_name='__main__')
    assert seen==[(('rh9vm',),{'replace':False,'ram_mib':3072})]


@pytest.mark.parametrize('profile',['rocky8vm','rocky8desktopvm','rocky9vm','rocky9desktopvm','alma10vm','alma10desktopvm'])
def test_minimal_native_worker_installs_archive_tools_before_using_them(tmp_path,monkeypatch,profile):
    from mykos_profiles import el_vm
    import mykos_network,json,types,shlex
    module=el_vm();seen=[]
    monkeypatch.setattr(module.os,'geteuid',lambda:0)
    monkeypatch.setattr(vm,'safe_dir',lambda path:path.mkdir(parents=True,mode=0o700,exist_ok=True))
    monkeypatch.setattr(module.shutil,'which',lambda name:'/usr/bin/'+name)
    monkeypatch.setattr(module.shutil,'disk_usage',lambda path:types.SimpleNamespace(free=100*1024**3))
    monkeypatch.setattr(runtime,'build_priority',lambda:None)
    monkeypatch.setattr(runtime,'config',lambda:{})
    monkeypatch.setattr(mykos_network,'arguments',lambda *a:[])
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:types.SimpleNamespace(returncode=1))
    class Captured(Exception):pass
    def run(args,**kw):
        if args[:3]==['podman','image','inspect']:return json.dumps([{'Id':'a'*64,'Labels':{'io.mykos.os':runtime.EL_VM_PROFILES[profile]['native']}}])
        if args[:2]==['openssl','passwd']:return '$6$salt$hash'
        if args[:2]==['podman','create']:seen.append(args[-1]);raise Captured()
        pytest.fail(str(args[:2]))
    monkeypatch.setattr(module,'run',run)
    with pytest.raises(Captured):module.build_disk(profile,tmp_path/'vm/data.qcow2',{'username':'mykos','password':'owned-test-password'})
    script=seen[0];dnf=next(line for line in script.splitlines() if line.startswith('dnf '));packages=shlex.split(dnf)
    assert {'tar','gzip','iproute','util-linux','policycoreutils','selinux-policy-targeted','/usr/bin/passwd'} <= set(packages[packages.index('install')+1:])
    assert script.index(dnf)<script.index('tar -C /boot/efi')
    import configparser,uuid
    connection=configparser.ConfigParser()
    connection.read_string(script.split("<<'MYKOS_DHCP'\n",1)[1].split('\nMYKOS_DHCP',1)[0])
    assert connection['connection']['type']=='ethernet'
    assert connection['connection'].getboolean('autoconnect')
    assert str(uuid.UUID(connection['connection']['uuid']))==connection['connection']['uuid']
    assert connection['ipv4']['method']==connection['ipv6']['method']=='auto'
    assert 'interface-name' not in connection['connection']
    assert 'chmod 0600 /etc/NetworkManager/system-connections/mykos-vm.nmconnection' in script
    if 'desktop' in profile:
        assert "if rpm -q coreutils-single >/dev/null 2>&1; then" in script
        assert script.index('swap coreutils-single coreutils')<script.index('group install graphical-server-environment')<script.index("<<'MYKOS_DHCP'")
        assert '--allowerasing' not in script
    else:assert 'swap coreutils-single coreutils' not in script
    import re
    seeded=re.findall(r"printf '%s\\n' ([0-9a-f]{32}) > /etc/machine-id",script)
    if runtime.EL_VM_PROFILES[profile]['major']=='8':assert len(seeded)==1 and ': > /etc/machine-id' not in script
    else:assert seeded==[] and ': > /etc/machine-id' in script
    assert not list((tmp_path/'vm').glob('.el-vm-build-*'))


@pytest.mark.parametrize('name',list(runtime.EL_VM_PROFILES))
def test_host_inventory_uses_guest_major_not_qemu_worker_version(name):
    import mykos_host_monitoring as monitor
    info={'Image':'a'*64,'Config':{'Labels':{'io.mykos.vm.os':name}}}
    found=monitor.image_os(info,{'Labels':{'version':'7.50','name':'QEMU'}})
    assert found['os_version']==runtime.EL_VM_PROFILES[name]['major']
    assert found['os_type']!='QEMU'


def test_fractional_gib_ram_reserves_full_host_headroom(tmp_path,monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    original_exists=Path.exists;original_read=Path.read_text
    monkeypatch.setattr(Path,'exists',lambda self:True if str(self) in ('/dev/kvm','/dev/net/tun') else original_exists(self))
    available=[6200]
    monkeypatch.setattr(Path,'read_text',lambda self,*a,**kw:'MemAvailable: %d kB\n'%(available[0]*1024) if str(self)=='/proc/meminfo' else original_read(self,*a,**kw))
    monkeypatch.setattr(vm,'safe_dir',lambda *a:None)
    monkeypatch.setattr(vm,'storage_root',lambda:tmp_path)
    monkeypatch.setattr(vm.shutil,'disk_usage',lambda path:SimpleNamespace(free=100*1024**3))
    with pytest.raises(ValueError,match='6.25 GiB'):vm.preflight('rh9vm',2304)
    available[0]=6400;vm.preflight('rh9vm',2304)


def test_el_build_translates_termination_and_restores_signal_handlers(monkeypatch):
    from mykos_profiles import el_vm
    import mykos_image_build as build,signal,threading
    module=el_vm();handlers={signal.SIGTERM:'original-term',signal.SIGHUP:'original-hup'};reports=[]
    def install(number,handler):
        before=handlers[number];handlers[number]=handler;return before
    monkeypatch.setattr(module.signal,'signal',install)
    monkeypatch.setattr(build,'report',lambda *args:reports.append(args))
    def preflight(*args):handlers[signal.SIGTERM](signal.SIGTERM,None)
    monkeypatch.setattr(vm,'preflight',preflight)
    with pytest.raises(KeyboardInterrupt,match='interrupted'):module.build_template('rh9vm',3072,{'username':'mykos','password':'owned-test-password'})
    assert reports[-1][1]=='failed'
    assert handlers=={signal.SIGTERM:'original-term',signal.SIGHUP:'original-hup'}


def test_larger_custom_ram_checks_staging_space_before_guest_allocation(tmp_path,monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    original_exists=Path.exists;original_read=Path.read_text
    monkeypatch.setattr(Path,'exists',lambda self:True if str(self) in ('/dev/kvm','/dev/net/tun') else original_exists(self))
    monkeypatch.setattr(Path,'read_text',lambda self,*a,**kw:'MemAvailable: 134217728 kB\n' if str(self)=='/proc/meminfo' else original_read(self,*a,**kw))
    monkeypatch.setattr(vm,'safe_dir',lambda *a:None);monkeypatch.setattr(vm,'storage_root',lambda:tmp_path)
    free=[39*1024**3]
    monkeypatch.setattr(vm.shutil,'disk_usage',lambda path:SimpleNamespace(free=free[0]))
    with pytest.raises(ValueError,match='40 GiB'):vm.preflight('rh9vm',6144)
    free[0]=40*1024**3;vm.preflight('rh9vm',6144)


@pytest.mark.parametrize('native',NATIVE)
def test_vendor_initramfs_uses_labeled_appliance_ext4(native):
    from mykos_profiles import el_vm
    import subprocess
    module=el_vm();kernel='4.18.0-553.el8.x86_64'
    commands=module.appliance_finalization_commands(native+'vm',kernel,{'type':'targeted','mode':'enforcing'},'12345678-1234-1234-1234-123456789abc')
    shell=[(i,c[1]) for i,c in enumerate(commands) if c[0]=='sh'];assert len(shell)==2
    index,script=shell[0]
    assert commands[index-1]==commands[index+2]==['selinux-relabel','/etc/selinux/targeted/contexts/files/file_contexts','/','force:true']
    assert 'dracut --force --no-hostonly --kver '+kernel in script
    assert 'test -s /boot/initramfs-'+kernel+'.img' in script
    assert not any(x in script for x in ('DRACUT_NO_XATTR','setenforce','--no-xattr','--no-preserve','label=disable'))
    assert subprocess.run(['/bin/sh','-n'],input=script,text=True,capture_output=True).returncode==0
    assert commands[-4]==['checksum','sha256','/etc/selinux/config']

@pytest.mark.parametrize('kernel',['','../vmlinuz','4.18;id','4.18\nwhoami'])
def test_appliance_kernel_identity_rejects_path_or_shell_injection(kernel):
    from mykos_profiles import el_vm
    with pytest.raises(ValueError,match='kernel'):el_vm().appliance_initramfs_commands('rocky8vm',kernel)


@pytest.mark.parametrize('os_name',['rh9vm','rocky8desktopvm','alma10desktopvm'])
@pytest.mark.parametrize('operation',['balloon','cold-allocation'])
def test_existing_el_ram_cannot_change_through_legacy_memory_routes(os_name,operation,monkeypatch):
    import contextlib
    monkeypatch.setattr(vm,'locked',contextlib.nullcontext)
    monkeypatch.setattr(vm,'read',lambda key:{'os':os_name,'ram_mib':6144,'runtime_ram_mib':6144})
    monkeypatch.setattr(vm,'QemuMonitor',lambda *a:pytest.fail('Existing guest monitor was touched'))
    monkeypatch.setattr(vm,'inspect',lambda *a:pytest.fail('Existing guest was inspected for mutation'))
    monkeypatch.setattr(vm,'stop_container',lambda *a:pytest.fail('Existing guest was stopped'))
    with pytest.raises(ValueError,match='new-instance'):
        if operation=='balloon':vm.memory('97829101',2048)
        else:vm.configure_memory('97829101',2048)


@pytest.mark.parametrize('mode',['enforcing','permissive','disabled'])
def test_guest_policy_config_is_parsed_without_changing_mode(mode):
    from mykos_profiles import el_vm
    assert el_vm().parse_selinux_config('# vendor config\nSELINUX='+mode+'\nSELINUXTYPE="targeted" # policy\n')=={'mode':mode,'type':'targeted'}

@pytest.mark.parametrize('text',['SELINUX=enforcing\nSELINUXTYPE=../../bad','SELINUX=enforcing\nSELINUXTYPE=$(id)','SELINUX=unknown\nSELINUXTYPE=targeted','SELINUX=enforcing\nSELINUX=enforcing\nSELINUXTYPE=targeted','SELINUX=enforcing'])
def test_invalid_guest_policy_does_not_fall_back_to_weaker_settings(text):
    from mykos_profiles import el_vm
    with pytest.raises(ValueError):el_vm().parse_selinux_config(text)


def test_new_instance_relabels_actual_copied_policy_not_stale_image_metadata(tmp_path,monkeypatch):
    from mykos_profiles import el_vm
    module=el_vm();folder=tmp_path/'storage';folder.mkdir();disk=folder/'data.qcow2';disk.write_bytes(b'owned fixture');disk.chmod(0o600);calls=[];digest='a'*64
    def guestfish(disk,commands,work,**kwargs):
        calls.append(commands)
        return digest+'\nSELINUX=enforcing\nSELINUXTYPE=mls\n' if len(calls)==1 else digest
    monkeypatch.setattr(module,'guestfish',guestfish)
    value={'os':'rh9vm','role':'instance','ready':False,'name':'97829101.test','el_vm_build':{'selinux_policy':{'type':'targeted','mode':'permissive'}}}
    module.provision_disk('97829101',value,disk)
    assert len(calls)==2
    assert ['selinux-relabel','/etc/selinux/mls/contexts/files/file_contexts','/','force:true'] in calls[1]
    assert not list(tmp_path.glob('.el-vm-provision-*'))
