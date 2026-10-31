from pathlib import Path
from types import SimpleNamespace
import signal
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pos'))
import build_rootfs


@pytest.mark.parametrize('name,el8',[('rh7',False),('rh8',True),('rocky8',True),('alma8',True),('oracle8',True),('centos8',True),('rh9',False),('rh10',False),('fedora44',False)])
def test_every_installer_has_private_mounts_pid_run_and_logs(tmp_path,monkeypatch,name,el8):
    release=tmp_path/'os-release';original='NAME=Host\nPLATFORM_ID="platform:el9"\n';release.write_text(original)
    ks=tmp_path/(name+'.ks');ks.write_text('rootpw --lock\n')
    monkeypatch.setattr(build_rootfs.os,'geteuid',lambda:0)
    monkeypatch.setattr(build_rootfs.shutil,'which',lambda name:'/usr/sbin/'+name)
    calls=[]
    def execute(args,**kwargs):
        calls.append(args)
        assert args[:8]==['/usr/sbin/unshare','--mount','--pid','--fork','--kill-child','--mount-proc','--propagation','private']
        directory=Path(args[12]);assert directory.is_dir()
        assert args[14]==('el8' if el8 else 'native')
        assert (directory/'os-release').read_text()==original.replace('platform:el9','platform:el8')
        assert 'mount --bind "$1/mnt" /mnt' in args[10]
        assert 'mount --bind "$1/tmp" /tmp' in args[10]
        assert 'tmpfs /run' in args[10]
        assert Path(args[13]).read_bytes()==(directory/'resolv.conf').read_bytes()
        assert release.read_text()==original
        assert '--ks='+str(ks) in args and kwargs['start_new_session']
        result=directory/'result';result.mkdir();(result/(name+'-docker.tar.xz')).write_bytes(b'rootfs')
        return SimpleNamespace(returncode=0,poll=lambda:0)
    monkeypatch.setattr(build_rootfs.subprocess,'Popen',execute)
    assert build_rootfs.build(name,ks,release_file=release,temporary_root=tmp_path,lock_root=tmp_path)==0
    assert len(calls)==1 and release.read_text()==original
    assert (tmp_path/(name+'-docker.tar.xz')).read_bytes()==b'rootfs'
    work=next(tmp_path.glob('mykos-build-*'));assert not (work/'mnt').exists() and not (work/'tmp').exists()


def test_missing_namespace_tool_never_falls_back_to_host_mutation(tmp_path,monkeypatch):
    release=tmp_path/'os-release';release.write_text('PLATFORM_ID="platform:el9"\n')
    ks=tmp_path/'rh8.ks';ks.write_text('rootpw --lock\n')
    monkeypatch.setattr(build_rootfs.os,'geteuid',lambda:0)
    monkeypatch.setattr(build_rootfs.shutil,'which',lambda name:'/usr/sbin/livemedia-creator' if name=='livemedia-creator' else None)
    with pytest.raises(ValueError,match='require util-linux unshare'):
        build_rootfs.build('rh8',ks,release_file=release,temporary_root=tmp_path)
    assert release.read_text()=='PLATFORM_ID="platform:el9"\n'


def test_fatal_installer_prompt_terminates_only_its_process_group(tmp_path,monkeypatch):
    log=tmp_path/'build.log';log.write_text('There was an error running the kickstart script at line 98.\n')
    calls=[];state={'running':True}
    def wait(timeout):state['running']=False
    process=SimpleNamespace(pid=12345,poll=lambda:None if state['running'] else -15,wait=wait)
    monkeypatch.setattr(build_rootfs.os,'killpg',lambda pid,sig:calls.append((pid,sig)))
    with pytest.raises(RuntimeError,match='kickstart script'):
        build_rootfs.watch(process,log,60)
    assert calls==[(12345,signal.SIGTERM)] and log.exists()


def test_build_timeout_terminates_child_instead_of_hanging(tmp_path,monkeypatch):
    calls=[];state={'running':True}
    process=SimpleNamespace(pid=12345,poll=lambda:None if state['running'] else -15,wait=lambda timeout:state.update(running=False))
    monkeypatch.setattr(build_rootfs.os,'killpg',lambda pid,sig:calls.append((pid,sig)))
    with pytest.raises(RuntimeError,match='timed out'):
        build_rootfs.watch(process,tmp_path/'log',-1)
    assert calls==[(12345,signal.SIGTERM)]


def test_rootfs_builder_rejects_unrecognized_os_before_execution(tmp_path):
    with pytest.raises(ValueError,match='Unsupported'):
        build_rootfs.build('rh8;reboot',tmp_path/'ks')


def test_different_build_roots_share_one_installer_cache_lock(tmp_path,monkeypatch):
    release=tmp_path/'os-release';release.write_text('PLATFORM_ID="platform:el9"\n')
    ks=tmp_path/'rh7.ks';ks.write_text('rootpw --lock\n')
    other=tmp_path/'another-build';other.mkdir()
    monkeypatch.setattr(build_rootfs.os,'geteuid',lambda:0)
    monkeypatch.setattr(build_rootfs.shutil,'which',lambda name:'/usr/sbin/'+name)
    descriptor=build_rootfs.os.open(tmp_path,build_rootfs.os.O_RDONLY|build_rootfs.os.O_DIRECTORY)
    try:
        build_rootfs.fcntl.flock(descriptor,build_rootfs.fcntl.LOCK_EX|build_rootfs.fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='Another MYKOS native image build'):
            build_rootfs.build('rh7',ks,release_file=release,temporary_root=other,lock_root=tmp_path)
        assert list(other.iterdir())==[]
    finally:build_rootfs.os.close(descriptor)
