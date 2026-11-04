from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
import importlib.util,importlib.machinery,json,sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_linux as native,mykos_ubuntu_vpn as ubuntu

@pytest.mark.parametrize('operation',['start','stop'])
def test_native_vpn_controls_retain_config_and_only_target_ipsec(monkeypatch,tmp_path,operation):
 folder=tmp_path/'.01234567';folder.mkdir();cfg=folder/'office.conn.conf';cfg.write_text('conn office\n  auto=start\n');secret=folder/'office.secrets';secret.write_text('synthetic fixture')
 events=[];state={'Id':'same','State':{'Running':True}}
 monkeypatch.setattr(native.vm,'POS',tmp_path);monkeypatch.setattr(native.vm,'locked',nullcontext);monkeypatch.setattr(native,'inspect',lambda s:('01234567.host',state));monkeypatch.setattr(native,'vpn_preflight',lambda s:None);monkeypatch.setattr(native,'_prepare_vpn',lambda s:events.append('prepare'));monkeypatch.setattr(native,'run',lambda a,**kw:events.append(a) or '')
 assert native.vpn_change('01234567',operation)['configuration_retained']
 assert [native.PODMAN,'exec','01234567.host','systemctl',operation,'ipsec'] in events
 if operation=='start':assert events[-1]==[native.PODMAN,'exec','01234567.host','ipsec','auto','--asynchronous','--up','office']
 assert ('prepare' in events)==(operation=='start') and cfg.read_text()=='conn office\n  auto=start\n' and secret.read_text()=='synthetic fixture'
 with pytest.raises(ValueError):native.vpn_change('01234567','delete')


def test_ubuntu_default_subnet_is_shared_lan_and_secret_stays_stdin(monkeypatch,tmp_path):
 value={'os':'ubuntu22.04','name':'01234567.host','role':'instance','guest_agent':True};calls=[]
 monkeypatch.setattr(ubuntu,'selection',lambda s:value);monkeypatch.setattr(ubuntu.vm,'POS',tmp_path);monkeypatch.setattr(ubuntu.vm,'storage',lambda s:tmp_path)
 def execute(v,path,args=(),payload=None,timeout=30):calls.append((args,payload));return 'mykos-office'
 monkeypatch.setattr(ubuntu,'execute',execute)
 result=ubuntu.add('01234567','office','vpn.example','10.1.0.0/24','synthetic-private-value')
 payload=json.loads(calls[0][1]);assert payload[2]=='192.168.22.0/24' and len(calls)==1
 assert 'synthetic-private-value' not in (tmp_path/'.01234567/office.conn.conf').read_text()


def loader():
 path=Path(__file__).resolve().parents[1]/'services/helpers/mykos-container-helper';ld=importlib.machinery.SourceFileLoader('vpn_control_helper_test',str(path));sp=importlib.util.spec_from_loader(ld.name,ld);m=importlib.util.module_from_spec(sp);ld.exec_module(m);return m

@pytest.mark.parametrize('action',['start-vpn','stop-vpn'])
def test_vpn_action_queues_the_correct_worker(monkeypatch,tmp_path,action):
 m=loader();saved=[];commands=[];monkeypatch.setattr(m,'JOBS',tmp_path);monkeypatch.setattr(m,'active',lambda s:False);monkeypatch.setattr(m,'vpn_target',lambda s:'native');monkeypatch.setattr(m,'save_job',lambda s,v:saved.append(dict(v)));monkeypatch.setattr(m,'run',lambda a,**kw:commands.append(a) or '')
 result=m._dispatch([action,'01234567']);assert result['operation']==action and result['state']=='queued';assert commands==[['/usr/bin/systemctl','start','--no-block','mykos-stage@01234567.service']]


def test_vpn_route_requires_admin_and_same_origin_before_invocation(monkeypatch):
 import container_admin as api
 calls=[];allowed=[True]
 app=SimpleNamespace(check=lambda r:calls.append('check'),origin=lambda r:calls.append('origin'),mykos_terminal_permission=lambda r:{'allowed':allowed[0]})
 monkeypatch.setitem(sys.modules,'app',app);monkeypatch.setattr(api,'invoke',lambda *a:calls.append(a) or {'state':'queued'})
 assert api.container_vpn_control('01234567','stop',object())['state']=='queued';assert calls==['check','origin',('stop-vpn','01234567')]
 allowed[0]=False
 with pytest.raises(api.HTTPException) as e:api.container_vpn_control('01234567','start',object())
 assert e.value.status_code==403



def test_vpn_boot_wait_handles_early_systemd_states(monkeypatch):
 clock=[0];states=iter(['','starting','degraded']);monkeypatch.setattr(native.time,'monotonic',lambda:clock[0]);monkeypatch.setattr(native.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
 assert native.wait_boot('owned',lambda *a,**k:next(states))=='degraded' and clock[0]==1
