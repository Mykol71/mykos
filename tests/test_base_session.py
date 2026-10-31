from pathlib import Path
import contextlib,json,sys,subprocess,os
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_base_session as session
import mykos_image_admin as images
import mykos_vm as vm

@pytest.mark.parametrize('command',['reboot','shutdown -h now','poweroff','halt','init 6','systemctl --system reboot','systemctl start poweroff.target','sudo /sbin/reboot'])
def test_base_shell_blocks_common_power_commands(tmp_path,command):
    script=tmp_path/'guard';script.write_text(session.SHELL_GUARD+'\n'+command+'\n')
    result=subprocess.run(['bash',str(script)],capture_output=True,text=True)
    assert result.returncode==1 and 'Shutdown/reboot is disabled' in result.stderr


def test_base_shell_logout_exits_and_does_not_run_following_commands(tmp_path):
    script=tmp_path/'guard';script.write_text(session.SHELL_GUARD+'\nlogout\necho should-not-run\n')
    p=subprocess.run(['bash',str(script)],capture_output=True,text=True)
    assert p.returncode==0 and 'should-not-run' not in p.stdout

@pytest.mark.parametrize('command',['Restart-Computer -Force','Stop-Computer','& C:\\Windows\\System32\\shutdown.exe /r /t 0','Start-Process shutdown.exe'])
def test_windows_base_blocks_power_commands(command):
    with pytest.raises(ValueError):session.windows_script(command)
    assert session.windows_script('Get-Date')=='Get-Date'


def test_single_base_editor_and_reset_blocks_new_sessions(tmp_path,monkeypatch):
    monkeypatch.setattr(images,'STATE',tmp_path);monkeypatch.setattr(images,'job',lambda name:{'state':'idle'})
    first=session.acquire('rh9')
    try:
        with pytest.raises(ValueError,match='already has'):session.acquire('rh9')
    finally:os.close(first)
    monkeypatch.setattr(images,'job',lambda name:{'state':'queued'})
    with pytest.raises(ValueError,match='in progress'):session.acquire('rh9')


def test_reset_queue_requires_confirmation_and_does_not_save_on_bad_selection(tmp_path,monkeypatch):
    monkeypatch.setattr(images,'STATE',tmp_path)
    with pytest.raises(ValueError):images.queue_reset('rh9','wrong')
    assert not list(tmp_path.iterdir())


def test_automatic_reset_follows_capture_and_runs_outside_terminal_service(tmp_path,monkeypatch):
    monkeypatch.setattr(images,'STATE',tmp_path)
    monkeypatch.setattr(images,'catalog',lambda:[{'os':'rh9','image_id':'original'}])
    calls=[];monkeypatch.setattr(images,'run',lambda args,**kw:calls.append(args) or '')
    queued=images.queue_reset('rh9','rh9',closing_session=True)
    assert queued['operation']=='reset' and queued['follow_capture']
    assert calls[0][0]=='/usr/bin/systemd-run' and '--worker' in calls[0]
    assert not any('stop' in call for call in calls)


def test_reset_worker_reads_latest_capture_after_console_close(tmp_path,monkeypatch):
    monkeypatch.setattr(images,'STATE',tmp_path)
    images.save('rh9',{'os':'rh9','operation':'reset','state':'queued','follow_capture':True,'image_id':'old','updated_at':__import__('time').time()})
    calls=[];monkeypatch.setattr(images,'reset_base',lambda os_name,expected:calls.append((os_name,expected)) or {'state':'reset'})
    assert images.worker('rh9')['state']=='reset'
    assert calls==[('rh9',None)]


def test_native_reset_preserves_other_containers_and_never_commits(monkeypatch,tmp_path):
    import mykos_containers as containers,mykos_prune as prune
    monkeypatch.setattr(vm,'locked',contextlib.nullcontext)
    monkeypatch.setattr(prune,'builders',lambda:{})
    monkeypatch.setattr(prune,'process_table',lambda:{})
    monkeypatch.setattr(images.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,3,'inactive\n',''))
    base={'Id':'base','Name':'/rh9','ImageName':'localhost/rh9:latest','Config':{'Labels':{'io.mykos.os':'rh9'}},'State':{'Running':True}}
    instance={'Id':'keep','Name':'/12345678.host','State':{'Running':True}}
    current=[base,instance];calls=[]
    monkeypatch.setattr(containers,'all_containers',lambda:current[:])
    def run(args,**kw):
        calls.append(args)
        if args[1:3]==['image','inspect']:return json.dumps([{'Id':'captured'}])
        if args[1]=='rm':current.remove(base)
        return ''
    monkeypatch.setattr(images,'run',run)
    monkeypatch.setattr(vm,'stop_container',lambda cid,**kw:calls.append(['stop',cid]))
    monkeypatch.setattr(images,'launch_native',lambda name:calls.append(['launch',name]))
    assert images.reset_base('rh9','captured')['state']=='reset'
    assert current==[instance]
    assert [images.PODMAN,'rm','base'] in calls
    assert not any('commit' in call or 'rmi' in call or '--force' in call for call in calls)


def test_vm_reset_rejects_missing_or_mismatched_capture_before_stopping(monkeypatch,tmp_path):
    import mykos_containers as containers,mykos_prune as prune
    monkeypatch.setattr(vm,'locked',contextlib.nullcontext)
    monkeypatch.setattr(prune,'builders',lambda:{})
    monkeypatch.setattr(prune,'process_table',lambda:{})
    monkeypatch.setattr(images.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,3,'inactive\n',''))
    monkeypatch.setattr(images,'run',lambda *a,**kw:json.dumps([{'Id':'captured'}]))
    monkeypatch.setattr(containers,'all_containers',lambda:[])
    monkeypatch.setattr(vm,'read',lambda key:{'role':'template','name':'mac15','ready':True,'image':'wrong'})
    monkeypatch.setattr(vm,'stop_container',lambda *a,**kw:pytest.fail('Must not stop on mismatched capture'))
    with pytest.raises(ValueError,match='identity'):images.reset_base('mac15')


def test_vm_reset_restores_only_private_working_tree_from_golden(monkeypatch,tmp_path):
    import mykos_containers as containers,mykos_prune as prune,shutil
    monkeypatch.setattr(vm,'locked',contextlib.nullcontext)
    monkeypatch.setattr(prune,'builders',lambda:{})
    monkeypatch.setattr(prune,'process_table',lambda:{})
    monkeypatch.setattr(images.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,3,'inactive\n',''))
    monkeypatch.setattr(images,'run',lambda *a,**kw:json.dumps([{'Id':'captured'}]))
    monkeypatch.setattr(containers,'all_containers',lambda:[])
    monkeypatch.setattr(containers,'unshared',lambda *a:None)
    monkeypatch.setattr(vm,'read',lambda key:{'role':'template','name':'mac15','ready':True,'image':'captured'})
    monkeypatch.setattr(vm,'storage',lambda key:tmp_path)
    golden=tmp_path/'golden';golden.mkdir();(golden/'saved').write_text('captured')
    (golden/'.mykos-checkpoint').mkdir()
    working=tmp_path/'storage';working.mkdir();(working/'unsaved').write_text('discard')
    monkeypatch.setattr(vm,'restore_manifest',lambda *a:{})
    monkeypatch.setattr(vm,'tree_copy',shutil.copytree)
    monkeypatch.setattr(vm,'wait_for_restore',lambda *a:None)
    launched=[]
    def launch(*args):
        request=working/'.mykos-checkpoint/restore.request'
        assert request.read_text()=='restore once\n'
        assert request.stat().st_mode & 0o777==0o600
        launched.append(args)
    monkeypatch.setattr(vm,'launch',launch)
    assert images.reset_base('mac15')['state']=='reset'
    assert (working/'saved').read_text()=='captured' and not (working/'unsaved').exists()
    assert (golden/'saved').read_text()=='captured'
    assert not (golden/'.mykos-checkpoint/restore.request').exists()
    assert launched and not list(tmp_path.glob('.reset-before-*'))
