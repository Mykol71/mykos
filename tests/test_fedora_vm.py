import importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/helpers'))
import mykos_fedora as fedora
import mykos_vm as vm


def test_desktop_choices_preserve_native_recipes():
    from mykos_runtime import NATIVE_OS_NAMES,STAGE_OS_NAMES
    for version in ('43','44'):
        key='fedora'+version+'vm'
        assert vm.VERSIONS[key]==('fedora',version)
        assert key in STAGE_OS_NAMES and key not in NATIVE_OS_NAMES
        assert 'fedora'+version in NATIVE_OS_NAMES
        assert 'gnome' not in (ROOT/'pos/ks'/('fedora'+version+'.ks')).read_text().lower()
        assert vm.template_image(key)=='localhost/'+key+':latest'
        assert vm.checkpoint_cpu_profile(key)['CPU_FLAGS']=='migratable=on,-invtsc'


def test_official_cloud_resolution(monkeypatch):
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return b'<a href="Fedora-Cloud-Base-Generic-43-1.6.x86_64.qcow2">disk</a><a href="https://evil/disk.qcow2">bad</a>'
    monkeypatch.setattr(fedora.urllib.request,'urlopen',lambda url,timeout:Response())
    assert fedora.cloud_image('43').endswith('/43/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-43-1.6.x86_64.qcow2')
    with pytest.raises(ValueError):fedora.cloud_image('42')


def test_seed_keeps_passwords_and_login_boundaries(monkeypatch):
    calls=[]
    def run(args,**kw):
        calls.append((args,kw));return SimpleNamespace(stdout='$6$salt$validhash\n')
    monkeypatch.setattr(fedora.subprocess,'run',run)
    text=fedora.userdata({'username':'mykos','password':'Test-password-123!'})
    data=json.loads(text.split('\n',1)[1])
    assert 'Test-password-123!' not in text
    assert calls[0][0]==['/usr/bin/openssl','passwd','-6','-stdin']
    assert calls[0][1]['input']=='Test-password-123!\n'
    assert data['disable_root'] and data['ssh_pwauth'] is False
    assert data['users'][0]['passwd'].startswith('$6$')
    setup=data['write_files'][1]['content']
    assert '--autologin' not in setup and 'AutomaticLogin' not in setup
    assert 'gnome-shell' in data['packages'] and 'xorg-x11-server-Xwayland' in data['packages']
    assert 'mkswapfile' in setup and '12G' in setup
    with pytest.raises(ValueError):fedora.userdata({'username':'root','password':'foo'})
    with pytest.raises(ValueError):fedora.userdata({'username':'mykos','password':'bad\npassword123'})


def media_module():
    import importlib.machinery
    loader=importlib.machinery.SourceFileLoader('fedora_media_test',str(ROOT/'services/helpers/mykos-fedora-media'))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    module=importlib.util.module_from_spec(spec);loader.exec_module(module);return module


@pytest.mark.parametrize('conversion_fails',[True,False])
def test_cloud_disk_conversion_is_atomic(tmp_path,monkeypatch,conversion_fails):
    import io,hashlib,subprocess
    module=media_module();name='Fedora-Cloud-Base-Generic-43-1.6.x86_64.qcow2';content=b'authenticated cloud disk'
    checksum=('SHA256 ('+name+') = '+hashlib.sha256(content).hexdigest()+'\n').encode()
    monkeypatch.setattr(module.urllib.request,'urlopen',lambda url,timeout:io.BytesIO(checksum if url.endswith('CHECKSUM') else content))
    def run(args,**kwargs):
        if args[0]=='qemu-img':
            Path(args[-1]).write_bytes(b'converted cloud disk')
            if conversion_fails:raise subprocess.CalledProcessError(1,args)
        else:Path(args[args.index('-o')+1]).write_bytes(b'private seed ISO')
    monkeypatch.setattr(module.subprocess,'run',run)
    state=tmp_path/'.mykos-installstate';state.mkdir();(state/'user-data').write_text('seed');(state/'meta-data').write_text('{}')
    url='https://dl.fedoraproject.org/pub/fedora/linux/releases/43/Cloud/x86_64/images/'+name
    if conversion_fails:
        with pytest.raises(subprocess.CalledProcessError):module.prepare(tmp_path,url)
        assert not (tmp_path/'data.qcow2').exists()
    else:
        module.prepare(tmp_path,url)
        assert (tmp_path/'data.qcow2').read_bytes()==b'converted cloud disk'
        assert (tmp_path/'seed.iso').read_bytes()==b'private seed ISO'
        assert (tmp_path/'data.qcow2').stat().st_mode & 0o777==0o600
    assert not (tmp_path/'.data.qcow2.installing').exists()
    assert not (tmp_path/'fedora-cloud.download').exists()


def test_fedora_vm_version_ignores_qemu_wrapper_version():
    import mykos_host_monitoring as host
    info={'Image':'wrapper','Config':{'Labels':{'io.mykos.vm.os':'fedora43vm'}}}
    assert host.image_os(info,{'Labels':{'version':'7.50'}})['os_version']=='43'
