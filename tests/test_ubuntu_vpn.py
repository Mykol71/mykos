import base64,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_ubuntu_vpn as vpn


def instance(monkeypatch,tmp_path,agent=True):
    value={'os':'ubuntu22.04','name':'12345678.host','role':'instance','guest_agent':agent,'ready':True}
    monkeypatch.setattr(vpn.vm,'read',lambda shop:value)
    monkeypatch.setattr(vpn.vm,'storage',lambda shop:tmp_path)
    monkeypatch.setattr(vpn.vm,'POS',tmp_path)
    return value


def test_add_uses_private_stdin_and_no_secret_in_host_marker(monkeypatch,tmp_path):
    value=instance(monkeypatch,tmp_path)
    calls=[]
    def execute(v,path,args=(),payload=None,timeout=30):
        calls.append((path,args,payload))
        return 'mykos-office'
    monkeypatch.setattr(vpn,'execute',execute)
    result=vpn.add('12345678','office','vpn.example.com','10.10.10.0/24','my-private-psk','192.168.22.1/32')
    marker=(tmp_path/'.12345678/office.conn.conf').read_text()
    assert 'my-private-psk' not in marker+repr(result)+repr(calls[0][:2])
    assert json.loads(calls[0][2])[-1]=='my-private-psk'
    assert 'save_connection' in calls[0][1][1]
    with pytest.raises(ValueError,match='already exists'):
        vpn.add('12345678','office','vpn.example.com','10.10.10.0/24','psk','192.168.22.1/32')


def test_legacy_guest_with_no_vpn_is_not_configured_without_guest_probe(monkeypatch,tmp_path):
    instance(monkeypatch,tmp_path,False)
    monkeypatch.setattr(vpn,'execute',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('probe')))
    info={'Name':'12345678.host','State':{'Running':True}}
    assert vpn.status(info)['status']=='not_configured'
    with pytest.raises(ValueError,match='guest-agent-enable'):vpn.selection('12345678')


def test_status_is_cached_by_container_identity(monkeypatch,tmp_path):
    instance(monkeypatch,tmp_path)
    calls=[]
    monkeypatch.setattr(vpn,'execute',lambda *a,**kw:calls.append(a) or '000 Total IPsec connections: loaded 1, active 1\n000 IPsec SAs: total(1)')
    info={'Name':'12345678.host','Id':'one','State':{'Running':True,'StartedAt':'first'}}
    assert vpn.status(info)['status']=='up'
    assert vpn.status(info)['status']=='up'
    assert len(calls)==1
    info['Id']='two'
    vpn.status(info)
    assert len(calls)==2


def test_agent_command_receives_payload_as_stdin(monkeypatch,tmp_path):
    value=instance(monkeypatch,tmp_path)
    calls=[]
    class Agent:
        def __init__(self,value,timeout):pass
        def call(self,action,args):
            calls.append((action,args))
            if action=='guest-exec':return {'pid':1}
            return {'exited':True,'exitcode':0,'out-data':base64.b64encode(b'configured').decode()}
        def close(self):pass
    monkeypatch.setattr(vpn,'GuestAgent',Agent)
    assert vpn.execute(value,'/usr/bin/python3',('-c','trusted script'),b'private input')=='configured'
    assert calls[0][1]['input-data']==base64.b64encode(b'private input').decode()
    assert 'private input' not in str(calls[0][1]['arg'])
