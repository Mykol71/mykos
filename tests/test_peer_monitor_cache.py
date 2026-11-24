"""Peer monitoring caching: freshness, concurrent reads and authorization boundaries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
import peers_api
from peer_monitor_cache import PeerMonitoringCache, PeerCacheBusy

PEER = dict(id='a'*32, node='b'*32, ip='192.0.2.1', local_ip='192.0.2.2',
            origin='https://peer.example', certificate='cert-one', outgoing='private-test-key')
PATH = '/api/web/host-metrics'
VALUE = 200, 'application/json', b'{"collected_at":123,"cpu_percent":12}'


@pytest.fixture
def cache():
    clock = [0.]
    c = PeerMonitoringCache(clock=lambda: clock[0])
    yield c, clock
    c.close()


def test_fresh_hit_original_timestamp_and_actor_trust_isolation(cache):
    c, clock = cache
    calls = []
    def fetch():
        calls.append(1)
        return VALUE
    assert c.get(PEER, 'actor-one', PATH, fetch)[3]['state'] == 'miss'
    clock[0] = .4
    result = c.get(PEER, 'actor-one', PATH, fetch)
    assert result[:3] == VALUE and result[3] == {'state':'hit', 'age_seconds':.4}
    c.get(PEER, 'actor-two', PATH, fetch)
    for field in ('certificate', 'outgoing', 'node', 'ip', 'origin'):
        c.get(dict(PEER, **{field:'changed'}), 'actor-one', PATH, fetch)
    assert len(calls) == 7


def test_concurrent_cold_requests_share_one_fetch(cache):
    c, clock = cache
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch():
        calls.append(1); entered.set()
        assert release.wait(3)
        return VALUE
    with ThreadPoolExecutor(max_workers=8) as callers:
        futures = [callers.submit(c.get, PEER, 'actor', PATH, fetch) for _ in range(8)]
        assert entered.wait(2)
        time.sleep(.05);release.set()
        results = [f.result(3) for f in futures]
    assert len(calls) == 1 and all(r[:3] == VALUE for r in results)


def test_short_old_sample_returns_while_refresh_runs_then_new_sample(cache):
    c, clock = cache
    c.get(PEER, 'actor', PATH, lambda:VALUE)
    entered, release = threading.Event(), threading.Event()
    updated = 200, 'application/json', b'{"collected_at":124,"cpu_percent":18}'
    def refresh():
        entered.set();assert release.wait(3)
        return updated
    clock[0] = .8
    value = c.get(PEER, 'actor', PATH, refresh)
    assert value[:3] == VALUE and value[3]['state'] == 'refreshing'
    assert entered.wait(2)
    release.set()
    c.close()
    clock[0] = .9
    assert c.get(PEER, 'actor', PATH, lambda:updated)[:3] == updated


def test_expired_sample_never_served_and_refresh_failure_drops_old(cache):
    c, clock = cache
    c.get(PEER, 'actor', PATH, lambda:VALUE)
    clock[0] = 1.5
    def fail():
        raise HTTPException(502, 'Peer certificate expired')
    with pytest.raises(HTTPException):c.get(PEER, 'actor', PATH, fail)
    assert not c._entries
    assert c.get(PEER, 'actor', PATH, lambda:VALUE)[3]['state'] == 'miss'


@pytest.mark.parametrize('value', [(403,'application/json',b'{"detail":"Denied"}'),
    (502,'application/json',b'{"detail":"Offline"}'),(200,'text/html',b'<html>login</html>'),
    (200,'application/json',b'not json')])
def test_errors_HTML_invalid_JSON_not_cached(cache, value):
    c, clock = cache;calls=[]
    def fetch():calls.append(1);return value
    c.get(PEER,'actor',PATH,fetch);c.get(PEER,'actor',PATH,fetch)
    assert len(calls)==2 and not c._entries


def test_bounded_entries_bytes_and_inflight():
    c=PeerMonitoringCache(max_entries=2,max_bytes=100,max_entry_bytes=50,max_inflight=1)
    try:
        for i in range(8):c.get(PEER,str(i),PATH,lambda:VALUE)
        assert len(c._entries)<=2 and c._bytes<=100
        entered,release=threading.Event(),threading.Event()
        def slow():entered.set();assert release.wait(3);return VALUE
        with ThreadPoolExecutor(max_workers=1) as callers:
            f=callers.submit(c.get,PEER,'slow',PATH,slow);assert entered.wait(2)
            try:
                with pytest.raises(PeerCacheBusy):c.get(PEER,'other',PATH,lambda:VALUE)
            finally:release.set()
            f.result(3)
    finally:c.close()


def test_control_invalidation_prevents_old_inflight_repopulation(cache):
    c,clock=cache;entered,release=threading.Event(),threading.Event()
    def slow():entered.set();assert release.wait(3);return VALUE
    newer=200,'application/json',b'{"collected_at":125}'
    with ThreadPoolExecutor(max_workers=1) as callers:
        f=callers.submit(c.get,PEER,'actor',PATH,slow);assert entered.wait(2)
        c.invalidate()
        assert c.get(PEER,'actor',PATH,lambda:newer)[:3]==newer
        release.set();f.result(3)
    assert c.get(PEER,'actor',PATH,lambda:VALUE)[:3]==newer


def test_proxy_checks_current_permission_pairing_and_bypasses_controls(monkeypatch,cache):
    c,clock=cache;monkeypatch.setattr(peers_api,'monitor_cache',c)
    monkeypatch.setenv('MYKOS_PUBLIC_ORIGIN','https://owner.example')
    state={'allowed':True,'active':True};calls=[];checks=[]
    def auth(request,modify=False):
        checks.append(1)
        if not state['allowed']:raise HTTPException(403,'No longer admin')
    def get(identifier):
        if not state['active']:raise HTTPException(404,'Revoked')
        return PEER.copy()
    def exchange(*args):calls.append(args[2]);return VALUE
    monkeypatch.setattr(peers_api,'admin',auth);monkeypatch.setattr(peers_api.peers,'get',get)
    monkeypatch.setattr(peers_api.peers,'exchange',exchange)
    class Request:
        method='GET';headers={};cookies={'mykos_session':'test-session'}
        url=SimpleNamespace(query='')
        async def stream(self):
            if False:yield b''
    def request(path,method='GET'):
        r=Request();r.method=method
        return asyncio.run(peers_api.proxy(PEER['id'],path,r))
    assert request(PATH[1:]).headers['x-mykos-peer-cache']=='miss'
    assert request(PATH[1:]).headers['x-mykos-peer-cache']=='hit'
    state['allowed']=False
    with pytest.raises(HTTPException):request(PATH[1:])
    state['allowed']=True;state['active']=False
    with pytest.raises(HTTPException):request(PATH[1:])
    state['active']=True
    for path in ['api/web/current-user','api/terminal/permission','console/12345678/mandatory.json']:
        assert 'x-mykos-peer-cache' not in request(path).headers
    request('api/web/host/reboot','POST')
    assert request(PATH[1:]).headers['x-mykos-peer-cache']=='miss'
    assert len(checks)==9 and calls.count(PATH)==2
