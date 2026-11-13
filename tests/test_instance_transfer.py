"""Peer copy trust boundaries and failure handling; all fixtures are temporary."""
import io
import json
from pathlib import Path
import tarfile
from unittest.mock import Mock

import pytest
import mykos_instance_transfer as transfer
import mykos_peers as peers

JOB = 'a' * 32
PEER = 'b' * 32
HASH = 'c' * 64


def manifest(**updates):
    return {'protocol':1,'job':JOB,'destination':'97827701','sha256':HASH,'size':4096,
            'kind':'native','mode':'restart',**updates}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer,'STATE',tmp_path/'state')
    monkeypatch.setattr(transfer.vm,'storage_root',lambda:tmp_path)
    monkeypatch.setattr(transfer,'available',lambda code:None)
    return tmp_path


def archive(entries):
    result=io.BytesIO()
    with tarfile.open(fileobj=result,mode='w') as handle:
        for name,kind,value in entries:
            member=tarfile.TarInfo(name)
            if kind=='file':
                payload=value.encode();member.size=len(payload);handle.addfile(member,io.BytesIO(payload))
            elif kind=='directory':
                member.type=tarfile.DIRTYPE;handle.addfile(member)
            else:
                member.type=tarfile.SYMTYPE if kind=='symlink' else tarfile.LNKTYPE
                member.linkname=value;handle.addfile(member)
    result.seek(0)
    return tarfile.open(fileobj=result,mode='r:')


@pytest.mark.parametrize('entries',[
    [('../escape','file','bad')],
    [('/escape','file','bad')],
    [('file','file','one'),('file','file','two')],
    [('link','symlink','/etc'),('link/passwd','file','bad')],
    [('link/passwd','file','bad'),('link','symlink','/etc')],
    [('hard','hardlink','../outside')],
    [('a','symlink','/etc'),('b','hardlink','a')],
    [('a','file','not a directory'),('a/child','file','bad')],
])
def test_archive_rejects_host_path_writes(entries):
    with archive(entries) as handle,pytest.raises(ValueError):
        transfer.validated_members(handle)


def test_guest_symlink_is_retained_without_following():
    with archive([('etc','directory',''),('etc/localtime','symlink','/usr/share/zoneinfo/UTC'),('data','file','saved')]) as handle:
        members,total=transfer.validated_members(handle)
    assert len(members)==3 and total==5


def test_valid_hardlink_only_points_at_a_regular_guest_file():
    with archive([('data','file','saved'),('copy','hardlink','data')]) as handle:
        assert len(transfer.validated_members(handle)[0])==2


@pytest.mark.parametrize('update',[
    {'destination':'../other'},{'job':'a'*31},{'sha256':'no'}, {'size':0},{'size':True},
    {'size':transfer.MAX_BYTES+1},{'protocol':2},{'kind':'host'},{'mode':'unknown'},
    {'kind':'native','mode':'memory'},
])
def test_manifest_rejects_unsafe_or_unsupported_requests(update):
    with pytest.raises(ValueError):transfer.validate_manifest(manifest(**update))


def test_begin_is_idempotent_and_peer_bound(registry):
    first=transfer.begin(PEER,manifest())
    again=transfer.begin(PEER,manifest())
    assert first==again
    assert transfer.read(first['token'],PEER)['offset']==0
    with pytest.raises(ValueError):transfer.read(first['token'],'d'*32)
    with pytest.raises(ValueError):transfer.begin(PEER,manifest(size=4097))


def test_destination_is_reserved_during_upload(registry):
    transfer.begin(PEER,manifest())
    with pytest.raises(ValueError,match='reserved'):
        transfer.begin(PEER,manifest(job='d'*32))


def test_finish_never_imports_incomplete_or_corrupted_content(registry,monkeypatch):
    created=transfer.begin(PEER,manifest())
    schedule=Mock();monkeypatch.setattr(transfer,'schedule',schedule)
    with pytest.raises(ValueError,match='incomplete'):
        transfer.finish_receive(created['token'],PEER)
    data=transfer.read(created['token']);transfer.update(data,offset=data['size'])
    with pytest.raises(ValueError,match='checksum'):
        transfer.finish_receive(created['token'],PEER)
    schedule.assert_not_called()


def test_finish_launch_failure_preserves_retryable_upload(registry,monkeypatch):
    payload=b'capsule';created=transfer.begin(PEER,manifest(size=len(payload),sha256=__import__('hashlib').sha256(payload).hexdigest()))
    (transfer.spool(created['token'])/'capsule.tar').write_bytes(payload)
    data=transfer.read(created['token']);transfer.update(data,offset=len(payload))
    monkeypatch.setattr(transfer,'schedule',Mock(side_effect=RuntimeError('unit failed')))
    with pytest.raises(RuntimeError):transfer.finish_receive(created['token'],PEER)
    assert transfer.read(created['token'])['state']=='receiving'
    assert (transfer.spool(created['token'])/'capsule.tar').read_bytes()==payload


def test_memory_requires_conservative_cpu_compatibility():
    source={'architecture':'x86_64','vendor':'GenuineIntel','model':'CPU1','flags':['sse2','vmx']}
    assert transfer.compatible(source,{**source,'flags':['sse2','vmx','avx']})
    for updates in ({'model':'CPU2'},{'flags':['sse2']},{'architecture':'aarch64'},{'vendor':'AuthenticAMD'}):
        assert not transfer.compatible(source,{**source,**updates})


@pytest.mark.parametrize('path',[
 '/api/peers/instance-transfers/begin',
 '/api/peers/instance-transfers/capabilities/97827701',
 '/api/peers/instance-transfers/'+JOB,
 '/api/peers/instance-transfers/'+JOB+'/finish',
 '/api/peers/instance-transfers/'+JOB+'/chunk/4194304/'+HASH,
])
def test_peer_protocol_is_explicitly_allowed(path):
    assert peers.allowed_path(path)


@pytest.mark.parametrize('path',[
 '/api/web/instance-transfers','/api/web/instance-transfers/'+JOB,
 '/api/peers/instance-transfers/../../host/reboot',
 '/api/peers/instance-transfers/'+JOB+'/chunk/-1/'+HASH,
 '/api/peers/instance-transfers/'+JOB+'/cancel',
 '/api/peers/instance-transfers/capabilities/invalid',
])
def test_peer_cannot_relay_local_copy_controls_or_unlisted_operations(path):
    assert not peers.allowed_path(path)


def test_failure_cleanup_cannot_remove_another_container(registry,monkeypatch):
    data={'token':JOB,'materializing':True,'destination_name':'97827701.host','destination':'97827701'}
    import mykos_containers
    monkeypatch.setattr(mykos_containers,'all_containers',lambda:[{'Name':data['destination_name']}])
    monkeypatch.setattr(transfer.vm,'locked',__import__('contextlib').nullcontext)
    monkeypatch.setattr(transfer.vm,'inspect',lambda name:{'Config':{'Labels':{'io.mykos.peer-copy':'different'}}})
    run=Mock();monkeypatch.setattr(transfer.vm,'run',run)
    with pytest.raises(ValueError,match='ownership changed'):transfer.cleanup_destination(data)
    run.assert_not_called()


def test_full_admin_boundary_does_not_accept_a_scoped_operator(monkeypatch):
    import instance_transfers as web
    import app
    import mykos_users
    from fastapi import HTTPException
    monkeypatch.setattr(web,'admin',lambda request,modify=False:None)
    monkeypatch.setattr(app,'mykos_terminal_permission',lambda request,include_user=False:{'allowed':True,'username':'scoped-operator'})
    monkeypatch.setattr(mykos_users,'is_admin',lambda name:name=='full-admin')
    with pytest.raises(HTTPException) as error:web.local_admin(object(),True)
    assert error.value.status_code==403
    assert web.permission(object())=={'allowed':False}
    monkeypatch.setattr(app,'mykos_terminal_permission',lambda request,include_user=False:{'allowed':True,'username':'full-admin'})
    assert web.permission(object())=={'allowed':True}


def test_local_control_refuses_forwarded_peer_even_with_an_admin_actor(monkeypatch):
    import instance_transfers as web
    from starlette.requests import Request
    from fastapi import HTTPException
    request=Request({'type':'http','state':{'mykos_peer':{'id':PEER,'node':JOB,'actor':HASH}},'headers':[]})
    with pytest.raises(HTTPException) as error:web.local_admin(request,True)
    assert error.value.status_code==403


def test_receiver_requires_machine_authentication(monkeypatch):
    import instance_transfers as web
    from starlette.requests import Request
    from fastapi import HTTPException
    request=Request({'type':'http','state':{},'headers':[]})
    with pytest.raises(HTTPException) as error:web.peer_identity(request)
    assert error.value.status_code==401


def test_transfer_receipts_do_not_expose_private_manifest_or_owners():
    data={'token':JOB,'state':'ready','manifest':{'private':'do not expose'},'owner':HASH,'source_image_baseline':['private'],'receiver':PEER}
    assert transfer.public(data)=={'token':JOB,'state':'ready'}


def test_interrupted_native_capture_resumes_only_its_original_container(registry,monkeypatch):
    transfer.folder(JOB).mkdir()
    data={'token':JOB,'owner':HASH,'created':1,'code':'97827701','container_id':HASH,'source_pause_intent':True,'pause_guard':True}
    transfer.update(data)
    monkeypatch.setattr(transfer.vm,'locked',__import__('contextlib').nullcontext)
    monkeypatch.setattr(transfer,'target',lambda code:'97827701.host')
    monkeypatch.setattr(transfer.vm,'inspect',lambda name:{'Id':HASH,'Name':'97827701.host','State':{'Paused':True}})
    run=Mock();monkeypatch.setattr(transfer.vm,'run',run)
    transfer.recover_source(data)
    assert not data['source_pause_intent'] and not data['pause_guard']
    assert run.call_args_list[0].args[0]==[transfer.vm.PODMAN,'unpause',HASH]
    assert run.call_args_list[1].args[0][1:] == ['stop','mykos-copy-resume-'+JOB+'.timer']


def test_pause_recovery_never_touches_a_replaced_source(registry,monkeypatch):
    transfer.folder(JOB).mkdir()
    data={'token':JOB,'owner':HASH,'created':1,'code':'97827701','container_id':HASH,'source_pause_intent':True}
    transfer.update(data)
    monkeypatch.setattr(transfer.vm,'locked',__import__('contextlib').nullcontext)
    monkeypatch.setattr(transfer,'target',lambda code:'97827701.host')
    monkeypatch.setattr(transfer.vm,'inspect',lambda name:{'Id':'d'*64,'Name':'97827701.host','State':{'Paused':True}})
    run=Mock();monkeypatch.setattr(transfer.vm,'run',run)
    with pytest.raises(ValueError,match='identity changed'):transfer.recover_source(data)
    run.assert_not_called()


def test_interrupted_vm_copy_cancels_migration_before_resuming(registry,monkeypatch):
    transfer.folder(JOB).mkdir()
    data={'token':JOB,'owner':HASH,'created':1,'code':'97827701','container_id':HASH,'source_qemu_pause_intent':True}
    transfer.update(data)
    monkeypatch.setattr(transfer.vm,'locked',__import__('contextlib').nullcontext)
    monkeypatch.setattr(transfer,'select_source',lambda *a:{'state':{}})
    monkeypatch.setattr(transfer.vm,'read',lambda code:{'name':'97827701.host'})
    state={'migration':'active','status':'paused'};calls=[]
    class Monitor:
        def command(self,command):
            calls.append(command)
            if command=='info migrate':return 'Migration status: '+state['migration']
            if command=='migrate_cancel':state['migration']='cancelled'
            if command=='cont':state['status']='running'
            if command=='info status':return 'VM status: '+state['status']
            return ''
        def close(self):calls.append('close')
    monkeypatch.setattr(transfer.vm,'QemuMonitor',lambda value:Monitor())
    transfer.recover_source(data)
    assert calls.index('migrate_cancel')<calls.index('cont')
    assert not data['source_qemu_pause_intent']


def test_pause_guard_is_token_bound_and_bounded(registry,monkeypatch):
    transfer.folder(JOB).mkdir()
    data={'token':JOB,'owner':HASH,'created':1}
    run=Mock();monkeypatch.setattr(transfer.vm,'run',run)
    transfer.arm_pause_guard(data,seconds=420)
    command=run.call_args.args[0]
    assert '--on-active=420s' in command
    assert command[-3:]==['/usr/local/sbin/mykos-instance-transfer','recover-source',JOB]
    assert data['pause_guard'] is True


def test_abort_removes_only_incomplete_upload_and_is_idempotent(registry):
    value=transfer.begin(PEER,manifest())
    assert transfer.abort_receive(value['token'],PEER)['state']=='cancelled'
    assert not transfer.spool(value['token']).exists()
    assert transfer.abort_receive(value['token'],PEER)['state']=='cancelled'


@pytest.mark.parametrize('state',['importing','verifying','ready'])
def test_lost_response_abort_cannot_delete_a_materializing_or_completed_copy(registry,state):
    value=transfer.begin(PEER,manifest());data=transfer.read(value['token'])
    transfer.update(data,state=state)
    assert transfer.abort_receive(value['token'],PEER)['state']==state
    assert transfer.spool(value['token']).is_dir()


def test_peer_abort_cannot_target_someone_elses_upload(registry):
    value=transfer.begin(PEER,manifest())
    with pytest.raises(ValueError,match='not found'):
        transfer.abort_receive(value['token'],'d'*32)
    assert transfer.read(value['token'])['state']=='receiving'


def test_abort_transport_path_is_narrowly_allowed():
    assert peers.allowed_path('/api/peers/instance-transfers/'+JOB+'/abort')
    assert not peers.allowed_path('/api/web/instance-transfers/'+JOB+'/abort')


@pytest.fixture
def inbox(registry,monkeypatch):
    import pwd,os,types
    path=registry/'inbox';path.mkdir(mode=0o700)
    monkeypatch.setattr(transfer,'INBOX',path)
    monkeypatch.setattr(pwd,'getpwnam',lambda name:types.SimpleNamespace(pw_uid=os.geteuid()))
    return path


def test_real_chunk_write_and_retry_recover_an_unjournaled_tail(registry,inbox):
    import hashlib
    payload=b'copy payload';value=transfer.begin(PEER,manifest(size=len(payload),sha256=hashlib.sha256(payload).hexdigest()))
    capsule=transfer.spool(value['token'])/'capsule.tar';capsule.write_bytes(b'crash tail')
    chunk=inbox/(JOB+'.chunk');chunk.write_bytes(payload);chunk.chmod(0o600)
    result=transfer.receive_chunk(value['token'],PEER,JOB,0,hashlib.sha256(payload).hexdigest())
    assert result['offset']==len(payload) and capsule.read_bytes()==payload
    again=transfer.receive_chunk(value['token'],PEER,JOB,0,hashlib.sha256(payload).hexdigest())
    assert again['offset']==len(payload) and capsule.read_bytes()==payload
    chunk.write_bytes(b'evil payload');chunk.chmod(0o600)
    with pytest.raises(ValueError,match='Conflicting'):
        transfer.receive_chunk(value['token'],PEER,JOB,0,hashlib.sha256(b'evil payload').hexdigest())
    assert capsule.read_bytes()==payload


@pytest.mark.parametrize('link',['symbolic','hard'])
def test_chunk_cannot_read_through_a_symlink_or_host_hardlink(registry,inbox,link):
    import os,hashlib
    value=transfer.begin(PEER,manifest(size=4));outside=registry/'outside';outside.write_bytes(b'probe');outside.chmod(0o600)
    path=inbox/(JOB+'.chunk')
    if link=='symbolic':path.symlink_to(outside)
    else:os.link(outside,path)
    with pytest.raises((ValueError,OSError)):
        transfer.receive_chunk(value['token'],PEER,JOB,0,hashlib.sha256(b'probe').hexdigest())
    assert (transfer.spool(value['token'])/'capsule.tar').stat().st_size==0
    assert outside.read_bytes()==b'probe'


def test_inbox_directory_symlink_is_rejected(registry,inbox,monkeypatch):
    import hashlib
    value=transfer.begin(PEER,manifest(size=4));link=registry/'inbox-alias';link.symlink_to(inbox,target_is_directory=True)
    monkeypatch.setattr(transfer,'INBOX',link)
    with pytest.raises(OSError):transfer.receive_chunk(value['token'],PEER,JOB,0,hashlib.sha256(b'test').hexdigest())
    assert (transfer.spool(value['token'])/'capsule.tar').stat().st_size==0


@pytest.mark.parametrize('kind',['linux-record','orphan-marker','dangling-link'])
def test_destination_id_never_overwrites_saved_or_orphaned_metadata(tmp_path,monkeypatch,kind):
    code='97827701';pos=tmp_path/'pos';pos.mkdir();registry=tmp_path/'vm';registry.mkdir()
    monkeypatch.setattr(transfer.vm,'POS',pos);monkeypatch.setattr(transfer.vm,'ROOT',registry)
    monkeypatch.setattr(transfer.vm,'storage_root',lambda:tmp_path/'storage')
    import mykos_containers
    monkeypatch.setattr(mykos_containers,'all_containers',lambda:[])
    if kind=='linux-record':
        (pos/'.linux').mkdir();(pos/'.linux'/(code+'.json')).write_text('{"retained":true}')
    elif kind=='orphan-marker':(pos/('.'+code+'.container')).write_text('retained')
    else:(pos/('.'+code)).symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError,match='saved data'):transfer.available(code)


def test_pax_nul_path_cannot_confuse_gnu_tar_ancestor_checks():
    payload=io.BytesIO()
    with tarfile.open(fileobj=payload,mode='w',format=tarfile.PAX_FORMAT) as out:
        member=tarfile.TarInfo('placeholder');member.type=tarfile.SYMTYPE;member.linkname='/outside';member.pax_headers={'path':'link\x00padding'};out.addfile(member)
        child=tarfile.TarInfo('link/file');child.size=4;out.addfile(child,io.BytesIO(b'data'))
    payload.seek(0)
    with tarfile.open(fileobj=payload,mode='r:') as incoming,pytest.raises(ValueError,match='Unsafe archive path'):
        transfer.validated_members(incoming)


def test_unknown_token_does_not_create_a_journal_directory(registry):
    with pytest.raises(ValueError,match='not found'):
        transfer.abort_receive(JOB,PEER)
    assert not (transfer.STATE/JOB).exists()


def test_source_cleanup_preserves_shared_image_and_snapshot_aliases(registry,monkeypatch):
    transfer.folder(JOB).mkdir()
    owned='localhost/mykos-peer-copy/'+JOB+':transfer'
    originals={'localhost/example-os:latest','localhost/user-snapshot:keep'}
    tags=originals|{owned}
    data={'token':JOB,'owner':HASH,'created':1,'source_tag':owned,'source_tag_cleanup':True,'source_image_baseline':[HASH]}
    transfer.update(data)
    monkeypatch.setattr(transfer,'image_exists',lambda ref:ref in tags or ref.removeprefix('sha256:')==HASH)
    def cli(command,**kwargs):
        if command[1:3]==['image','inspect']:return json.dumps([{'Id':'sha256:'+HASH,'RepoTags':sorted(tags)}])
        if command[1]=='untag':
            # Podman's IMAGE-only form removes every alias, even when IMAGE
            # happens to be a tag. IMAGE NAME removes only the explicit NAME.
            if len(command)==3:tags.clear()
            else:tags.discard(command[3])
            return ''
        raise AssertionError('Unexpected image modification: '+str(command))
    monkeypatch.setattr(transfer.vm,'run',cli)
    transfer.cleanup_source_tag(data)
    assert tags==originals
    assert data['source_tag_cleanup'] is False


def test_cleanup_preserves_an_image_pinned_by_any_user_tag(monkeypatch):
    monkeypatch.setattr(transfer,'image_exists',lambda ref:True)
    calls=[]
    def cli(command,**kwargs):
        calls.append(command)
        assert command[1:3]==['image','inspect']
        return json.dumps([{'Id':HASH,'RepoTags':['localhost/user:keep']}])
    monkeypatch.setattr(transfer.vm,'run',cli)
    assert transfer.remove_unreferenced_image(HASH) is False
    assert len(calls)==1


def test_cleanup_preserves_an_untagged_image_referenced_by_a_stopped_container(monkeypatch):
    import mykos_containers
    monkeypatch.setattr(transfer,'image_exists',lambda ref:True)
    monkeypatch.setattr(transfer.vm,'run',lambda command,**kwargs:json.dumps([{'Id':HASH,'RepoTags':[]}]) if command[1:3]==['image','inspect'] else pytest.fail('Referenced image removal attempted'))
    monkeypatch.setattr(mykos_containers,'all_containers',lambda:[{'Image':'sha256:'+HASH,'State':{'Running':False}}])
    assert transfer.remove_unreferenced_image(HASH) is False


def test_vm_copy_isolation_rejects_a_bypassing_rule(monkeypatch):
    value={'name':'fixture','peer_copy':JOB}
    def cli(command,**kw):
        chain=command[-1]
        if chain=='OUTPUT':return '-P OUTPUT ACCEPT\n-A OUTPUT -j ACCEPT\n-A OUTPUT -j MYKOS_COPY_OUT'
        if chain=='FORWARD':return '-P FORWARD ACCEPT\n-A FORWARD -j DROP'
        return '-A MYKOS_COPY_OUT -o lo -j ACCEPT\n-A MYKOS_COPY_OUT -j REJECT'
    monkeypatch.setattr(transfer.vm,'run',cli)
    with pytest.raises(RuntimeError,match='replaced'):
        transfer.vm.verify_copy_isolation(value)


def test_vm_copy_isolation_requires_both_address_families(monkeypatch):
    families=[]
    def cli(command,**kw):
        family,chain=command[-3],command[-1];families.append(family)
        if family=='ip6tables':raise RuntimeError('IPv6 policy unavailable')
        if chain=='OUTPUT':return '-A OUTPUT -j MYKOS_COPY_OUT'
        if chain=='FORWARD':return '-A FORWARD -j DROP'
        return '-A MYKOS_COPY_OUT -o lo -j ACCEPT\n-A MYKOS_COPY_OUT -j REJECT'
    monkeypatch.setattr(transfer.vm,'run',cli)
    with pytest.raises(RuntimeError,match='IPv6 policy unavailable'):
        transfer.vm.verify_copy_isolation({'name':'fixture','peer_copy':JOB})
    assert 'iptables' in families and 'ip6tables' in families


def test_privileged_copy_dispatch_uses_the_app_environment(monkeypatch):
    import importlib.machinery,importlib.util,sys
    from types import SimpleNamespace
    loader=importlib.machinery.SourceFileLoader('copy_helper_boundary',str(Path(__file__).parents[1]/'services/helpers/mykos-container-helper'))
    spec=importlib.util.spec_from_loader(loader.name,loader);helper=importlib.util.module_from_spec(spec);loader.exec_module(helper)
    args=['instance-copy-plan','97827740','97827741',PEER]
    monkeypatch.setitem(sys.modules,'mykos_instance_transfer',None)
    def child(command,**kwargs):
        assert command==['/opt/mykos-web/.venv/bin/python','/usr/local/sbin/mykos-instance-transfer','dispatch']
        assert json.loads(kwargs['input'])==args and kwargs['timeout']==150
        return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'result':{'mode':'restart'}}))
    monkeypatch.setattr(helper.subprocess,'run',child)
    assert helper.dispatch(args)=={'mode':'restart'}


@pytest.mark.parametrize('stdout,returncode',[('not-json',0),(json.dumps({'ok':False,'error':'Peer unavailable'}),1)])
def test_privileged_copy_dispatch_rejects_failed_child(monkeypatch,stdout,returncode):
    import importlib.machinery,importlib.util
    from types import SimpleNamespace
    loader=importlib.machinery.SourceFileLoader('copy_helper_failure',str(Path(__file__).parents[1]/'services/helpers/mykos-container-helper'))
    spec=importlib.util.spec_from_loader(loader.name,loader);helper=importlib.util.module_from_spec(spec);loader.exec_module(helper)
    monkeypatch.setattr(helper.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=returncode,stdout=stdout))
    with pytest.raises(ValueError):helper.dispatch(['instance-copy-capabilities','97827741'])


@pytest.mark.parametrize('destination,source',[
    ('/run/mykos-legacy-systemd','/run/mykos-legacy-cgroups/systemd'),
    ('/usr/local/libexec/mykos-rh7-init','/usr/local/sbin/mykos-rh7-init'),
])
def test_only_readonly_exact_legacy_runtime_mounts_are_recreated(destination,source):
    state={'Config':{'Labels':{'io.mykos.os':'centos7'}}}
    mount={'Type':'bind','RW':False,'Source':source,'Destination':destination}
    assert transfer.legacy_runtime_mount(mount,state)
    for changed in ({'RW':True},{'Source':'/other'},{'Destination':'/other'},{'Type':'volume'}):
        assert not transfer.legacy_runtime_mount({**mount,**changed},state)
    assert not transfer.legacy_runtime_mount(mount,{'Config':{'Labels':{'io.mykos.os':'rocky9'}}})
