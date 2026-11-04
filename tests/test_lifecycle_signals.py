from pathlib import Path
import importlib.util,sys,json,types
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'services/helpers'))
import mykos_refresh as signals,mykos_vm as vm,mykos_host_monitoring as host

@pytest.mark.parametrize('operation,expected',[
 ('start',[('container','up'),('vpn','up')]),('start-vpn',[('vpn','up')]),('stop',[('container','down')]),('stop-vpn',[('vpn','down')]),('delete',[('container','deleted'),('vpn','deleted')]),('restore',[('container','up'),('vpn','up')]),(None,[('container','up'),('vpn','up')])])
def test_lifecycle_only_sends_state_and_never_collects_stats(monkeypatch,operation,expected):
 calls=[];monkeypatch.setattr(signals,'notify',lambda shop,kind,state:calls.append((kind,state)));monkeypatch.setattr(signals,'vpn_up',lambda shop,**kw:calls.append(('vpn','up')))
 monkeypatch.setattr(signals,'refresh',lambda *a,**k:pytest.fail('Statistics must not be collected'))
 assert signals.lifecycle('01234567',operation)
 assert calls==expected


def test_vpn_only_signals_after_real_up_and_aborts_if_container_replaced(monkeypatch,tmp_path):
 shop='01234567';folder=tmp_path/('.'+shop);folder.mkdir();(folder/'office.conn.conf').write_text('synthetic')
 import mykos_runtime
 monkeypatch.setattr(mykos_runtime,'target',lambda shop:shop+'.fixture')
 monkeypatch.setattr(vm,'POS',tmp_path);clock=[0];import time
 monkeypatch.setattr(time,'monotonic',lambda:clock[0]);monkeypatch.setattr(time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
 states=iter(['down','up']);calls=[];monkeypatch.setattr(host,'query_vpn',lambda info:{'status':next(states)});monkeypatch.setattr(signals,'notify',lambda *a:calls.append(a))
 info={'Id':'original','State':{'Running':True,'Pid':100}}
 assert signals.vpn_up(shop,run=lambda *a,**kw:json.dumps([info]))
 assert calls==[(shop,'vpn','up')] and clock[0]==2
 calls.clear();states=iter(['down']);sequence=iter([info,{**info,'Id':'replacement'}])
 assert signals.vpn_up(shop,run=lambda *a,**kw:json.dumps([next(sequence)])) is False
 assert calls==[]


def test_invalid_payload_is_rejected():
 for args in [('bad','container','up'),('01234567','password','up'),('01234567','vpn','details')]:
  with pytest.raises(ValueError):signals.notify(*args)


def test_signal_route_is_authenticated_before_stream(monkeypatch):
 sys.path.insert(0,str(ROOT/'services/web'));import container_admin as api
 monkeypatch.setattr(api,'authorized',lambda request:(_ for _ in ()).throw(PermissionError('No session')))
 import asyncio
 with pytest.raises(PermissionError):asyncio.run(api.startup_events(object()))


def test_stopping_configured_container_also_signals_vpn_down(monkeypatch,tmp_path):
 folder=tmp_path/'.01234567';folder.mkdir();(folder/'office.conn.conf').write_text('synthetic')
 monkeypatch.setattr(vm,'POS',tmp_path);calls=[];monkeypatch.setattr(signals,'notify',lambda *args:calls.append(args))
 assert signals.lifecycle('01234567','stop')
 assert calls==[('01234567','container','down'),('01234567','vpn','down')]
