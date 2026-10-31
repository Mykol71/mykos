from pathlib import Path
import os,sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_criu as rpc
import mykos_criu_cgroups as cg

CID='a'*64
PATH='/machine.slice/libpod-'+CID+'.scope'
VALUE={'path':PATH,'controllers':['cpu','memory','pids']}

def string(n,value):return rpc.integer((n<<3)|2)+rpc.integer(len(value))+value

def request(action,root=None):
    options=b'\x10\x2a'
    if root is not None:options+=string(25,string(1,b'')+string(2,root))
    return b'\x08'+rpc.integer(action)+string(2,options)

def test_dump_collects_empty_groups_only_inside_its_unified_boundary():
    txn=cg.Transaction(VALUE,'capture');packet=request(1);rewritten=txn.request(packet)
    entries=[rpc.fields(v) for n,w,v,_ in rpc.wire_fields(rpc.fields(rewritten)[2]) if n==25]
    assert entries==[{1:b'',2:os.fsencode(PATH)}]
    assert txn.request(rewritten)==rewritten
    with pytest.raises(ValueError,match='root'):txn.request(request(1,b'/'))


def test_restore_requires_exact_runtime_boundary():
    txn=cg.Transaction(VALUE,'restore')
    version=b'\x08\x0a'
    assert txn.request(version)==version and not txn.started
    assert txn.request(request(2,os.fsencode(PATH)))==request(2,os.fsencode(PATH))
    assert txn.started
    implicit=txn.request(request(2))
    roots=[rpc.fields(v) for n,w,v,_ in rpc.wire_fields(rpc.fields(implicit)[2]) if n==25]
    assert roots==[{1:b'',2:os.fsencode(PATH)}]
    for packet in [request(2,b'/'),request(1)]:
        with pytest.raises(ValueError):txn.request(packet)


def test_stopped_container_restore_uses_saved_scope_without_reading_dead_pid(tmp_path,monkeypatch):
    (tmp_path/'cgroup.controllers').touch();monkeypatch.setattr(cg,'CGROUP',tmp_path)
    assert cg.restore_context({'Id':CID,'State':{'Pid':0}},{'cgroup':VALUE})==VALUE
    with pytest.raises(ValueError,match='fresh snapshot'):cg.restore_context({'Id':CID,'State':{'Pid':0}},{})
    with pytest.raises(ValueError,match='identity'):cg.restore_context({'Id':'b'*64},{'cgroup':VALUE})


def test_unrelated_process_rejects_before_any_cgroup_changes(tmp_path,monkeypatch):
    monkeypatch.setattr(cg,'CGROUP',tmp_path);root=tmp_path/PATH.lstrip('/');root.mkdir(parents=True)
    (root/'cgroup.procs').write_text('99999');(root/'cgroup.subtree_control').write_text('memory')
    txn=cg.Transaction(VALUE,'restore');txn.worker=42;monkeypatch.setattr(txn,'owned_process',lambda pid:False)
    notification={1:6,5:string(1,b'post-setup-namespaces')}
    with pytest.raises(ValueError,match='Unrelated'):txn.notification(notification)
    assert (root/'cgroup.procs').read_text()=='99999' and (root/'cgroup.subtree_control').read_text()=='memory'
    assert not list(root.glob('.mykos-criu-restore-*'))


def test_controllers_wait_until_namespace_callback_and_parent_empty(tmp_path,monkeypatch):
    monkeypatch.setattr(cg,'CGROUP',tmp_path);root=tmp_path/PATH.lstrip('/');root.mkdir(parents=True)
    (root/'cgroup.procs').write_text('42');(root/'cgroup.controllers').write_text('cpu memory pids');(root/'cgroup.subtree_control').write_text('')
    txn=cg.Transaction(VALUE,'restore');txn.worker=42;original=Path.write_text;events=[]
    def write(path,data,*args,**kwargs):
        events.append(path.name)
        if path.name=='cgroup.procs' and path.parent!=root:original(root/'cgroup.procs','')
        if path.name=='cgroup.subtree_control':assert not (root/'cgroup.procs').read_text()
        return original(path,data,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',write)
    txn.notification({1:6,5:string(1,b'setup-namespaces')});assert not events
    txn.notification({1:6,5:string(1,b'post-setup-namespaces')})
    assert txn.prepared and events.index('cgroup.procs')<events.index('cgroup.subtree_control')
    assert (root/'cgroup.subtree_control').read_text()=='+cpu +memory +pids'


def hierarchy(tmp_path,monkeypatch):
    monkeypatch.setattr(cg,'CGROUP',tmp_path)
    root=tmp_path/PATH.lstrip('/');child=root/'container';child.mkdir(parents=True)
    for node,enabled in ((root,'cpu memory pids'),(child,'memory')):
        (node/'cgroup.procs').write_text('');(node/'cgroup.subtree_control').write_text(enabled)
        (node/'cgroup.controllers').write_text('cpu memory pids')
    original=Path.write_text;events=[]
    def write(path,data,*args,**kwargs):
        events.append((path,data))
        if path.name=='cgroup.subtree_control':
            current=set(path.read_text().split())
            for item in data.split():
                if item.startswith('-'):current.discard(item[1:])
                else:current.add(item.lstrip('+'))
            return original(path,' '.join(sorted(current)))
        return original(path,data,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',write)
    txn=cg.Transaction(VALUE,'restore');txn.worker=42
    return txn,root,child,events


def test_crun_delegation_is_disabled_bottom_up_only_until_namespace_is_ready(tmp_path,monkeypatch):
    txn,root,child,events=hierarchy(tmp_path,monkeypatch)
    txn.notification({1:6,5:string(1,b'setup-namespaces')})
    assert events[0][0]==child/'cgroup.subtree_control' and events[1][0]==root/'cgroup.subtree_control'
    assert not cg.controllers(root) and not cg.controllers(child)
    txn.notification({1:6,5:string(1,b'post-setup-namespaces')})
    assert cg.controllers(root)==VALUE['controllers'] and cg.controllers(child)==['memory']
    assert events[2][0]==root/'cgroup.subtree_control' and events[3][0]==child/'cgroup.subtree_control'
    assert txn.prepared


def test_failed_namespace_setup_restores_delegation(tmp_path,monkeypatch):
    txn,root,child,events=hierarchy(tmp_path,monkeypatch)
    txn.notification({1:6,5:string(1,b'setup-namespaces')})
    txn.close()
    assert cg.controllers(root)==VALUE['controllers'] and cg.controllers(child)==['memory']
    txn.notification({1:2,2:0});assert not txn.succeeded


def test_unrelated_child_process_rejects_before_disabling_any_controller(tmp_path,monkeypatch):
    txn,root,child,events=hierarchy(tmp_path,monkeypatch)
    (child/'cgroup.procs').write_text('999999');events.clear()
    with pytest.raises(ValueError,match='Unrelated'):txn.notification({1:6,5:string(1,b'setup-namespaces')})
    assert not events and cg.controllers(root)==VALUE['controllers'] and cg.controllers(child)==['memory']


def test_success_response_is_tracked_separately_from_failed_restore(tmp_path,monkeypatch):
    txn,root,child,events=hierarchy(tmp_path,monkeypatch)
    txn.notification({1:2,2:0});assert not txn.succeeded
    txn.notification({1:2,2:1});assert txn.succeeded


@pytest.mark.parametrize('outside,inside,expected',[(PATH+'/init.scope','/init.scope',PATH),(PATH+'/container/init.scope','/init.scope',PATH+'/container'),(PATH+'/container','/',PATH+'/container')])
def test_actual_namespace_boundary_is_distinct_from_podman_resource_scope(outside,inside,expected):
    assert cg.namespace_boundary(outside,inside,PATH)==expected
    with pytest.raises(ValueError):cg.namespace_boundary('/other/init.scope',inside,PATH)


@pytest.mark.parametrize('action',[1,2])
def test_crun_rpc_relocates_to_saved_namespace_without_leaving_private_resource_scope(action):
    value=dict(VALUE,namespace_path=PATH+'/container',namespace_controllers=['memory'])
    txn=cg.Transaction(value,'capture' if action==1 else 'restore')
    packet=txn.request(request(action,os.fsencode(PATH)))
    roots=[rpc.fields(v) for n,w,v,_ in rpc.wire_fields(rpc.fields(packet)[2]) if n==25]
    assert roots==[{1:b'',2:os.fsencode(PATH+'/container')}]
    assert txn.request(packet)==packet
    with pytest.raises(ValueError,match='namespace'):cg.Transaction(dict(value,namespace_path='/other'),txn.operation)


def test_namespace_controllers_are_restored_even_when_runtime_precreated_empty_root(tmp_path,monkeypatch):
    txn,root,child,events=hierarchy(tmp_path,monkeypatch)
    (child/'cgroup.subtree_control').write_text('-memory');events.clear()
    txn=cg.Transaction(dict(VALUE,namespace_path=PATH+'/container',namespace_controllers=['memory']),'restore');txn.worker=42
    txn.notification({1:6,5:string(1,b'setup-namespaces')})
    txn.notification({1:6,5:string(1,b'post-setup-namespaces')})
    assert cg.controllers(root)==VALUE['controllers'] and cg.controllers(child)==['memory']


def test_captured_namespace_is_measured_with_host_tools_and_no_guest_execution(tmp_path,monkeypatch):
    from types import SimpleNamespace
    txn,root,child,events=hierarchy(tmp_path,monkeypatch)
    (tmp_path/'cgroup.controllers').touch()
    proc=tmp_path/'proc';(proc/'123').mkdir(parents=True)
    (proc/'123/cgroup').write_text('0::'+PATH+'/container/init.scope\n')
    monkeypatch.setattr(cg,'PROC',proc);calls=[]
    def run(args,**kwargs):calls.append(args);return SimpleNamespace(stdout='0::/init.scope\n')
    monkeypatch.setattr(cg.subprocess,'run',run)
    value=cg.capture_context({'Id':CID,'State':{'Pid':123,'CgroupPath':PATH}})
    assert value['namespace_path']==PATH+'/container' and value['namespace_controllers']==['memory']
    assert calls[0][0]=='/usr/bin/nsenter' and '/usr/bin/cat' in calls[0]


def test_legacy_named_root_is_bound_to_container_and_overrides_global_default():
    value={**VALUE,'namespace_path':PATH+'/container','namespace_controllers':[],'legacy_systemd_path':'/mykos/'+CID}
    txn=cg.Transaction(value,'restore')
    global_root=string(25,string(2,os.fsencode(PATH)))
    packet=b'\x08\x02'+string(2,b'\x10\x2a'+global_root)
    rewritten=txn.request(packet)
    roots=[rpc.fields(v) for n,w,v,_ in rpc.wire_fields(rpc.fields(rewritten)[2]) if n==25]
    assert roots==[{1:b'',2:os.fsencode(PATH+'/container')},{1:b'name=systemd',2:os.fsencode('/mykos/'+CID)}]
    assert txn.request(rewritten)==rewritten
    with pytest.raises(ValueError,match='identity'):
        cg.Transaction({**value,'legacy_systemd_path':'/'},'restore')


def test_nested_delegation_recreates_memory_event_files_before_tasks_resume(tmp_path,monkeypatch):
    _,root,child,events=hierarchy(tmp_path,monkeypatch)
    slice=child/'system.slice';leaf=slice/'systemd-journald.service';leaf.mkdir(parents=True)
    for node in (slice,leaf):
        (node/'cgroup.procs').touch();(node/'cgroup.subtree_control').touch();(node/'cgroup.controllers').write_text('cpu memory pids')
    event=leaf/'memory.events';original=Path.write_text
    def write(path,data,*args,**kwargs):
        value=original(path,data,*args,**kwargs)
        if path==slice/'cgroup.subtree_control' and '+memory' in data:event.write_text('oom 0\n')
        return value
    monkeypatch.setattr(Path,'write_text',write)
    value={**VALUE,'namespace_path':PATH+'/container','namespace_controllers':['memory'],'delegation':{'container/system.slice':['memory']}}
    txn=cg.Transaction(value,'restore');txn.worker=42
    txn.notification({1:6,5:string(1,b'setup-namespaces')});assert not event.exists()
    txn.notification({1:6,5:string(1,b'post-setup-namespaces')});assert event.read_text()=='oom 0\n'


@pytest.mark.parametrize('relative',['/system.slice','../other','container/../../other','container//system.slice'])
def test_saved_delegation_cannot_escape_container(relative):
    with pytest.raises(ValueError,match='escaped'):
        cg.Transaction({**VALUE,'delegation':{relative:['memory']}},'restore')


def test_vendor_systemd_escaped_unit_names_are_valid_linux_cgroup_components():
    value={**VALUE,'delegation':{r'container/system.slice/system-sshd\x2dkeygen.slice':['memory']}}
    txn=cg.Transaction(value,'capture')
    assert len(txn.delegation)==1
