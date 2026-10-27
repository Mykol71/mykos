"""Queue lifecycle and guest transport tests; never run commands on production."""
import base64
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_commands as q
import mykos_vm as vm

@pytest.fixture
def queue(tmp_path,monkeypatch):
    monkeypatch.setattr(q,'ROOT',tmp_path/'commands')
    q.init()
    return q.ROOT

def put(key='job1',**fields):
    return q.dispatch(['command-put',key,json.dumps({'command':'printf hello',**fields})])

def test_idempotent_and_pending_reschedule_cancel(queue):
    first=put();assert first['state']=='pending'
    assert put()==first
    updated=put(command='printf later');assert updated['created_at']==first['created_at']
    assert q.dispatch(['command-cancel','job1'])['state']=='cancelled'
    assert put(command='printf later')['state']=='cancelled'
    assert put(command='different')['status_code']==409
    assert q.dispatch(['command-get','missing'])['status_code']==404
    assert 'command' not in q.dispatch(['command-list'])['commands'][0]
    assert queue.stat().st_mode & 0o777==0o700
    assert (queue/'job1.json').stat().st_mode & 0o777==0o600

@pytest.mark.parametrize('fields',[{'target':'../oops'},{'timeout_seconds':0},{'timeout_seconds':True},
 {'run_at':'2026-10-25T10:00:00'},{'command':''},{'command':'bad\0'},{'command':'x'*16001},{'extra':'bad'}])
def test_validation(queue,fields):
    assert put(**fields)['status_code']==400


def test_symlinks_rejected(queue,tmp_path):
    (queue/'evil.json').symlink_to(tmp_path/'outside')
    with pytest.raises(OSError):q.read('evil')
    assert q.dispatch(['command-get','../bad'])['status_code']==400


def test_collect_due_serial_per_target_and_no_retries(queue,monkeypatch):
    put('one');put('two');put('future',target='01234567',run_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
    calls=[]
    monkeypatch.setattr(q.subprocess,'run',lambda args,**kw: calls.append(args) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(q,'service_active',lambda key:False)
    q.collect();assert len(calls)==1
    assert q.read('one')['state']=='starting';assert q.read('two')['state']=='pending'
    value=q.read('one');value['updated_at']-=100;q.save(value)
    q.collect();assert q.read('one')['state']=='interrupted'
    assert q.read('two')['state']=='starting'
    assert q.read('future')['state']=='pending'


def test_worker_only_runs_claimed_once_and_records_failure(queue,monkeypatch):
    put();calls=[]
    monkeypatch.setattr(q,'execute',lambda v: calls.append(v) or ('shell',dict(exit_code=0,output='hello',truncated=False,timed_out=False)))
    q.work('job1');assert not calls
    value=q.read('job1');value['state']='starting';q.save(value)
    q.work('job1');q.work('job1');assert len(calls)==1
    assert q.read('job1')['state']=='succeeded'
    assert put()['state']=='succeeded'
    assert put(command='again')['status_code']==409


def test_shell_timeout_and_output_bound():
    result=q.shell(['/bin/bash','-c','printf hello; exit 3'],2)
    assert result['output']=='hello' and result['exit_code']==3
    result=q.shell(['/bin/bash','-c','while :; do printf abcdefghijklmnopqrstuvwxyz; done'],.15)
    assert result['timed_out'] and result['truncated'] and len(result['output'])==q.LIMIT


def test_windows_uses_guest_powershell_not_podman_exec(queue,monkeypatch,tmp_path):
    record=tmp_path/'vm.json';record.write_text('{}')
    monkeypatch.setattr(vm,'record_path',lambda code:record)
    monkeypatch.setattr(vm,'read',lambda code:{'os':'win11','name':'01234567.example'})
    calls=[]
    class Agent:
        def __init__(self,value):assert value['os']=='win11'
        def call(self,cmd,args=None):
            calls.append((cmd,args))
            return {'pid':42} if cmd=='guest-exec' else {'exited':True,'exitcode':0,'out-data':base64.b64encode(b'done').decode()}
        def close(self):calls.append(('closed',None))
    monkeypatch.setattr(q,'GuestAgent',Agent)
    monkeypatch.setattr(q,'shell',lambda *a:pytest.fail('Windows must never execute in the Linux wrapper'))
    script='Get-Date; Write-Output "a $b"'
    interpreter,result=q.execute(dict(target='01234567',command=script,timeout_seconds=2))
    assert interpreter=='powershell' and result['output'].strip()=='done'
    args=calls[0][1];assert args['path'].endswith('powershell.exe')
    assert base64.b64decode(args['arg'][-1]).decode('utf-16le')==script
    assert calls[-1][0]=='closed'


def test_windows_timeout_kills_only_its_guest_process(monkeypatch):
    calls=[]
    class Agent:
        def __init__(self,value):pass
        def call(self,cmd,args=None):
            calls.append((cmd,args));return {'pid':42} if cmd=='guest-exec' else {'exited':False}
        def close(self):pass
    monkeypatch.setattr(q,'GuestAgent',Agent)
    result=q.powershell({},'Get-Date',-1)
    assert result['timed_out']
    assert calls[-1][1]['path'].endswith('taskkill.exe')
    assert calls[-1][1]['arg']==['/PID','42','/T','/F']
