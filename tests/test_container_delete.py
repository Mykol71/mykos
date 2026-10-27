"""Deletion uses mocked Podman and isolated disposable data only."""
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_containers as delete
import mykos_vm as vm

SHOP='12345678'
CID='a'*64

@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setattr(vm,'ROOT',tmp_path/'pos/.vm');monkeypatch.setattr(vm,'POS',tmp_path/'pos')
    monkeypatch.setattr(vm,'storage_root',lambda:tmp_path/'disks')
    vm.safe_dir(vm.ROOT)
    state={'Id':CID,'Name':SHOP+'.test','State':{'Running':True},'HostConfig':{'AutoRemove':False},'Config':{'Labels':{}},'Mounts':[]}
    present={CID:state};calls=[]
    monkeypatch.setattr(delete,'target',lambda shop:state['Name'])
    monkeypatch.setattr(vm,'inspect',lambda name:state)
    monkeypatch.setattr(delete,'all_containers',lambda:list(present.values()))
    def stop(name):
        calls.append(['stop',name]);state['State']['Running']=False
    def run(args,**kw):
        calls.append(args)
        if args[1]=='rm':present.pop(args[-1])
        return ''
    monkeypatch.setattr(vm,'stop_container',stop);monkeypatch.setattr(vm,'run',run)
    return tmp_path,state,present,calls


def seed_private(root,state,desktop=False):
    private=vm.POS/('.'+SHOP);private.mkdir(mode=0o700);(private/'vpn.conn.conf').write_text('private VPN')
    state['Mounts'].append({'Source':str(private),'Destination':'/etc/ipsec.d'})
    (vm.POS/('.'+SHOP+'.container')).write_text(CID)
    linux=vm.POS/'.linux';linux.mkdir(mode=0o700);(linux/(SHOP+'.json')).write_text('{}')
    if desktop:
        state['Config']['Labels']={'io.mykos.vm.role':'instance','io.mykos.vm.os':'win11'}
        vm.save(SHOP,dict(name=state['Name'],role='instance',os='win11',ready=True))
        vm.safe_dir(vm.storage(SHOP)/'storage');(vm.storage(SHOP)/'storage/disk').write_text('VM disk')
        state['Mounts'].append({'Source':str(vm.storage(SHOP)/'storage'),'Destination':'/storage'})
    snapshots=vm.storage_root()/'snapshots'/SHOP;snapshots.mkdir(parents=True);(snapshots/'intentional').write_text('saved snapshot')
    backup=root/'rsync-backups';backup.mkdir();(backup/'data').write_text('backup')
    state['Mounts'].append({'Source':str(backup),'Destination':'/usr2'})
    return private,snapshots,backup


@pytest.mark.parametrize('desktop',[False,True])
def test_delete_private_data_and_keep_snapshots_images_backups(environment,desktop):
    root,state,present,calls=environment;private,snapshots,backup=seed_private(root,state,desktop)
    result=delete.delete(SHOP,SHOP,CID)
    assert result['state']=='deleted' and result['images_retained'] and result['snapshots_retained']
    assert not private.exists() and not vm.record_path(SHOP).exists()
    assert not (vm.POS/'.linux'/(SHOP+'.json')).exists()
    assert not (vm.POS/('.'+SHOP+'.container')).exists()
    if desktop:assert not vm.storage(SHOP).exists()
    assert (snapshots/'intentional').read_text()=='saved snapshot'
    assert (backup/'data').read_text()=='backup' and str(backup) in result['retained_mounts']
    assert calls[0]==['stop',CID] and calls[1]==[vm.PODMAN,'rm',CID]
    assert not present


def test_cancel_wrong_id_and_replaced_container_are_never_stopped(environment):
    root,state,present,calls=environment;seed_private(root,state)
    for confirmation,expected in [('87654321',CID),(SHOP,'b'*64)]:
        with pytest.raises(ValueError):delete.delete(SHOP,confirmation,expected)
    assert not calls


def test_shared_disk_refused_before_stop(environment):
    root,state,present,calls=environment;private,_,_=seed_private(root,state,True)
    present['b'*64]={'Id':'b'*64,'Name':'other','Mounts':[{'Source':str(vm.storage(SHOP)/'storage')}]}
    with pytest.raises(ValueError,match='another container'):delete.delete(SHOP,SHOP,CID)
    assert not calls and private.exists()


def test_symlink_in_private_data_refused_before_stop(environment):
    root,state,present,calls=environment;private,_,backup=seed_private(root,state)
    (private/'escape').symlink_to(backup,target_is_directory=True)
    with pytest.raises(ValueError,match='Symlink'):delete.delete(SHOP,SHOP,CID)
    assert not calls and (backup/'data').exists()


def test_stopped_container_deleted_without_another_stop(environment):
    root,state,present,calls=environment;seed_private(root,state);state['State']['Running']=False
    delete.delete(SHOP,SHOP,CID)
    assert calls==[[vm.PODMAN,'rm',CID]]


def test_stop_failure_preserves_all_data(environment,monkeypatch):
    root,state,present,calls=environment;private,_,_=seed_private(root,state,True)
    def fail(name):raise RuntimeError('stop failed')
    monkeypatch.setattr(vm,'stop_container',fail)
    with pytest.raises(RuntimeError,match='stop failed'):delete.delete(SHOP,SHOP,CID)
    assert private.exists() and vm.record_path(SHOP).exists() and not calls


def test_remove_failure_preserves_all_data(environment,monkeypatch):
    root,state,present,calls=environment;private,_,_=seed_private(root,state,True)
    def fail(args,**kw):raise RuntimeError('remove failed')
    monkeypatch.setattr(vm,'run',fail)
    with pytest.raises(RuntimeError,match='remove failed'):delete.delete(SHOP,SHOP,CID)
    assert private.exists() and vm.record_path(SHOP).exists()
