"""Network tests are confined to a fake client; no bandwidth tests in unit tests."""
import sys,json,subprocess,fcntl,os,time,signal
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/web'))
import network_speed as ns

@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setattr(ns,'OUTPUT',tmp_path/'speed.json')
    monkeypatch.setattr(ns,'LOCK',tmp_path/'speed.lock')
    monkeypatch.setattr(ns,'BINARY',Path('/usr/bin/true'))
    monkeypatch.setattr(ns.os,'geteuid',lambda:1000)
    calls=[]
    def fake(args,timeout):
        calls.append((args,timeout))
        if '--version' in args:return b'Speedtest by Ookla 1.2.0.84 Linux\n'
        return json.dumps({'type':'result','download':{'bandwidth':125000000},'upload':{'bandwidth':12500000},'ping':{'latency':12.5},'interface':{'externalIp':'private'},'result':{'url':'do-not-publish'}}).encode()
    monkeypatch.setattr(ns,'execute',fake)
    return calls

def test_official_json_units_and_no_private_fields(environment):
    assert ns.collect(now=1000)=={'state':'ok'}
    result=ns.public(ns.read_cache(),1001)
    assert (result['download_mbps'],result['upload_mbps'],result['latency_ms'])==(1000,100,12.5)
    assert not result['stale'] and result['age_seconds']==1
    assert len(environment)==2 and environment[1][0][-2:]==['--format=json','--progress=no']
    assert 'private' not in ns.OUTPUT.read_text() and 'do-not-publish' not in ns.OUTPUT.read_text()

def test_no_more_than_once_per_minute(environment):
    ns.collect(now=1000);assert ns.collect(now=1059)=={'state':'not_due'};assert len(environment)==2
    ns.collect(now=1060);assert len(environment)==4

def test_nonoverlap_lock(environment):
    with ns.LOCK.open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        assert ns.collect(now=1000)=={'state':'already_running'}
        assert not environment and not ns.OUTPUT.exists()

def test_os_repository_client_is_rejected(environment,monkeypatch):
    monkeypatch.setattr(ns,'execute',lambda args,timeout:b'speedtest-cli 2.1.3\n')
    assert ns.collect(now=1000)=={'state':'error'}
    assert ns.public(ns.read_cache(),1000)['error']=='unsupported_client'

def test_missing_client_is_unavailable(environment,monkeypatch,tmp_path):
    monkeypatch.setattr(ns,'BINARY',tmp_path/'absent')
    assert ns.collect(now=1000)=={'state':'unavailable'}
    assert ns.public(ns.read_cache(),1000)['error']=='not_installed'

@pytest.mark.parametrize('failure',[subprocess.TimeoutExpired('speedtest',45),RuntimeError('command_failed'),ValueError('broken')])
def test_failure_retains_last_good_and_marks_stale(environment,monkeypatch,failure):
    ns.collect(now=1000)
    def fail(*args):raise failure
    monkeypatch.setattr(ns,'execute',fail);assert ns.collect(now=1060)['state']=='error'
    value=ns.public(ns.read_cache(),1061)
    assert value['download_mbps']==1000 and value['upload_mbps']==100 and value['stale']
    assert value['measured_at']==1000

@pytest.mark.parametrize('bad',[True,-1,float('inf'),float('nan'),'100',None,10**1000])
def test_invalid_cache_values_not_exposed(environment,bad):
    value=ns.public({'status':'ok','last_success':{'download_mbps':bad,'upload_mbps':2,'measured_at':1000}},1001)
    assert value['download_mbps'] is None and value['stale']

@pytest.mark.parametrize('raw',[{},[],{'type':'result','download':None}, {'type':'result','download':{'bandwidth':True},'upload':{'bandwidth':1}}])
def test_invalid_cli_json_is_error(environment,monkeypatch,raw):
    original=ns.execute
    monkeypatch.setattr(ns,'execute',lambda args,timeout:original(args,timeout) if '--version' in args else json.dumps(raw).encode())
    assert ns.collect(now=1000)=={'state':'error'}
    assert ns.read_cache()['error']=='invalid_result'

def test_age_and_reader_never_start_client(environment):
    ns.collect(now=1000);assert ns.public(now=1000+ns.STALE_AFTER+1)['stale']
    for _ in range(5):ns.public(now=1010)
    assert len(environment)==2

def test_cache_corruption_and_symlink_are_unavailable(environment,tmp_path):
    ns.OUTPUT.write_text('broken');assert ns.read_cache()=={}
    ns.OUTPUT.unlink();source=tmp_path/'secret';source.write_text('private');ns.OUTPUT.symlink_to(source)
    assert ns.read_cache()=={}

def test_interruption_is_recorded_and_lock_released(environment,monkeypatch):
    def interrupt(*args):raise InterruptedError('stop')
    original=ns.execute;monkeypatch.setattr(ns,'execute',interrupt)
    with pytest.raises(InterruptedError):ns.collect(now=1000)
    assert ns.read_cache()['error']=='interrupted'
    monkeypatch.setattr(ns,'execute',original);assert ns.collect(now=1060)['state']=='ok'

def test_real_timeout_reaps_child_group(tmp_path):
    # Existing system Python is the only fake process; it creates no disk/cache resources.
    pidfile=tmp_path/'pid'
    code='import os,time;open('+repr(str(pidfile))+',"w").write(str(os.getpid()));time.sleep(30)'
    with pytest.raises(subprocess.TimeoutExpired):ns.execute([sys.executable,'-B','-c',code],.3)
    pid=int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):os.kill(pid,0)

def test_existing_configured_user_is_child_only(environment,monkeypatch,tmp_path):
    from types import SimpleNamespace
    monkeypatch.setenv('MYKOS_SPEEDTEST_USER','existing-user')
    monkeypatch.setenv('MYKOS_SPEEDTEST_HOME',str(tmp_path))
    monkeypatch.setattr(ns.os,'geteuid',lambda:0)
    monkeypatch.setattr(ns.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=1234,pw_gid=1234,pw_dir='/home/existing-user'))
    monkeypatch.setattr(ns.os,'getgrouplist',lambda name,gid:[1234])
    env,options=ns.execution_options()
    assert env['HOME']==str(tmp_path) and env['USER']=='existing-user'
    assert options=={'user':1234,'group':1234,'extra_groups':[1234],'cwd':str(tmp_path)}

def test_invalid_configured_user_is_rejected(environment,monkeypatch):
    monkeypatch.setenv('MYKOS_SPEEDTEST_USER','missing')
    def missing(_):raise KeyError('missing')
    monkeypatch.setattr(ns.pwd,'getpwnam',missing)
    with pytest.raises(RuntimeError):ns.execution_options()


def test_twice_daily_interval_and_fresh_cache_between_slots(environment):
    ns.collect(now=1000)
    assert ns.public(now=1001)['interval_seconds']==12*60*60
    assert not ns.public(now=1000+6*60*60)['stale']
    assert not ns.public(now=1000+12*60*60)['stale']
    assert ns.public(now=1000+13*60*60+1)['stale']
    assert len(environment)==2, 'Cache reads must never launch a bandwidth test'
