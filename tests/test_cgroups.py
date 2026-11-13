from pathlib import Path
import json,sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_cgroups as cg


def test_cgroup_v1_host_keeps_existing_startup(monkeypatch,tmp_path):
    monkeypatch.setattr(cg,'UNIFIED',tmp_path/'absent')
    monkeypatch.setattr(cg,'run',lambda *a,**k:pytest.fail('Must not change a cgroup v1 host'))
    assert cg.arguments('rh7')==[]
    assert cg.init_command('rh7')==['/sbin/init']


@pytest.mark.parametrize('image',['rh7','localhost/rh7:latest','sha256:saved'])
def test_rh7_enables_persistent_compatibility_before_launch(monkeypatch,tmp_path,image):
    marker=tmp_path/'cgroup.controllers';marker.touch()
    monkeypatch.setattr(cg,'UNIFIED',marker)
    original=Path.is_file
    monkeypatch.setattr(Path,'is_file',lambda p:p==Path('/usr/bin/crun') or original(p))
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        return json.dumps([{'Config':{'Labels':{'io.mykos.os':'rh7'}}}]) if args[1:3]==['image','inspect'] else ''
    monkeypatch.setattr(cg,'run',run)
    args=cg.arguments(image)
    assert '--runtime=/usr/bin/crun' in args and '--systemd=always' in args
    mounts=[args[i+1] for i,arg in enumerate(args[:-1]) if arg=='--mount']
    assert 'type=bind,source=/run/mykos-legacy-cgroups/systemd,destination=/run/mykos-legacy-systemd,ro=true' in mounts
    assert any(m.endswith('destination=/usr/local/libexec/mykos-rh7-init,ro=true') for m in mounts)
    assert not any('force_cgroup_v1' in arg for arg in args)
    assert cg.init_command(image)==['/usr/bin/python','/usr/local/libexec/mykos-rh7-init']
    assert ['/usr/bin/systemctl','enable','--now','mykos-legacy-cgroups.service'] in calls
    assert next(call for call in calls if call[0]=='/usr/bin/systemctl')==['/usr/bin/systemctl','enable','--now','mykos-legacy-cgroups.service']


def test_current_images_do_not_enable_legacy_service(monkeypatch,tmp_path):
    marker=tmp_path/'cgroup.controllers';marker.touch();monkeypatch.setattr(cg,'UNIFIED',marker)
    monkeypatch.setattr(cg,'run',lambda *a,**k:json.dumps([{'Config':{'Labels':{'io.mykos.os':'rocky9'}}}]))
    assert cg.arguments('sha256:modern')==[]


def test_prepare_refuses_symlink_before_mounting(monkeypatch,tmp_path):
    marker=tmp_path/'cgroup.controllers';marker.touch();monkeypatch.setattr(cg,'UNIFIED',marker)
    root=tmp_path/'unsafe';root.symlink_to(tmp_path/'elsewhere');monkeypatch.setattr(cg,'ROOT',root)
    monkeypatch.setattr(cg.os,'geteuid',lambda:0)
    monkeypatch.setattr(cg,'run',lambda *a,**k:pytest.fail('Unsafe target must not mount'))
    with pytest.raises(ValueError,match='Unsafe'):cg.prepare()


def test_legacy_init_identity_parser_never_accepts_shell_text():
    import importlib.machinery,importlib.util
    path=Path(__file__).resolve().parents[1]/'services/helpers/mykos-rh7-init'
    loader=importlib.machinery.SourceFileLoader('legacy_init_test',str(path));spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    assert module.container_id('engine="podman"\nid="'+'a'*64+'"\n')=='a'*64
    old='36 25 0:42 / /sys/fs/cgroup/systemd rw - cgroup cgroup rw,name=systemd'
    new='37 25 0:42 / /run/mykos-legacy-systemd ro - cgroup cgroup rw,name=systemd'
    assert module.hierarchy_mount(iter([old]))=='/sys/fs/cgroup/systemd'
    assert module.hierarchy_mount([old,new])=='/run/mykos-legacy-systemd'
    for lines in ([],[old,old],[new.replace(' - cgroup ',' - cgroup2 '),old]):
        with pytest.raises(ValueError,match='named systemd hierarchy'):module.hierarchy_mount(lines)
    for text in ('id="../../other"','id="'+'a'*64+'"; id','id="'+'a'*64+'"\nid="'+'b'*64+'"'):
        with pytest.raises(ValueError):module.container_id(text)
    with pytest.raises(ValueError,match='PID 1'):module.main()


@pytest.mark.parametrize('os_name',cg.EL7_NAMES)
def test_el7_private_mount_is_never_an_external_host_root(monkeypatch,tmp_path,os_name):
    marker=tmp_path/'cgroup.controllers';marker.touch()
    monkeypatch.setattr(cg,'UNIFIED',marker)
    assert cg.host_mount_arguments(os_name)==[]


def test_modern_and_v1_guests_keep_host_mount(monkeypatch,tmp_path):
    monkeypatch.setattr(cg,'UNIFIED',tmp_path/'absent')
    for os_name in ('rh7','rh9','centos6'):
        assert cg.host_mount_arguments(os_name)==['--volume','/sys/fs/cgroup:/sys/fs/cgroup:ro']
