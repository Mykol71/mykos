import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/helpers'))
sys.path.insert(0,str(ROOT/'services/web'))
import mykos_refresh as refresh


def test_refresh_is_targeted_prioritized_and_bounded():
    calls=[]
    refresh.refresh('12345678',run=lambda args,**kw:calls.append((args,kw)))
    assert len(calls)==3
    assert all(args[:3]==['/usr/bin/nice','-n','-5'] and args[-1]=='12345678' and kw['timeout']==45 for args,kw in calls)
    calls.clear()
    refresh.refresh('host',run=lambda args,**kw:calls.append(args))
    assert len(calls)==1 and calls[0][-2:]==['/opt/mykos-web/collect-nodes.py','host']
    with pytest.raises(ValueError):refresh.refresh('../all',run=lambda *a,**k:pytest.fail('unsafe execution'))


def test_priority_restores_previous_value_on_failure(monkeypatch):
    calls=[]
    monkeypatch.setattr(refresh.os,'geteuid',lambda:0)
    monkeypatch.setattr(refresh.os,'getpriority',lambda *args:7)
    monkeypatch.setattr(refresh.os,'setpriority',lambda *args:calls.append(args[-1]))
    with pytest.raises(RuntimeError):
        with refresh.priority():raise RuntimeError('action error')
    assert calls==[-5,7]


def test_refresh_failure_does_not_misreport_successful_action():
    def failed(*a,**kw):raise OSError('collector unavailable')
    value=refresh.best_effort('12345678',{'ok':True},run=failed)
    assert value['ok'] and 'deferred' in value['refresh_warning']


@pytest.mark.parametrize('target',['host','12345678'])
def test_targeted_collection_preserves_unrelated_data_and_removes_deleted_id(tmp_path,monkeypatch,target):
    spec=importlib.util.spec_from_file_location('action_nodes',ROOT/'services/web/collect-nodes.py')
    c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
    inventory=tmp_path/'inventory.json';status=tmp_path/'status.json'
    inventory.write_text(json.dumps({'nodes':[{'id':'12345678','name':'first'},{'id':'87654321','name':'second'}]}))
    old={'observations':{'12345678':{'status':'online'},'87654321':{'status':'offline'}},'vpn_samples':{'12345678.host':{'vpn':{'status':'up'}},'87654321.host':{'vpn':{'status':'down'}}},'host_vpn_status':'down','host_certificate_domain':'old.example'}
    status.write_text(json.dumps(old));monkeypatch.setattr(c,'INVENTORY',inventory);monkeypatch.setattr(c,'STATUS',status)
    monkeypatch.setattr(c,'atomic_json',lambda p,v:p.write_text(json.dumps(v)))
    monkeypatch.setattr(c,'graph_totals',lambda:{'containers':1,'vpns':0})
    def containers(code=None):
        assert target!='host' and code==target
        return []
    monkeypatch.setattr(c.host,'containers',containers)
    def vpn(*args):
        assert target=='host' and not args
        return {'status':'up'}
    monkeypatch.setattr(c.host,'query_vpn',vpn)
    monkeypatch.setattr(c.host,'apache_certificate_domain',lambda:'new.example' if target=='host' else pytest.fail('unrelated host probe'))
    c.collect(target);saved=json.loads(status.read_text());nodes=json.loads(inventory.read_text())['nodes']
    assert saved['observations']['87654321']==old['observations']['87654321']
    assert saved['vpn_samples']['87654321.host']==old['vpn_samples']['87654321.host']
    if target=='host':
        assert len(nodes)==2 and saved['host_vpn_status']=='up'
        assert saved['observations']==old['observations']
    else:
        assert [n['id'] for n in nodes]==['87654321']
        assert '12345678' not in saved['observations'] and '12345678.host' not in saved['vpn_samples']
        assert saved['host_vpn_status']=='down' and saved['host_certificate_domain']=='old.example'
