"""Fault injection for review fixes; temporary files and mocked guests only."""
import asyncio,base64,contextlib,importlib.machinery,importlib.util,json,os,shutil,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_snapshots import environment,seed_vm,seed_snapshot
import mykos_vm as vm,mykos_snapshots as snaps,mykos_runtime as runtime,mykos_linux as linux,mykos_commands as commands
ROOT=Path(__file__).resolve().parents[1]

def module(name,path):
    loader=importlib.machinery.SourceFileLoader(name,str(path));spec=importlib.util.spec_from_loader(name,loader);value=importlib.util.module_from_spec(spec);loader.exec_module(value);return value

@pytest.mark.parametrize('framed',[False,True])
@pytest.mark.parametrize('payload',['A'*5000+'\r','🌻'*3000+'\r'])
def test_large_terminal_input_preserved(monkeypatch,framed,payload):
    import app
    written=[];packets=[{'type':'websocket.receive','text':payload},{'type':'websocket.disconnect'}]
    class WS:
        query_params={'geometry':'1'} if framed else {}
        async def accept(self):pass
        async def receive(self):return packets.pop(0)
        async def send_bytes(self,v):pass
        async def send_text(self,v):pass
        async def close(self,**kw):pass
    class Reader:
        async def readline(self):return b'MYKOS_TERMINAL_READY\n'
        async def read(self,n):await asyncio.sleep(20)
    class Writer:
        def write(self,v):written.append(v)
        async def drain(self):pass
        def close(self):pass
        async def wait_closed(self):pass
    async def connection(*a):return Reader(),Writer()
    monkeypatch.setattr(app,'mykos_terminal_authority',lambda *a:None);monkeypatch.setattr(asyncio,'open_unix_connection',connection)
    asyncio.run(app.mykos_terminal_bridge(WS(),'host'))
    if framed:
        helper=module('review_terminal_parser',ROOT/'services/helpers/mykos-terminal-helper')
        decoded=helper.TerminalFrames().feed(b''.join(written[1:]))
        assert all(kind==b'I' and len(data)<=4096 for kind,data in decoded)
        output=b''.join(data for _,data in decoded)
    else:output=b''.join(written[1:])
    assert output==payload.encode()

@pytest.mark.parametrize('exception',[KeyboardInterrupt,SystemExit,InterruptedError])
def test_vm_restore_unwinds_interruption(environment,monkeypatch,exception):
    value=seed_vm();identifier,_=seed_snapshot(value);running={'v':True}
    def run(args,**kw):
        if args[1] in ('stop','kill'):running['v']=False
        if args[1]=='start':running['v']=True
        return ''
    monkeypatch.setattr(vm,'run',run);monkeypatch.setattr(vm,'inspect',lambda n:{'Id':'original','State':{'Running':running['v']},'Config':{'Labels':{'io.mykos.vm.os':'mac15'}}})
    monkeypatch.setattr(runtime,'require_container_capacity',lambda *a:None);monkeypatch.setattr(vm,'restore_manifest',lambda *a:None);monkeypatch.setattr(vm,'tree_copy',shutil.copytree)
    monkeypatch.setattr(vm,'launch',lambda *a:(_ for _ in ()).throw(exception('interrupted')))
    with pytest.raises(exception):snaps.restore_vm('12345678',identifier)
    assert running['v'] and vm.read('12345678')==value
    assert (vm.storage('12345678')/'storage/disk').read_text()=='current'
    assert not list(vm.storage('12345678').glob('.restore-*'))


def test_hard_exit_recovery_on_next_lifecycle_lock(environment,monkeypatch):
    base,_=environment
    # Child dies inside the real restore after the original rename/disk swap.
    code='''import sys,os,json,shutil
from pathlib import Path
sys.path[:0]=[ROOT+'/services/helpers',ROOT+'/tests']
import mykos_vm as vm,mykos_snapshots as s,mykos_runtime as rt
from test_snapshots import seed_vm,seed_snapshot
base=Path(BASE);vm.ROOT=base/'pos/.vm';vm.POS=base/'pos';vm.storage_root=lambda:base/'disks';s.target=lambda code:code+'.test'
value=seed_vm();identifier,_=seed_snapshot(value)
state={'12345678.test':{'Id':'original','State':{'Running':True},'Config':{'Labels':{'io.mykos.vm.os':'mac15','io.mykos.vm.role':'instance'}}}}
def save(): (base/'podman.json').write_text(json.dumps(state))
def run(a,**kw):
 if a[1]=='rename':state[a[3]]=state.pop(a[2])
 if a[1]=='stop':state[a[-1]]['State']['Running']=False
 save();return ''
vm.run=run;vm.inspect=lambda name:state[name];vm.stop_for_restore=lambda v,d:run(['podman','stop',v['name']]);vm.restore_manifest=lambda *a:None;vm.tree_copy=shutil.copytree;rt.require_container_capacity=lambda *a:None
vm.launch=lambda *a:os._exit(23)
s.restore_vm('12345678',identifier)
'''
    child=subprocess.run([sys.executable,'-c','ROOT='+repr(str(ROOT))+'\nBASE='+repr(str(base))+'\n'+code],capture_output=True,text=True)
    assert child.returncode==23,child.stderr
    assert (vm.storage('12345678')/'storage/disk').read_text()=='snapshot'
    states=json.loads((base/'podman.json').read_text())
    def inspect(name):
        if name not in states:raise RuntimeError('not found')
        return states[name]
    def run(a,**kw):
        if a[1]=='rename':states[a[3]]=states.pop(a[2])
        elif a[1]=='start':states[a[2]]['State']['Running']=True
        elif a[1]=='rm':states.pop(a[2])
        return ''
    monkeypatch.setattr(vm,'inspect',inspect);monkeypatch.setattr(vm,'run',run)
    with vm.locked():assert (vm.storage('12345678')/'storage/disk').read_text()=='current'
    assert list(states)==['12345678.test'] and states['12345678.test']['State']['Running']
    assert not list(vm.storage('12345678').glob('.restore-*'))


def test_native_recovery_restores_backups_and_checks_identity(environment,monkeypatch):
    base,_=environment;upper=base/'upper';upper.mkdir();(upper/'data').write_text('snapshot')
    transaction=snaps.root()/'.native-restore-test';transaction.mkdir(parents=True);backup=transaction/'data-0';backup.mkdir();(backup/'data').write_text('original')
    state={'Id':'original','State':{'Running':False},'HostConfig':{'AutoRemove':False},'GraphDriver':{'Data':{'UpperDir':str(upper)}}}
    data={'kind':'native','shop':'12345678','name':'12345678.test','original_id':'original','running':True,'changed':True,'phase':'prepared','backed':[{'backup':'data-0','destination':str(upper)}]}
    snaps.recovery_record(transaction,data)
    monkeypatch.setattr(linux,'inspect',lambda code:('12345678.test',state));monkeypatch.setattr(snaps,'native_data_mounts',lambda state:[])
    import mykos_containers
    monkeypatch.setattr(mykos_containers,'all_containers',lambda:[])
    monkeypatch.setattr(snaps,'sync_recovery',lambda a,b:shutil.copytree(a,b,dirs_exist_ok=True))
    monkeypatch.setattr(vm,'run',lambda a,**kw:state['State'].update(Running=True) if a[1]=='start' else '')
    state['Id']='different'
    with pytest.raises(RuntimeError,match='identity changed'):snaps.recover_locked()
    assert (upper/'data').read_text()=='snapshot' and backup.exists()
    state['Id']='original';snaps.recover_locked()
    assert (upper/'data').read_text()=='original' and state['State']['Running'] and not transaction.exists()


def test_partial_snapshot_delete_remains_visible_and_retryable(environment,monkeypatch):
    value=seed_vm();identifier,folder=seed_snapshot(value);p=folder/'metadata.json';data=json.loads(p.read_text());data['image_ref']='localhost/mykos-snapshots/12345678:'+identifier;p.write_text(json.dumps(data));p.chmod(0o600)
    real=shutil.rmtree
    def fail(path):
        (path/'metadata.json').unlink()
        raise OSError('injected partial delete')
    monkeypatch.setattr(snaps.shutil,'rmtree',fail)
    with pytest.raises(RuntimeError,match='retry Delete'):snaps.delete_snapshot('12345678',identifier)
    pending=snaps.list_snapshots('12345678');assert len(pending)==1 and pending[0]['id']==identifier and pending[0]['deletion_pending']
    with pytest.raises(ValueError,match='deletion pending'):snaps.read_snapshot('12345678',identifier)
    monkeypatch.setattr(snaps.shutil,'rmtree',real)
    assert snaps.delete_snapshot('12345678',identifier)['state']=='deleted'
    assert not snaps.list_snapshots('12345678') and not list(folder.parent.iterdir())
    assert (vm.storage('12345678')/'storage/disk').read_text()=='current'


def test_same_attributes_do_not_allow_immutable_url_change(tmp_path):
    sync=module('review_repo_sync276',ROOT/'repo/bin/sync_repos.py');live=tmp_path/'live';stage=tmp_path/'stage';live.mkdir();stage.mkdir()
    old=live/'same.rpm';new=stage/'same.rpm';old.write_bytes(b'OLD');new.write_bytes(b'NEW');stamp=old.stat().st_mtime_ns;os.utime(new,ns=(stamp,stamp))
    with pytest.raises(ValueError,match='immutable URL'):sync.preserve_previous(live,stage)
    assert old.read_bytes()==b'OLD'


def test_legacy_package_uses_exact_hashed_bytes(tmp_path,monkeypatch):
    legacy=module('review_legacy276',ROOT/'bin/install-bundle');app=tmp_path/'app';app.mkdir();(app/'app.py').write_text('# fixture');data=app/'status.json';data.write_text('before');units=tmp_path/'units';units.mkdir();unit=units/'mykos-review.service';unit.write_text('[Service]\n')
    def run(*a,**kw):data.write_text('after');return SimpleNamespace(stdout='',returncode=0)
    monkeypatch.setattr(legacy,'APP',app);monkeypatch.setattr(legacy,'UNIT_DIR',units);monkeypatch.setattr(legacy,'candidates',lambda *a:[data,unit]);monkeypatch.setattr(legacy,'allowed',lambda p:Path(p).is_relative_to(tmp_path));monkeypatch.setattr(legacy,'run',run)
    output=tmp_path/'bundle.tar.gz';legacy.package(output,True);legacy.read_bundle(output)
    assert data.read_text()=='after'


def test_guest_transport_loss_blocks_target_until_exit(tmp_path,monkeypatch):
    monkeypatch.setattr(commands,'ROOT',tmp_path/'queue')
    import mykos_refresh
    monkeypatch.setattr(mykos_refresh,'best_effort',lambda *a:None)
    record=tmp_path/'vm';record.write_text('{}');monkeypatch.setattr(vm,'record_path',lambda code:record);monkeypatch.setattr(vm,'read',lambda code:{'os':'win11','name':'12345678.test'})
    running={'v':True};calls=[]
    class Agent:
        identity='original'
        def __init__(self,*a,**k):pass
        def call(self,name,args=None):
            calls.append(name)
            if name=='guest-exec':return {'pid':123}
            if running['v']:raise commands.QueueError('injected QGA disconnect')
            return {'exited':True,'exitcode':0,'out-data':base64.b64encode(b'done').decode()}
        def close(self):pass
    monkeypatch.setattr(commands,'GuestAgent',Agent);monkeypatch.setattr(commands,'service_active',lambda key:False)
    for key in ('one','two'):commands.dispatch(['command-put',key,json.dumps({'target':'12345678','command':'Get-Date'})])
    value=commands.read('one');value['state']='starting';commands.save(value);commands.work('one')
    assert commands.read('one')['state']=='unknown' and commands.read('one')['guest_pid']==123
    monkeypatch.setattr(commands.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    commands.collect();assert commands.read('two')['state']=='pending' and calls.count('guest-exec')==1
    running['v']=False;commands.collect()
    assert commands.read('one')['state']=='succeeded' and commands.read('one')['reconciled']
    assert commands.read('two')['state']=='starting'
