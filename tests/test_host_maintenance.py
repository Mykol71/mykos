"""Failure-oriented host maintenance tests; no live guest or host operations."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
import mykos_host_maintenance as m
import mykos_snapshots as snap
import mykos_linux as linux

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'ROOT',tmp_path/'journal')
    monkeypatch.setattr(m,'boot_id',lambda:'boot-a')
    monkeypatch.setattr(m,'blockers',lambda:[])
    monkeypatch.setattr(m.vm,'safe_dir',lambda p:Path(p).mkdir(parents=True,exist_ok=True,mode=0o700))
    monkeypatch.setattr(m,'schedule',lambda action:None)
    monkeypatch.setattr(m.os,'sync',lambda:None)
    monkeypatch.setattr(m,'verify_snapshot',lambda item:{'archive_sha256':'a'*64,'metadata_sha256':'b'*64})
    guest={'name':'12345678.native','container_id':'id-original','code':'12345678','mode':'memory','kind':'native','pid':4,'started_at':'start','issues':[],'phase':'pending'}
    state={'Id':'id-original','State':{'Running':True,'Pid':4,'StartedAt':'start'}}
    monkeypatch.setattr(m.vm,'inspect',lambda name:copy.deepcopy(state))
    monkeypatch.setattr(m,'inventory',lambda:[copy.deepcopy(state)] if state['State']['Running'] else [])
    value={'format':1,'id':'a'*32,'state':'queued','hostname':m.socket.gethostname(),'boot_id':'boot-a','kernel':os.uname().release,'instances':[guest]}
    m.write(value)
    monkeypatch.setattr(snap,'capture',lambda code:{'id':'1234567890-abcdef123456'})
    monkeypatch.setattr(linux,'stop',lambda code:state['State'].update(Running=False))
    monkeypatch.setattr(snap,'restore',lambda code,ident:state['State'].update(Running=True))
    return value,state

def test_save_ready_recover_and_admission(env):
    with pytest.raises(ValueError,match='maintenance'):m.admission()
    m.prepare_worker();v=m.read();assert v['state']=='ready';assert v['instances'][0]['verification']
    m.recover_worker();assert m.read()['state']=='recovered';m.admission()

def test_blocker_never_stops(env,monkeypatch):
    monkeypatch.setattr(m,'blockers',lambda:['updates running'])
    with pytest.raises(ValueError,match='updates'):m.prepare_worker()
    assert env[1]['State']['Running'];assert m.read()['state']=='attention'

def test_capture_failure_never_stops(env,monkeypatch):
    def fail(*args):raise RuntimeError('capture failed')
    monkeypatch.setattr(snap,'capture',fail)
    with pytest.raises(RuntimeError):m.prepare_worker()
    assert env[1]['State']['Running'];assert m.read()['state']=='attention'
    m.recover_worker();assert m.read()['state']=='recovered';assert m.read()['instances'][0]['phase']=='untouched'

def test_verify_failure_never_stops(env,monkeypatch):
    def fail(*args):raise ValueError('tampered')
    monkeypatch.setattr(m,'verify_snapshot',fail)
    with pytest.raises(ValueError):m.prepare_worker()
    assert env[1]['State']['Running'];assert m.read()['state']=='attention'

def test_crash_after_stop_intent_recovers(env):
    v,state=env;v['instances'][0].update(phase='stopping',snapshot='1234567890-abcdef123456');m.write(v)
    state['State']['Running']=False;m.recover_worker();assert m.read()['state']=='recovered'

def test_replacement_not_touched(env):
    env[1]['Id']='replacement'
    with pytest.raises(ValueError,match='inventory changed'):m.prepare_worker()
    m.recover_worker();assert m.read()['state']=='attention';assert env[1]['State']['Running']

def test_new_boot_running_guest_not_overwritten(env,monkeypatch):
    monkeypatch.setattr(m,'boot_id',lambda:'boot-b')
    m.recover_worker();assert m.read()['state']=='attention';assert env[1]['State']['Running']

def test_kernel_change_never_falls_back_cold(env,monkeypatch):
    m.prepare_worker();v=m.read();v['kernel']='different-kernel';m.write(v)
    called=[];monkeypatch.setattr(linux,'start',lambda code:called.append(code))
    m.recover_worker();assert not called;assert not env[1]['State']['Running'];assert m.read()['state']=='attention'

def test_restore_failure_retry_does_not_recapture(env,monkeypatch):
    m.prepare_worker()
    def fail(*args):raise RuntimeError('restore failed')
    monkeypatch.setattr(snap,'restore',fail);m.recover_worker();assert m.read()['state']=='attention'
    monkeypatch.setattr(snap,'restore',lambda *args:env[1]['State'].update(Running=True));m.recover_worker();assert m.read()['state']=='recovered'

def test_reboot_requires_ready_and_exact_hostname(env,monkeypatch):
    calls=[];monkeypatch.setattr(m,'run',lambda *a,**k:calls.append(a))
    with pytest.raises(ValueError):m.reboot('wrong-host')
    with pytest.raises(ValueError):m.reboot(m.socket.gethostname())
    assert not calls
    m.prepare_worker();m.reboot(m.socket.gethostname());assert not calls;assert m.read()['state']=='verifying-reboot'
    m.reboot_worker();assert len(calls)==1;assert calls[0][0]==['/usr/bin/systemctl','--no-block','reboot'];assert m.read()['state']=='rebooting'

def test_reboot_rejects_new_work(env,monkeypatch):
    m.prepare_worker();monkeypatch.setattr(m,'blockers',lambda:['sync started'])
    with pytest.raises(ValueError,match='New work'):m.reboot(m.socket.gethostname())
    assert m.read()['state']=='ready'

def test_explicit_restart_only(env,monkeypatch):
    v,state=env;v['instances'][0]['mode']='restart';m.write(v)
    def fail(*a):raise AssertionError('RAM capture should not run')
    monkeypatch.setattr(snap,'capture',fail)
    m.prepare_worker();monkeypatch.setattr(linux,'start',lambda code:state['State'].update(Running=True));m.recover_worker();assert m.read()['state']=='recovered'

def test_no_duplicate_recovery(env,monkeypatch):
    m.prepare_worker();m.recover_worker()
    monkeypatch.setattr(snap,'restore',lambda *a:pytest.fail('duplicate restore'));m.recover_worker()

def test_journal_symlink_rejected(env,tmp_path):
    p=m.ROOT/'status.json';p.unlink();p.symlink_to(tmp_path/'elsewhere')
    # Broken symlink must also fail closed.
    with pytest.raises((ValueError,OSError)):m.read()


def test_resume_queued_is_atomic(env,monkeypatch):
    m.request_resume();assert m.read()['state']=='resume-requested'
    monkeypatch.setattr(snap,'capture',lambda *a:pytest.fail('cancelled queue captured a guest'))
    m.prepare_worker();m.recover_worker();assert m.read()['state']=='recovered'

def test_resume_forbidden_during_scheduled_reboot(env):
    v,state=env;v['state']='rebooting';m.write(v)
    with pytest.raises(ValueError):m.request_resume()

def test_explicit_failed_ram_restart(env,monkeypatch):
    m.prepare_worker();v=m.read();v['kernel']='different';m.write(v);m.recover_worker()
    with pytest.raises(ValueError):m.request_resume('wrong-host',['12345678'])
    assert m.read()['instances'][0]['mode']=='memory'
    m.request_resume(m.socket.gethostname(),['12345678'])
    monkeypatch.setattr(linux,'start',lambda code:env[1]['State'].update(Running=True))
    m.recover_worker();assert m.read()['state']=='recovered';assert m.read()['instances'][0]['restart_after_failure_accepted']

def test_preparation_binds_reviewed_plan(env,monkeypatch):
    from contextlib import nullcontext
    v,state=env;v['state']='cancelled';m.write(v)
    monkeypatch.setattr(m.vm,'locked',nullcontext)
    plan={'hostname':m.socket.gethostname(),'boot_id':'boot-a','kernel':os.uname().release,'instances':[dict(v['instances'][0],mode='restart')],'blockers':[],'plan_token':'a'*64}
    monkeypatch.setattr(m,'plan',lambda:copy.deepcopy(plan))
    with pytest.raises(ValueError,match='plan changed'):m.prepare(m.socket.gethostname(),['12345678'],'b'*64)
    with pytest.raises(ValueError,match='restart consent'):m.prepare(m.socket.gethostname(),[],'a'*64)
    assert m.read()['state']=='cancelled'
    m.prepare(m.socket.gethostname(),['12345678'],'a'*64)
    assert m.read()['state']=='queued'

def test_peer_forwarding_denies_maintenance():
    import mykos_peers
    assert not mykos_peers.allowed_path('/api/web/host/maintenance/plan')
    assert not mykos_peers.allowed_path('/api/web/host/maintenance/prepare')


def test_readiness_storage_headroom_and_identity_token(env,monkeypatch,tmp_path):
    state=env[1];state['Name']='/12345678.native';state['HostConfig']={'Memory':0}
    marker=tmp_path/'vm.json';marker.write_text('{}');storage=tmp_path/'vm';(storage/'storage').mkdir(parents=True)
    monkeypatch.setattr(m.vm,'record_path',lambda code:marker)
    monkeypatch.setattr(m.vm,'read',lambda code:{'name':'12345678.native','role':'instance','ready':True,'ram_mib':2048})
    monkeypatch.setattr(m.vm,'storage',lambda code:storage)
    monkeypatch.setattr(m.vm,'storage_root',lambda:storage)
    monkeypatch.setattr(m,'run',lambda args,**kw:'4096 '+str(storage) if '/usr/bin/du' in args else '/boot/vmlinuz-'+os.uname().release)
    monkeypatch.setattr(m.shutil,'disk_usage',lambda path:SimpleNamespace(free=100*1024**3))
    first=m.plan();assert not first['blockers'];assert first['instances'][0]['preservation_estimate_bytes']>4*1024**3
    state['State']['Pid']+=1
    assert m.plan()['plan_token']!=first['plan_token']
    monkeypatch.setattr(m.shutil,'disk_usage',lambda path:SimpleNamespace(free=1024))
    assert any('headroom' in reason for reason in m.plan()['blockers'])


def test_maintenance_api_refuses_peer_before_helper(monkeypatch):
    import host_maintenance as web
    from starlette.requests import Request
    from fastapi import HTTPException
    request=Request({'type':'http','state':{'mykos_peer':{'id':'a'*32,'node':'b'*32,'actor':'c'*64}},'headers':[]})
    monkeypatch.setattr(web,'invoke',lambda *a:pytest.fail('Peer reached host maintenance helper'))
    for call in (lambda:web.plan(request),lambda:web.status(request),
                 lambda:web.prepare(web.Prepare(hostname='host',plan_token='a'*64),request),
                 lambda:web.resume(web.Resume(),request)):
        with pytest.raises(HTTPException) as error:call()
        assert error.value.status_code==403

def test_maintenance_api_refuses_scoped_operator(monkeypatch):
    import host_maintenance as web
    import instance_transfers,app,mykos_users
    from fastapi import HTTPException
    monkeypatch.setattr(instance_transfers,'admin',lambda *a,**k:None)
    monkeypatch.setattr(app,'mykos_terminal_permission',lambda *a,**k:{'allowed':True,'username':'scoped'})
    monkeypatch.setattr(mykos_users,'is_admin',lambda name:False)
    monkeypatch.setattr(web,'invoke',lambda *a:pytest.fail('Scoped operator reached helper'))
    with pytest.raises(HTTPException) as error:web.status(object())
    assert error.value.status_code==403


def test_reboot_verification_failure_never_schedules_reboot(env,monkeypatch):
    m.prepare_worker();m.reboot(m.socket.gethostname())
    calls=[];monkeypatch.setattr(m,'run',lambda *a,**k:calls.append(a))
    def fail(*a):raise ValueError('Snapshot verification failed')
    monkeypatch.setattr(m,'verify_snapshot',fail)
    with pytest.raises(ValueError):m.reboot_worker()
    assert not calls;assert m.read()['state']=='attention'


def test_readiness_deadline_never_starts_preparation(env,monkeypatch):
    import time,signal
    before=m.read()
    monkeypatch.setattr(m,'READINESS_SECONDS',.02)
    monkeypatch.setattr(m,'_plan',lambda:time.sleep(.2))
    with pytest.raises(ValueError,match='no preparation was started'):m.plan()
    assert m.read()==before and env[1]['State']['Running']
    assert signal.getitimer(signal.ITIMER_REAL)[0]==0
