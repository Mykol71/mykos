import copy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_vm as vm
from contextlib import nullcontext


def setup(monkeypatch,tmp_path,fail=False):
    value={'os':'win10','name':'12345678.host','image':'abc','role':'instance','host':'192.168.222.5','bind':'192.168.222.5','console_port':18006,'desktop_port':23000,'ready':True,'capture_mode':'memory'}
    calls=[];saved=[]
    monkeypatch.setattr(vm,'locked',nullcontext)
    monkeypatch.setattr(vm,'read',lambda key:copy.deepcopy(value))
    monkeypatch.setattr(vm,'storage',lambda key:tmp_path)
    monkeypatch.setattr(vm,'inspect',lambda name:{'State':{'Running':False},'Config':{'Labels':{'io.mykos.vm.os':'win10','io.mykos.vm.role':'instance'}}})
    monkeypatch.setattr(vm,'stop_container',lambda name:calls.append(['stop',name]))
    monkeypatch.setattr(vm,'run',lambda args,**kw:calls.append(args) or '')
    monkeypatch.setattr(vm,'save',lambda key,data:saved.append(copy.deepcopy(data)))
    def launch(key,data):
        calls.append(['launch',data['ram_mib'],data['capture_mode']])
        if fail: raise RuntimeError('launch failed')
    monkeypatch.setattr(vm,'launch',launch)
    return calls,saved


def test_cold_resize_preserves_capture_and_disks(monkeypatch,tmp_path):
    calls,saved=setup(monkeypatch,tmp_path)
    captures=tmp_path/'storage/.mykos-checkpoint';captures.mkdir(parents=True)
    (captures/'memory.gz').write_bytes(b'capture')
    result=vm.configure_memory('12345678',2048)
    assert saved[-1]['ram_mib']==2048 and saved[-1]['capture_mode']=='disk'
    assert (captures/'memory.gz').read_bytes()==b'capture'
    assert ['launch',2048,'disk'] in calls
    assert result['console_port']==18006


def test_resize_failure_restores_original_metadata(monkeypatch,tmp_path):
    calls,saved=setup(monkeypatch,tmp_path,fail=True)
    with pytest.raises(RuntimeError,match='launch failed'):vm.configure_memory('12345678',2048)
    assert saved[-1]['capture_mode']=='memory' and 'ram_mib' not in saved[-1]
    assert calls[-1][1:2]==['rename'] and calls[-1][-1]=='12345678.host'
    assert not any(command[1:2]==['update'] for command in calls)


def test_unconsumed_restore_is_not_discarded(monkeypatch,tmp_path):
    calls,saved=setup(monkeypatch,tmp_path)
    folder=tmp_path/'storage/.mykos-checkpoint';folder.mkdir(parents=True)
    (folder/'restore.request').write_text('restore')
    with pytest.raises(ValueError,match='restore request'):vm.configure_memory('12345678',2048)
    assert not saved and not any(command[0]=='launch' for command in calls)


@pytest.mark.parametrize('os_name',['win10','wns2025','win11','mac15','ubuntu24.04'])
def test_image_memory_defaults_and_minimums(os_name):
    assert vm.image_memory(os_name)==2048
    assert vm.image_memory(os_name,2048)==2048
    assert vm.image_memory(os_name,4096)==4096
    assert vm.image_memory(os_name,8192)==8192
    for bad in (True,'4096',0,3072,16384):
        with pytest.raises(ValueError):vm.image_memory(os_name,bad)


def test_live_resize_rejects_wrong_storage_before_stop(monkeypatch,tmp_path):
    calls,saved=setup(monkeypatch,tmp_path)
    (tmp_path/'storage').mkdir()
    monkeypatch.setattr(vm,'inspect',lambda name:{'State':{'Running':True,'Pid':99999999}})
    class Monitor:
        def __init__(self,*args):pass
        def command(self,command):return 'Migration status: none'
        def close(self):pass
    monkeypatch.setattr(vm,'QemuMonitor',Monitor)
    with pytest.raises(FileNotFoundError):vm.configure_memory('12345678',2048)
    assert not calls and not saved


@pytest.mark.parametrize('os_name',['win98','winme','win2k','winxp','win7u'])
def test_legacy_windows_retains_two_gib_compatibility(os_name):
    with pytest.raises(ValueError):vm.image_memory(os_name,4096)


def test_explicit_macos_resize_tracks_runtime_size(monkeypatch,tmp_path):
    calls,saved=setup(monkeypatch,tmp_path)
    old_read=vm.read
    monkeypatch.setattr(vm,'read',lambda key:{**old_read(key),'os':'mac12','ram_mib':2048})
    monkeypatch.setattr(vm,'available_memory_mib',lambda:12288)
    result=vm.configure_memory('12345678',4096)
    assert result['ram_mib']==4096 and saved[-1]['runtime_ram_mib']==4096
    assert saved[-1]['capture_mode']=='disk' and ['launch',4096,'disk'] in calls


def test_increase_rejects_low_headroom_before_stop(monkeypatch,tmp_path):
    calls,saved=setup(monkeypatch,tmp_path);old_read=vm.read
    monkeypatch.setattr(vm,'read',lambda key:{**old_read(key),'os':'mac12','ram_mib':2048})
    monkeypatch.setattr(vm,'available_memory_mib',lambda:4096)
    with pytest.raises(ValueError,match='available host memory'):vm.configure_memory('12345678',4096)
    assert not calls and not saved


def test_four_gib_cold_resize_does_not_apply_to_other_running_os(monkeypatch,tmp_path):
    calls,saved=setup(monkeypatch,tmp_path)
    with pytest.raises(ValueError,match='macOS'):vm.configure_memory('12345678',4096)
    assert not calls and not saved


def test_text_menu_ram_choice_reaches_unattended_build(monkeypatch,tmp_path):
    import runpy,sys,builtins,mykos_runtime,mykos_image_build
    calls=[]
    monkeypatch.setattr(sys,'argv',['vm','create','mac12'])
    monkeypatch.setattr(vm,'ROOT',tmp_path/'vm')
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    monkeypatch.setattr(builtins,'input',lambda prompt:'4096')
    monkeypatch.setattr(mykos_runtime,'build_priority',lambda:None)
    monkeypatch.setattr(mykos_image_build,'build',lambda selection,**kwargs:calls.append((selection,kwargs)) or {})
    monkeypatch.setattr(mykos_image_build,'STATE',tmp_path)
    runpy.run_path(str(Path(__file__).resolve().parents[1]/'pos/vm'),run_name='__main__')
    assert calls==[('mac12',{'replace':False,'ram_mib':4096})]


def test_menu_rebuild_requires_explicit_confirmation(monkeypatch,tmp_path):
    import runpy,sys,builtins,mykos_runtime,mykos_image_build
    root=tmp_path/'vm';root.mkdir();(root/'mac12.json').write_text('{}')
    monkeypatch.setattr(vm,'ROOT',root)
    monkeypatch.setattr(vm,'read',lambda key:{'os':'mac12','ram_mib':2048})
    monkeypatch.setattr(sys,'argv',['vm','create','mac12'])
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    replies=iter(['4096','n']);monkeypatch.setattr(builtins,'input',lambda prompt:next(replies))
    monkeypatch.setattr(mykos_runtime,'build_priority',lambda:None)
    calls=[];monkeypatch.setattr(mykos_image_build,'build',lambda *a,**kw:calls.append(kw))
    with pytest.raises(SystemExit):runpy.run_path(str(Path(__file__).resolve().parents[1]/'pos/vm'),run_name='__main__')
    assert not calls
