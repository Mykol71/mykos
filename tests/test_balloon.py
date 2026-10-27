import contextlib
import json
from pathlib import Path
import sys
import time
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_vm as vm

@pytest.fixture
def guest(monkeypatch):
    value={'os':'ubuntu24.04','role':'instance','name':'test'}
    calls=[];saved=[]
    stats={'last-update':time.time(),'stats':{'stat-available-memory':2*1024**3,'stat-total-memory':4*1024**3}}
    class Monitor:
        def __init__(self,*args):pass
        def command(self,command):
            calls.append(command)
            if command=='info balloon':return 'balloon: actual=2176'
            if command.startswith('qom-get'):return json.dumps(stats)
            if command=='info status':return 'VM status: running'
            return ''
        def close(self):pass
    monkeypatch.setattr(vm,'QemuMonitor',Monitor)
    monkeypatch.setattr(vm,'locked',contextlib.nullcontext)
    monkeypatch.setattr(vm,'read',lambda key:value)
    monkeypatch.setattr(vm,'save',lambda key,data:saved.append(dict(data)))
    return value,stats,calls,saved


def test_balloon_guarded_request_and_repeat_block(guest):
    value,stats,calls,saved=guest
    result=vm.memory('12345678',2048)
    assert result['state']=='requested' and result['actual_mib']==2176
    assert 'balloon 2048' in calls and saved
    with pytest.raises(ValueError,match='previous'):vm.memory('12345678',2048)

@pytest.mark.parametrize('problem',['stale','sentinel','low-memory','too-large-step','macos'])
def test_balloon_rejects_unsafe_reduction(guest,problem):
    value,stats,calls,saved=guest
    target=2048
    if problem=='stale':stats['last-update']=time.time()-30
    if problem=='sentinel':stats['stats']['stat-available-memory']=2**64-1
    if problem=='low-memory':stats['stats']['stat-available-memory']=1024**3
    if problem=='too-large-step':target=3072
    if problem=='macos':value['os']='mac15'
    with pytest.raises(ValueError):vm.memory('12345678',target)
    assert not any(c.startswith('balloon ') for c in calls) and not saved

@pytest.mark.parametrize('target',[True,2047,4097,'3968'])
def test_invalid_balloon_target_rejected(guest,target):
    with pytest.raises(ValueError):vm.memory('12345678',target)
    assert not guest[2]
