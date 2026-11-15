"""Confined EL guest readiness uses only the read-only agent protocol."""
from types import SimpleNamespace
import pytest
from mykos_profiles import el_vm

@pytest.mark.parametrize('failure',[None,'vendor','root','network'])
def test_confined_agent_readiness_closes_channel_and_rejects_wrong_state(monkeypatch,failure):
    import mykos_commands,mykos_vm,mykos_ubuntu_vpn
    el=el_vm();calls=[];closed=[];ticks=[0]
    def monotonic():ticks[0]+=1;return ticks[0]
    monkeypatch.setattr(el.time,'monotonic',monotonic);monkeypatch.setattr(el.time,'sleep',lambda _:None)
    monkeypatch.setattr(mykos_vm,'inspect',lambda name:{'State':{'Running':True}})
    monkeypatch.setattr(mykos_ubuntu_vpn,'_execute',lambda *a,**k:pytest.fail('Guest shell executed'))
    data={'guest-get-osinfo':{'id':'centos' if failure=='vendor' else 'almalinux','version-id':'10.2'},'guest-get-fsinfo':[{'mountpoint':'/','type':'xfs' if failure=='root' else 'ext4'}],'guest-network-get-interfaces':[{'name':'lo' if failure=='network' else 'eth0','ip-addresses':[{'ip-address-type':'ipv4','ip-address':'127.0.0.1' if failure=='network' else '10.0.2.15'}]}]}
    class Agent:
        def __init__(self,value,**kw):assert kw=={'allow_image_builder':True,'timeout':3}
        def call(self,command,args):calls.append(command);assert args=={};return data[command]
        def close(self):closed.append(True)
    monkeypatch.setattr(mykos_commands,'GuestAgent',Agent)
    value={'os':'alma10vm','name':'owned','role':'template'}
    if failure:
        with pytest.raises(RuntimeError,match={'vendor':'distribution','root':'ext4','network':'IPv4'}[failure]):el.wait_ready(value,timeout=4)
    else:el.wait_ready(value,timeout=4)
    assert closed and set(calls)<=set(data)
