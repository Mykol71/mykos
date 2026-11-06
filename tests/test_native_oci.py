import importlib.machinery
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/helpers'))
import mykos_runtime as runtime
import mykos_host_monitoring as monitor
import mykos_cgroups as cgroups
import mykos_vm as vm

def load(name,path):
    loader=importlib.machinery.SourceFileLoader(name,str(path))
    spec=importlib.util.spec_from_loader(name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module);return module

builder=load('native_oci',ROOT/'pos/build_native_oci.py')
android=load('android_media',ROOT/'services/helpers/mykos-android-media')

@pytest.mark.parametrize('name',builder.BASES)
def test_native_recipe_keeps_vendor_package_verification_and_identity(name):
    reference=builder.BASES[name].rsplit(':',1)[0]+'@sha256:'+'a'*64
    recipe,setup=builder.dockerfile(name,reference)
    assert name in runtime.NATIVE_OS_NAMES and name in runtime.STAGE_OS_NAMES and name not in vm.VERSIONS
    assert 'FROM '+reference in recipe and 'io.mykos.os="'+name+'"' in recipe
    for bypass in ('--nogpgcheck','--no-gpg-checks','--allow-untrusted','--allowerasing','PermitRootLogin yes'):
        assert bypass not in recipe+setup
    assert 'passwd -l root' in setup and 'ssh_host_' in setup
    assert 'systemd' not in builder.dockerfile('alpine',builder.BASES['alpine'].rsplit(':',1)[0]+'@sha256:'+'a'*64)[1]
    subprocess.run(['bash','-n'],input=setup,text=True,check=True)
    data=monitor.image_os({'Image':'abc'},{'Labels':{'io.mykos.os':name,'io.mykos.os.version':builder.VERSIONS[name]}})
    assert data['os_type']==builder.VENDORS[name] and data['os_version']==builder.VERSIONS[name]

def test_recipe_rejects_nonvendor_or_cross_distro_base():
    with pytest.raises(ValueError):builder.dockerfile('debian13','docker.io/library/archlinux@sha256:'+'a'*64)
    with pytest.raises(ValueError):builder.dockerfile('debian13','https://untrusted.example/image')
    with pytest.raises(ValueError):builder.dockerfile('unsupported','docker.io/library/debian@sha256:'+'a'*64)

def test_minimal_base_package_and_checkpoint_regressions():
    recipes={name:builder.dockerfile(name,base.rsplit(':',1)[0]+'@sha256:'+'a'*64) for name,base in builder.BASES.items()}
    assert 'coreutils-single' in recipes['amazon2023'][0] and ' passwd ' in recipes['amazon2023'][0]
    assert 'openrc-init' in recipes['alpine'][0] and 'openssh-server-common-openrc' in recipes['alpine'][0]
    assert '/var/lib/dbus' in recipes['amazon2023'][1]
    for name in ('debian13','archlinux','sles15sp7','amazon2023'):
        assert 'ImportCredential=' in recipes[name][1]
        assert 'mv -fT "$localtime_tmp" /etc/localtime' not in recipes[name][1]

def test_openrc_is_selected_from_new_image_metadata(monkeypatch):
    assert cgroups.init_command('alpine')==['/sbin/openrc-init']
    monkeypatch.setattr(cgroups,'run',lambda *a,**k:json.dumps([{'Config':{'Labels':{'io.mykos.init':'openrc'}}}]))
    assert cgroups.init_command('localhost/owned-alpine:test')==['/sbin/openrc-init']

def test_new_native_builder_is_protected_from_lifecycle_changes():
    assert runtime.native_build_os(['/usr/bin/python3','./build_native_oci.py','debian13'])=='debian13'
    assert runtime.native_build_os(['/usr/bin/python3','/usr/local/share/mykos/pos/build_native_oci.py','alpine'])=='alpine'
    assert runtime.native_build_os(['/usr/bin/python3','unrelated.py','alpine']) is None

class Response(io.BytesIO):
    def geturl(self):return 'https://mirror.example/android.iso'

def test_android_media_is_atomic_checked_and_private(tmp_path,monkeypatch):
    data=b'owned test installation media'
    monkeypatch.setattr(android,'SHA1',hashlib.sha1(data).hexdigest());monkeypatch.setattr(android,'SHA256',hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(android.urllib.request,'urlopen',lambda *a,**k:Response(data))
    assert android.prepare(tmp_path)==hashlib.sha256(data).hexdigest()
    assert (tmp_path/'boot.iso').read_bytes()==data and (tmp_path/'boot.iso').stat().st_mode&0o777==0o600
    assert (tmp_path/'android-media.json').stat().st_mode&0o777==0o600
    assert not (tmp_path/'.android-install.download').exists()

@pytest.mark.parametrize('data',[b'',b'<html>download failed</html>',b'wrong image'])
def test_android_failed_or_html_download_is_never_booted(tmp_path,monkeypatch,data):
    monkeypatch.setattr(android.urllib.request,'urlopen',lambda *a,**k:Response(data))
    with pytest.raises(ValueError,match='checksum'):android.prepare(tmp_path)
    assert not (tmp_path/'boot.iso').exists() and not (tmp_path/'.android-install.download').exists()

def test_android_profile_preserves_native_guest_and_no_adb_boundary():
    assert vm.VERSIONS['android9']==('android','9.0-r2')
    assert 'android9' in runtime.STAGE_OS_NAMES and 'android9' not in runtime.NATIVE_OS_NAMES
    assert vm.checkpoint_cpu_profile('android9')['CPU_FLAGS']=='migratable=on,-invtsc'
    assert vm.image_memory('android9',4096)==4096

@pytest.mark.parametrize('ready',[False,True])
def test_android_launch_never_inherits_install_or_exposes_adb(monkeypatch,tmp_path,ready):
    calls=[]
    monkeypatch.setattr(runtime,'require_container_capacity',lambda *a:None)
    monkeypatch.setattr(vm,'storage',lambda *a:tmp_path)
    monkeypatch.setattr(vm,'launch_image',lambda *a:'owned-image')
    monkeypatch.setattr(vm,'host_config',lambda:{})
    monkeypatch.setattr(vm.network,'arguments',lambda *a:[])
    monkeypatch.setattr(vm,'run',lambda args,**kw:calls.append(args) or 'owned-container')
    monkeypatch.setattr(vm,'inspect',lambda *a:{'State':{'Running':True}})
    value={'os':'android9','name':'android9','role':'template','image':'owned-image','host':'192.168.222.123','bind':'192.168.222.123','console_port':18999,'desktop_port':23999,'ram_mib':2048,'boot':'/storage/boot.iso','ready':ready,'installation_complete':ready}
    vm.launch('android9',value);args=calls[0]
    env=dict(item.split('=',1) for i,item in enumerate(args) if i and args[i-1]=='-e')
    assert env['MYKOS_ANDROID_INSTALL']==('N' if ready else 'Y')
    assert env['BOOT']==('none' if ready else '/storage/boot.iso')
    assert env['BOOT_MODE']=='legacy' and not any('5555' in str(a) for a in args)
    assert not any('QGA=' in str(a) for a in args)


@pytest.mark.parametrize('cached',[False,True])
def test_builder_preserves_preexisting_upstream_tag(tmp_path,monkeypatch,cached):
    calls=[];digest='sha256:'+'a'*64;image_id='sha256:'+'b'*64
    reference=builder.BASES['debian13'];base=reference.rsplit(':',1)[0]+'@'+digest
    monkeypatch.setattr(builder.os,'geteuid',lambda:0)
    class Result:
        def __init__(self,code):self.returncode=code
    monkeypatch.setattr(builder.subprocess,'run',lambda args,**kw:Result(0 if args[-1]==reference and cached else 1))
    def run(args,timeout=1800):
        calls.append(args)
        if args[:3]==['podman','image','inspect']:
            return json.dumps([{'Id':image_id,'Digest':digest,'Labels':{'io.mykos.os':'debian13','io.mykos.base.reference':base}}])
        return ''
    monkeypatch.setattr(builder,'run',run)
    original=builder.tempfile.TemporaryDirectory
    monkeypatch.setattr(builder.tempfile,'TemporaryDirectory',lambda **kw:original(prefix='owned-build-',dir=tmp_path))
    assert builder.build('debian13')['base_reference']==base
    assert any(args[:2]==['podman','pull'] for args in calls) is (not cached)
    assert not any(args[:2]==['podman','untag'] for args in calls)  # no created tag in this stub
    assert list(tmp_path.iterdir())==[]



def test_android_optical_patch_is_scoped_idempotent_and_rejects_unknown_vendor(tmp_path):
    entry=(ROOT/'services/helpers/mykos-checkpoint-entry').read_text()
    code=entry.split("python3 - <<'MYKOS_ANDROID_OPTICAL'",1)[1].split('\nMYKOS_ANDROID_OPTICAL',1)[0]
    path=tmp_path/'disk.sh';code=code.replace("Path('/run/disk.sh')",'Path('+repr(str(path))+')')
    original='if [[ "$hybrid" != "0000" ]]; then'
    path.write_text(original+'\nunchanged vendor body\n')
    exec(compile(code,'owned-optical-fixture','exec'),{})
    result=path.read_text();assert 'MYKOS_ANDROID_INSTALL:-N' in result and result.endswith('unchanged vendor body\n')
    exec(compile(code,'owned-optical-fixture','exec'),{});assert path.read_text()==result
    path.write_text('unexpected vendor format')
    with pytest.raises(SystemExit,match='Unsupported Android'):exec(compile(code,'owned-optical-fixture','exec'),{})


@pytest.mark.parametrize('os_name',list(builder.BASES))
def test_changed_native_timezone_rejected_before_checkpoint_but_preserved(tmp_path,os_name):
    import mykos_snapshots as snapshots
    root=tmp_path/'42/root/etc';root.mkdir(parents=True)
    state={'State':{'Pid':42},'Config':{'Labels':{'io.mykos.os':os_name}}}
    snapshots.check_native_timezone(state,tmp_path)
    (root/'localtime').write_bytes(b'operator timezone configuration')
    with pytest.raises(ValueError,match='No snapshot was created'):snapshots.check_native_timezone(state,tmp_path)
    assert (root/'localtime').read_bytes()==b'operator timezone configuration'
    state['Config']['Labels']['io.mykos.os']='rh9'
    snapshots.check_native_timezone(state,tmp_path)  # Existing family policy unchanged.
