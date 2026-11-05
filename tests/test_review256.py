"""Regression tests for review255; synthetic storage and no live services."""
import contextlib,json,sys,threading,time
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'services/helpers'),str(ROOT/'services/web')]
import mykos_snapshots as snaps,mykos_vm as vm,mykos_linux as linux,mykos_runtime as runtime,mykos_containers as containers
import mykos_admin_api as api

@pytest.mark.parametrize('mount_type,overlap',[('volume','exact'),('volume','parent'),('volume','child'),('bind','exact')])
def test_restore_refuses_other_container_storage_before_stopping(tmp_path,monkeypatch,mount_type,overlap):
    upper=tmp_path/'upper';static=tmp_path/'static';shared=tmp_path/'pos/.12345678/data';folder=tmp_path/'snapshot'
    for p in (upper,static,shared,folder/'mounts/0/data'):p.mkdir(parents=True)
    (shared/'record').write_text('new live data')
    state={'Id':'a'*64,'Image':'image','GraphDriver':{'Name':'overlay','Data':{'UpperDir':str(upper)}},'StaticDir':str(static),'State':{'Running':True},'Mounts':[{'Type':mount_type,'Source':str(shared),'Destination':'/data','RW':True}]}
    other_source={'exact':shared,'parent':shared.parent,'child':shared/'nested'}[overlap]
    other={'Id':'b'*64,'Name':'other','State':{'Running':True},'Mounts':[{'Source':str(other_source)}]}
    metadata={'capture_mode':'criu-memory','name':'12345678.test','configuration':{'Id':state['Id']},'image':'image','mounts':snaps.native_data_mounts(state)}
    monkeypatch.setattr(vm,'POS',tmp_path/'pos');monkeypatch.setattr(vm,'locked',contextlib.nullcontext)
    monkeypatch.setattr(vm,'safe_dir',lambda p:None);monkeypatch.setattr(vm,'run',lambda *a,**kw:pytest.fail('Shared storage must be rejected before stopping/writing'))
    monkeypatch.setattr(linux,'inspect',lambda _:('12345678.test',state));monkeypatch.setattr(linux,'require_persistent',lambda _:None)
    monkeypatch.setattr(runtime,'require_container_capacity',lambda _:None)
    monkeypatch.setattr(snaps,'read_snapshot',lambda *a:(folder,metadata));monkeypatch.setattr(snaps,'restore_cgroup_context',lambda *a:None)
    monkeypatch.setattr(containers,'all_containers',lambda:[state,other])
    with pytest.raises(ValueError,match='also mounted by another container'):snaps.restore_native('12345678','snapshot')
    assert state['State']['Running'] and other['State']['Running']
    assert (shared/'record').read_text()=='new live data'


def test_cached_other_kind_does_not_wait_for_slow_repository(tmp_path,monkeypatch):
    entered=threading.Event();release=threading.Event();done=threading.Event();errors=[]
    monkeypatch.setattr(api,'_cache',{'backup':(time.monotonic(),{'cached':True})});monkeypatch.setattr(api,'_fetch_locks',{});monkeypatch.setattr(api,'_generation',{})
    def invoke(kind):
        assert kind=='repo-list';entered.set();assert release.wait(5);return {'repo':True}
    monkeypatch.setattr(api,'invoke',invoke)
    def fetch_repo():
        try:api.listing('repo',True)
        except BaseException as e:errors.append(e)
    def fetch_backup():
        try:assert api.listing('backup')=={'cached':True};done.set()
        except BaseException as e:errors.append(e)
    a=threading.Thread(target=fetch_repo);b=threading.Thread(target=fetch_backup)
    a.start()
    try:
        assert entered.wait(2);b.start();assert done.wait(2),'Cached backup waited for unrelated repo helper'
    finally:
        release.set();a.join(5)
        if b.ident:b.join(5)
    assert not errors


def test_inflight_inventory_cannot_repopulate_cache_after_mutation(monkeypatch):
    monkeypatch.setattr(api,'_cache',{});monkeypatch.setattr(api,'_fetch_locks',{});monkeypatch.setattr(api,'_generation',{})
    def invoke(kind,*args):
        if kind=='repo-list':
            api.perform('repo','enable','example',{})
            return {'stale':True}
        return {'changed':True}
    monkeypatch.setattr(api,'invoke',invoke)
    assert api.listing('repo')=={'stale':True}
    assert 'repo' not in api._cache
