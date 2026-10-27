from pathlib import Path
from contextlib import nullcontext
import importlib.machinery,importlib.util,json,os,sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_backup_restore as backup
from test_backup_identity import data


def files(root,values):
    (root/'etc').mkdir(parents=True)
    for name,text in values.items():(root/'etc'/name).write_text(text)


@pytest.fixture
def customer(tmp_path):
    root=tmp_path/'backups';account=root/'tfrsync-01234567';account.mkdir(parents=True)
    files(account,data('alice:x:1001:1002:A:/home/alice:/bin/bash\ntfrsync-01234567:x:1004:1004:B:/home/tfrsync-01234567:/bin/bash\n','app:x:1002:alice\ntfrsync-01234567:x:1004:\n'))
    for rel in ('usr2','home/alice','home/tfrsync-01234567'):(account/rel).mkdir(parents=True)
    return account,{'backup_root':str(root)}


def test_mounts_are_complete_direct_rw_trees_with_separate_private_identities(customer):
    account,cfg=customer
    pairs=backup.mount_pairs('01234567',cfg)
    assert pairs==[(account/'usr2','/usr2'),(account/'etc','/etc'),(account/'home','/home')]
    args=backup.mount_arguments('01234567',cfg)
    assert all('ro=false' in arg for arg in args[1::2])
    assert not any('passwd' in arg or 'tfrsync-01234567/home/tfrsync' in arg for arg in args)


def test_backup_available_without_application_adapter(customer):
    _,cfg=customer
    import mykos_profiles
    assert mykos_profiles.has_backup('01234567',cfg)
    assert not mykos_profiles.has_backup('76543210',cfg)


def test_source_symlink_escape_is_rejected(customer,tmp_path):
    account,cfg=customer
    (account/'usr2').rmdir();(account/'usr2').symlink_to(tmp_path)
    with pytest.raises(ValueError,match='escapes'):backup.mount_pairs('01234567',cfg)


def test_guest_symlink_escape_rejected(tmp_path):
    root=tmp_path/'guest';root.mkdir();(root/'home').symlink_to(tmp_path)
    with pytest.raises(ValueError):backup.guest_path(root,'home/user')
    with pytest.raises(ValueError):backup.guest_path(Path('/'),'etc/passwd')


def test_prepare_keeps_source_files_and_drops_guest_backup_user(customer,tmp_path,monkeypatch):
    account,cfg=customer;root=tmp_path/'guest'
    base=data('tfrsync-01234567:x:1004:1004:B:/home/tfrsync-01234567:/bin/bash\n','tfrsync-01234567:x:1004:\n')
    files(root,base);(root/'home/tfrsync-01234567').mkdir(parents=True)
    (root/'home/tfrsync-01234567/private').write_text('stale')
    before={p.relative_to(account):p.read_bytes() for p in (account/'etc').iterdir()}
    # Unprivileged local test covers control/data handling; Linux test covers chown.
    monkeypatch.setattr(backup.os,'chown',lambda *args:None)
    real_apply=backup.identity.apply
    def apply(root,plan):
        for name,text in plan['files'].items():(root/'etc'/name).write_text(text)
        return plan['summary']
    monkeypatch.setattr(backup.identity,'apply',apply)
    result=backup.prepare('01234567',cfg,root,'01234567.test.example','192.168.22.80')
    assert result['mode']=='direct-rw'
    assert 'tfrsync-' not in (root/'etc/passwd').read_text()
    assert not (root/'home/tfrsync-01234567').exists()
    assert (account/'home/tfrsync-01234567').is_dir()
    assert before=={p.relative_to(account):p.read_bytes() for p in (account/'etc').iterdir()}
    assert '192.168.22.80 01234567.test.example 01234567' in (root/'etc/hosts').read_text()
    assert 'managed=0' in (root/'etc/NetworkManager/conf.d/90-mykos-podman.conf').read_text()


def launcher():
    path=Path(__file__).resolve().parents[1]/'services/helpers/mykos-stage-pos'
    loader=importlib.machinery.SourceFileLoader('backup_stage_test',str(path))
    spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    return module


def test_restore_is_prepared_before_start_and_failure_removes_only_created_guest(customer,tmp_path,monkeypatch):
    account,cfg=customer;module=launcher();events=[]
    monkeypatch.setattr(module.vm,'locked',nullcontext)
    monkeypatch.setattr(module,'config',lambda:cfg)
    monkeypatch.setattr(module,'containers',lambda:[])
    monkeypatch.setattr(module.profile,'services',lambda:())
    monkeypatch.setattr(module.socket,'gethostname',lambda:'test.example')
    monkeypatch.setattr(module.network,'arguments',lambda *a:['--network','test','--ip','192.168.22.80'])
    monkeypatch.setattr(module,'Path',lambda path:tmp_path/path.lstrip('/'))
    monkeypatch.setattr(module,'prepare_backup',lambda *a:events.append('prepare'))
    def run(args,**kw):
        events.append(args)
        return 'cid'
    monkeypatch.setattr(module,'run',run)
    module.stage('01234567','yes','localhost/rh9')
    create=next(x for x in events if isinstance(x,list) and x[1]=='create')
    assert '--no-hosts' in create and '-d' not in create
    assert events.index('prepare')<events.index([module.PODMAN,'start','cid'])
    assert not any('rm' in x for x in events)
    def fail(*args):raise ValueError('identity failure')
    __import__('shutil').rmtree(tmp_path/'usr/local/share/mykos/pos/.01234567/.mykos-restore')
    monkeypatch.setattr(module,'prepare_backup',fail);events.clear()
    with pytest.raises(ValueError,match='identity failure'):module.stage('01234567','yes','localhost/rh9')
    assert [module.PODMAN,'rm','--force','cid'] in events
    assert not any('start' in x for x in events)
    assert account.is_dir()


def test_unmount_on_identity_error_and_reject_wrong_container_root(tmp_path,monkeypatch):
    module=launcher();calls=[];root=tmp_path/'guest';root.mkdir()
    value={'Id':'cid','Name':'01234567.test','State':{'Running':False},'GraphDriver':{'Data':{'MergedDir':str(root)}}}
    def run(args,**kw):
        calls.append(args)
        if args[1]=='inspect':return json.dumps([value])
        if args[1:]==['mount','--format','json']:return json.dumps([{'id':'cid','mountpoint':value['GraphDriver']['Data']['MergedDir']}])
        return str(root)
    monkeypatch.setattr(module,'run',run)
    def fail(*args):raise ValueError('bad account')
    monkeypatch.setattr(module.backup,'prepare',fail)
    with pytest.raises(ValueError,match='bad account'):module.prepare_backup('cid','01234567.test','01234567',{},[])
    assert calls[-1]==[module.PODMAN,'unmount','cid']
    value['GraphDriver']['Data']['MergedDir']=str(tmp_path/'wrong')
    with pytest.raises(ValueError,match='does not belong'):module.prepare_backup('cid','01234567.test','01234567',{},[])
    assert calls[-1]==[module.PODMAN,'unmount','cid']


def test_private_files_live_under_existing_instance_storage(customer,tmp_path):
    vpn=tmp_path/'vpn';vpn.mkdir()
    root,args=backup.private_mounts(vpn)
    assert root==vpn/'.mykos-restore'
    assert 'destination=/etc/passwd,ro=false' in ' '.join(args)
    assert 'destination=/etc/machine-id,ro=false' in ' '.join(args)
    assert root.stat().st_mode & 0o777==0o700
    with pytest.raises(FileExistsError):backup.private_mounts(vpn)
