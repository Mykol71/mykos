import contextlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time

import pytest

HELPERS=Path(__file__).resolve().parents[1]/'services/helpers'
sys.path.insert(0,str(HELPERS))
import mykos_runtime as runtime
import mykos_vm as vm


def inventory(count):
    return [{'Names':[f'{i:08d}.host'],'State':'running'} for i in range(count)]


@pytest.fixture
def full(monkeypatch,tmp_path):
    monkeypatch.setattr(vm,'ROOT',tmp_path/'registry')
    rows=inventory(50)
    monkeypatch.setattr(runtime,'run',lambda *a,**kw:json.dumps(rows))
    return rows


def test_fiftieth_allowed_fifty_first_denied_and_running_noop_allowed(full):
    full.pop()
    runtime.require_container_capacity()
    full.append({'Names':['12345678.host'],'State':'running'})
    with pytest.raises(ValueError,match='limit reached.*50'):runtime.require_container_capacity()
    runtime.require_container_capacity('12345678.host')
    assert runtime.container_capacity()=={'running':50,'limit':50}


def test_bases_builders_and_stopped_containers_do_not_consume_slots(full):
    full.extend([{'Names':['rh9'],'State':'running'}, {'Name':'/win11','State':{'Running':True}},
                 {'Names':['99999999.host'],'State':'exited'}])
    assert runtime.container_capacity()['running']==50
    full.pop(0)
    full.append({'Names':['mykos-preview-'+'a'*32],'State':'running'})
    assert runtime.container_capacity()['running']==50
    with pytest.raises(ValueError):runtime.require_container_capacity()


def test_failed_live_inventory_does_not_allow_start(full,monkeypatch):
    monkeypatch.setattr(runtime,'run',lambda *a,**kw:'not JSON')
    with pytest.raises(ValueError):runtime.require_container_capacity()


def test_two_concurrent_requests_can_only_claim_one_last_slot(full):
    full.pop()
    barrier=threading.Barrier(2);outcomes=[]
    def worker(number):
        barrier.wait()
        with vm.locked():
            try:
                runtime.require_container_capacity()
                time.sleep(.02)
                full.append({'Names':[f'{90000000+number}.host'],'State':'running'})
                outcomes.append('started')
            except ValueError:outcomes.append('blocked')
    threads=[threading.Thread(target=worker,args=(i,)) for i in range(2)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=5);assert not thread.is_alive()
    assert sorted(outcomes)==['blocked','started'] and len(full)==50


def load(name):
    loader=importlib.machinery.SourceFileLoader('capacity_'+name.replace('-','_'),str(HELPERS/name))
    spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    return module


def test_native_creation_rejected_before_host_configuration_or_data_writes(full,monkeypatch):
    stage=load('mykos-stage-pos')
    monkeypatch.setattr(stage,'config',lambda:pytest.fail('Admission must precede config/network/data mutation'))
    with pytest.raises(ValueError,match='limit reached'):stage.stage('99999999','no','rh9')


def test_stopped_native_restart_rejected(full,monkeypatch):
    import mykos_linux as linux
    monkeypatch.setattr(linux,'inspect',lambda shop:('99999999.host',{'State':{'Running':False}}))
    monkeypatch.setattr(linux,'require_persistent',lambda state:None)
    with pytest.raises(ValueError,match='limit reached'):linux.start('99999999')


def test_existing_running_native_remains_usable_at_cap(full,monkeypatch):
    import mykos_linux as linux
    monkeypatch.setattr(linux,'inspect',lambda shop:('00000000.host',{'State':{'Running':True}}))
    monkeypatch.setattr(linux,'require_persistent',lambda state:None)
    assert linux.start('00000000')['state']=='running'


def test_new_vm_and_preview_launch_rejected_before_disk_or_port_changes(full):
    with pytest.raises(ValueError,match='limit reached'):vm.start('99999999','win11')
    for role in ['instance','preview']:
        with vm.locked(),pytest.raises(ValueError,match='limit reached'):
            vm.launch('99999999',{'role':role,'name':'99999999.host'})


def test_vm_restart_and_text_menu_resume_rejected(full,monkeypatch,tmp_path):
    path=tmp_path/'existing';path.touch()
    monkeypatch.setattr(vm,'record_path',lambda key:path)
    monkeypatch.setattr(vm,'read',lambda key:{'name':'99999999.host','ready':True,'role':'instance','os':'win11'})
    monkeypatch.setattr(vm,'inspect',lambda name:{'State':{'Running':False},'Config':{'Labels':{'io.mykos.vm.os':'win11'}}})
    with pytest.raises(ValueError,match='limit reached'):vm.start('99999999')
    with pytest.raises(ValueError,match='limit reached'):vm.resume('99999999')


def test_vm_restore_rejected_before_replacing_disks(full,monkeypatch,tmp_path):
    import mykos_snapshots as snaps
    value={'name':'99999999.host','ready':True,'role':'instance','os':'win11'}
    saved={'capture_mode':'memory','configuration':{'os':'win11'},'name':value['name']}
    monkeypatch.setattr(snaps,'read_snapshot',lambda *a:(tmp_path,saved))
    monkeypatch.setattr(snaps,'target',lambda key:value['name'])
    monkeypatch.setattr(vm,'read',lambda key:value)
    monkeypatch.setattr(vm,'inspect',lambda name:{'State':{'Running':False},'Config':{'Labels':{'io.mykos.vm.os':'win11'}}})
    monkeypatch.setattr(vm,'restore_manifest',lambda *a:pytest.fail('Must reject before disk replacement'))
    with pytest.raises(ValueError,match='limit reached'):snaps.restore_vm('99999999','snapshot')


def test_native_restore_rejected_before_mount_or_criu_changes(full,monkeypatch,tmp_path):
    import mykos_snapshots as snaps
    import mykos_linux as linux
    monkeypatch.setattr(snaps,'read_snapshot',lambda *a:(tmp_path,{'capture_mode':'criu-memory'}))
    monkeypatch.setattr(linux,'inspect',lambda code:('99999999.host',{'State':{'Running':False}}))
    monkeypatch.setattr(linux,'require_persistent',lambda state:None)
    with pytest.raises(ValueError,match='limit reached'):snaps.restore_native('99999999','snapshot')


def test_preview_admission_rejected_without_queued_worker_or_copied_storage(full,monkeypatch,tmp_path):
    import mykos_snapshot_preview as preview
    monkeypatch.setattr(preview,'STATE',tmp_path/'previews')
    monkeypatch.setattr(preview,'actor',lambda value:value)
    monkeypatch.setattr(preview.snapshots,'read_snapshot',lambda *a:pytest.fail('Must reject before reading/copying snapshot'))
    with pytest.raises(ValueError,match='limit reached'):preview.create('99999999','saved','owner')
    assert not list((tmp_path/'previews').glob('*/status.json'))


def test_chart_counts_running_previews_from_fresh_collector_state(tmp_path,monkeypatch):
    import host_metrics
    monkeypatch.setattr(host_metrics,'BASE',tmp_path)
    monkeypatch.setattr(host_metrics,'PROC',tmp_path/'proc')
    monkeypatch.setattr(host_metrics.time,'time',lambda:1000)
    (tmp_path/'data').mkdir()
    (tmp_path/'data/status.json').write_text(json.dumps({'collected_at':999,'graph_totals':{'containers':49,'vpns':0},
       'container_capacity':{'running':50,'limit':50},'observations':{'a':{'status':'online'}}}))
    assert host_metrics.snapshot()['graph_counts']['containers']==50


def test_capacity_rejection_is_clear_http_conflict(monkeypatch):
    import socket
    import container_admin
    from fastapi import HTTPException
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def settimeout(self,*args):pass
        def connect(self,*args):pass
        def sendall(self,*args):pass
        def recv(self,*args):
            if getattr(self,'sent',False):return b''
            self.sent=True
            return json.dumps({'ok':False,'error':'Container limit reached (50 running instances/previews).'}).encode()
    monkeypatch.setattr(socket,'socket',lambda *a:Reply())
    with pytest.raises(HTTPException) as error:container_admin.invoke('start-pos','99999999')
    assert error.value.status_code==409 and '50' in error.value.detail
