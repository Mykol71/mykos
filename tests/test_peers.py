"""Isolated peer trust, routing, private-note boundaries and tunnel ownership."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import app
import host_history
import mykos_peers as peers
import mykos_users
import peers_api

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/helpers'))
import mykos_peer_vpn as vpn


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    (tmp_path / 'data').mkdir()
    monkeypatch.setattr(peers, 'DB', tmp_path / 'data/peers.db')
    monkeypatch.setattr(mykos_users, 'DB', tmp_path / 'data/auth.db')
    monkeypatch.setenv('MYKOS_PUBLIC_ORIGIN', 'https://testserver')
    calls = []
    monkeypatch.setattr(peers, 'vpn', lambda *args: calls.append(args) or {'configured': True, 'connected': True})
    return tmp_path, calls


def row(state='active', expires=0):
    identifier, node, key = secrets.token_hex(16), secrets.token_hex(16), secrets.token_hex(32)
    with peers.connect() as conn:
        conn.execute('INSERT INTO peers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                     (identifier, node, 'remote.example', '192.0.2.20', '192.0.2.10', 'https://remote.example', 'public-certificate',
                      hashlib.sha256(key.encode()).hexdigest(), secrets.token_hex(32), state, expires, int(time.time())))
    return identifier, node, key


def machine(identifier, node, key, actor=''):
    return {'X-MYKOS-Peer': identifier, 'X-MYKOS-Node': node, 'X-MYKOS-Peer-Key': key, 'X-MYKOS-Actor': actor}


def browser(role='admin'):
    name = 'operator-' + role
    mykos_users.set_password(name, 'test-password-123', role=role, create=True)
    client = TestClient(app.app, base_url='https://testserver')
    result = client.post('/api/login', headers={'Origin': 'https://testserver'}, json={'username': name, 'password': 'test-password-123'})
    assert result.status_code == 200
    return client


def test_registry_private_and_stable(isolated):
    first = peers.node_id()
    assert peers.node_id() == first
    identifier, node, key = row()
    assert os.stat(peers.DB).st_mode & 0o777 == 0o600
    listing = json.dumps(peers.listing())
    stored = peers.get(identifier)
    assert key not in listing and stored['outgoing'] not in listing and stored['incoming'] not in listing
    assert node in listing and identifier in listing


@pytest.mark.parametrize('path', ['/api/login', '/api/logout', '/api/v2/keys', '/api/web/peers', '/api/web/peers/x/proxy/api/web/host-metrics', '/api/web/personal-notes', '/api/web/notes/host', '/api/web/id-notes/01234567', '/api/web/../personal-notes', '/api/web/%2e%2e/notes', '//evil.example/api/web/host-metrics'])
def test_private_and_recursive_paths_denied(path):
    assert not peers.allowed_path(path)


@pytest.mark.parametrize('path', ['/api/web/host-metrics', '/api/web/host-history', '/api/web/host/top', '/api/web/host/reboot', '/api/terminal/host', '/api/terminal/window/rh9', '/api/terminal/rh9', '/api/web/stage-images', '/api/web/containers/01234567/snapshots', '/console/01234567/websockify'])
def test_operational_paths_allowed(path):
    assert peers.allowed_path(path)


def test_machine_can_read_graph_but_not_notes_or_registry(isolated, monkeypatch):
    identifier, node, key = row()
    monkeypatch.setattr(host_history, 'latest', lambda: {'host': {'hostname': 'remote'}, 'cpu': {'percent': 17}})
    client = TestClient(app.app, base_url='https://testserver')
    headers = machine(identifier, node, key)
    assert client.get('/api/web/host-metrics', headers=headers).json()['cpu']['percent'] == 17
    assert client.get('/api/terminal/permission', headers=headers).json()['allowed'] is True
    for path in ('/api/web/personal-notes', '/api/web/notes/host', '/api/web/peers', '/api/logout'):
        assert client.get(path, headers=headers).status_code == 403
    assert client.get('/api/web/host-metrics', headers=machine(identifier, node, '0'*64)).status_code == 401
    assert client.get('/api/web/host-metrics', headers=machine(identifier, '0'*32, key)).status_code == 403
    assert client.get('/api/web/host-metrics').status_code == 401


def test_pending_revoked_offline_fail_closed(isolated, monkeypatch):
    identifier, node, key = row('pending', int(time.time()) + 300)
    client = TestClient(app.app, base_url='https://testserver')
    headers = machine(identifier, node, key)
    assert client.get('/api/web/host-metrics', headers=headers).status_code == 403
    with peers.connect() as conn:
        conn.execute("UPDATE peers SET state='active' WHERE id=?", (identifier,))
    monkeypatch.setattr(peers, 'vpn', lambda *args: {'connected': False})
    assert client.get('/api/web/host-metrics', headers=headers).status_code == 503
    with peers.connect() as conn:
        conn.execute("UPDATE peers SET state='revoked',incoming=NULL,outgoing=NULL WHERE id=?", (identifier,))
    assert client.get('/api/web/host-metrics', headers=headers).status_code == 404


def test_local_broker_requires_admin_and_origin(isolated):
    client = TestClient(app.app, base_url='https://testserver')
    assert client.get('/api/web/peers').status_code == 401
    reader = browser('user')
    assert reader.get('/api/web/peers').status_code == 403
    admin = browser()
    assert admin.get('/api/web/peers').status_code == 200
    assert admin.post('/api/web/peers/invite', json={'local_ip':'192.0.2.10','remote_ip':'192.0.2.20'}).status_code == 403


def test_proxy_node_binding_and_no_credential_leak(isolated, monkeypatch):
    identifier, node, key = row()
    calls = []
    def exchange(peer, method, path, body, content_type, actor):
        calls.append((peer['node'], method, path, actor))
        return 200, 'application/json', b'{"hostname":"remote"}'
    monkeypatch.setattr(peers, 'exchange', exchange)
    client = browser()
    prefix = '/api/web/peers/' + identifier
    result = client.get(prefix + '/proxy/api/web/host-metrics')
    assert result.json()['hostname'] == 'remote'
    assert calls[-1][0] == node and len(calls[-1][3]) == 64
    assert key not in result.text
    for path in ('api/web/notes/host', 'api/web/peers', 'api/login'):
        assert client.get(prefix + '/proxy/' + path).status_code == 403
    with peers.connect() as conn:
        conn.execute("UPDATE peers SET state='revoked' WHERE id=?", (identifier,))
    before = len(calls)
    assert client.get(prefix + '/proxy/api/web/host-metrics').status_code == 404
    assert len(calls) == before


def test_complete_pane_and_scoped_frame_headers(isolated, monkeypatch):
    identifier, _, _ = row()
    monkeypatch.setattr(app, 'BASE', ROOT / 'services/web')
    client = browser()
    result = client.get('/api/web/peers/' + identifier + '/view')
    assert result.status_code == 200
    for value in ('mykos-host-pane', 'mykos-host-chart', 'mykos-history-range', 'mykos-host-status-row', 'mykos-host-hardware', '/api/web/host-metrics', '/api/web/host-history'):
        assert value in result.text
    assert '"/mykos/api/web/peers/' + identifier + '/proxy"' in result.text
    assert result.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert client.get('/').headers['X-Frame-Options'] == 'DENY'
    assert 'main > :not(#mykos-host-pane) { display:none!important; }' not in client.get('/api/web/peers/' + identifier + '/view?full=true').text


def test_expired_cleanup_removes_only_owned_peers(isolated):
    identifier, _, _ = row('pending', int(time.time()) - 1)
    peers_api.cleanup_expired()
    assert ('remove', identifier) in isolated[1]
    with pytest.raises(HTTPException): peers.get(identifier, False)


def test_revoke_invalidates_credentials_before_teardown(isolated, monkeypatch):
    identifier, _, _ = row()
    def remove(*args):
        with peers.connect() as conn:
            record = conn.execute('SELECT * FROM peers WHERE id=?', (identifier,)).fetchone()
            assert record['state'] == 'revoked' and record['incoming'] is None and record['outgoing'] is None
        raise RuntimeError('teardown unavailable')
    monkeypatch.setattr(peers, 'vpn', remove)
    with pytest.raises(RuntimeError): peers_api.revoke(identifier)


@pytest.mark.parametrize('value', ['http://example.com','https://user:pass@example.com','https://example.com:8000','https://example.com/path','https://example.com?x=1'])
def test_origin_is_fixed_https_only(value):
    with pytest.raises(HTTPException): peers.public_origin(value)


def test_invitation_validation_rejects_stale_and_oversized():
    for value in ('invalid', 'x'*32769, base64.urlsafe_b64encode(json.dumps({'version':1}).encode()).decode()):
        with pytest.raises(HTTPException): peers.parse_invitation(value)


def test_snapshot_owners_are_scoped_by_peer_and_browser(isolated):
    from snapshot_preview_api import owner
    from starlette.requests import Request
    def request(node, actor):
        return Request({'type':'http', 'headers':[], 'state':{'mykos_peer':{'node':node,'actor':actor}}})
    assert owner(request('a','b')) != owner(request('a','c'))
    assert owner(request('a','b')) != owner(request('d','b'))
    with pytest.raises(HTTPException): owner(request('a',''))


def test_root_helper_config_is_host_only_and_removal_scoped(tmp_path, monkeypatch):
    state, ipsec = tmp_path/'peers', tmp_path/'ipsec.d'
    state.mkdir(); ipsec.mkdir()
    monkeypatch.setattr(vpn,'STATE',state); monkeypatch.setattr(vpn,'IPSEC',ipsec)
    unrelated = ipsec/'customer.conf'; unrelated.write_text('preserve')
    identifier = '1'*32
    commands=[]
    class Result:
        stdout=''; returncode=0
    def command(args, **kwargs):
        commands.append(args)
        result=Result()
        if args[:3] == ['/usr/sbin/ip','-j','-4']:
            result.stdout='[{"addr_info":[{"local":"192.0.2.10"}]}]'
        return result
    monkeypatch.setattr(vpn,'command',command)
    monkeypatch.setattr(vpn,'firewall_plan',lambda remote: [])
    original=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda self,*a,**k: 'include /etc/ipsec.d/*.conf' if str(self)=='/etc/ipsec.conf' else 'include /etc/ipsec.d/*.secrets' if str(self)=='/etc/ipsec.secrets' else original(self,*a,**k))
    vpn.configure(identifier, {'local':'192.0.2.10','remote':'192.0.2.20','secret':'2'*64})
    conf=(ipsec/('mykos-peer-'+identifier+'.conf')).read_text()
    assert 'leftsubnet=192.0.2.10/32' in conf and 'rightsubnet=192.0.2.20/32' in conf
    assert 'ikev2=insist' in conf and 'auto=start' in conf
    assert '0.0.0.0/0' not in conf and 'restart' not in json.dumps(commands)
    # A newly started daemon has already loaded auto=start: replace only this
    # connection, rather than failing on a duplicate --add.
    assert ['/usr/sbin/ipsec', 'auto', '--replace', 'mykos-peer-'+identifier] in commands
    assert (ipsec/('mykos-peer-'+identifier+'.secrets')).stat().st_mode & 0o777 == 0o600
    vpn.remove(identifier)
    assert unrelated.read_text() == 'preserve'
    assert list(ipsec.iterdir()) == [unrelated]
    assert not list(state.glob('*.json'))


def test_pair_invite_accept_and_confirmation_bind_reciprocal_identity(isolated, monkeypatch):
    origin_db = peers.DB
    destination_db = isolated[0] / 'destination.db'
    server = {'node':'1'*32, 'name':'server.example', 'ip':'192.0.2.10', 'origin':'https://server.example', 'certificate':'server-certificate'}
    client = {'node':'2'*32, 'name':'client.example', 'ip':'192.0.2.20', 'origin':'https://client.example', 'certificate':'client-certificate'}
    monkeypatch.setattr(peers, 'certificate_context', lambda value: (None, b'pin'))
    monkeypatch.setattr(peers, 'identity', lambda ip: server)
    monkeypatch.setattr(peers, 'node_id', lambda: server['node'] if peers.DB == origin_db else client['node'])
    invitation = peers.invite(server['ip'], client['ip'])
    encoded = invitation['invitation']
    value = peers.parse_invitation(encoded)
    assert value['expected_ip'] == client['ip']
    assert 'psk' not in json.dumps(peers.listing())
    # Simulate the authenticated encrypted confirmation at the receiving node.
    def exchange(peer, method, path, body=b'', **kwargs):
        assert peers.DB == destination_db
        assert peer['node'] == server['node'] and peer['outgoing'] == value['incoming']
        peers.DB = origin_db
        try:
            headers = machine(invitation['id'], client['node'], peer['outgoing'])
            result = TestClient(app.app,base_url='https://testserver').post(path, headers=headers, json=json.loads(body))
            assert result.status_code == 200, result.text
            return result.status_code, 'application/json', result.content
        finally: peers.DB = destination_db
    monkeypatch.setattr(peers, 'exchange', exchange)
    peers.DB = destination_db
    monkeypatch.setattr(peers, 'identity', lambda ip: client)
    assert peers.accept(encoded)['state'] == 'active'
    destination = peers.get(invitation['id'])
    peers.DB = origin_db
    source = peers.get(invitation['id'])
    assert source['node'] == client['node'] and destination['node'] == server['node']
    assert hashlib.sha256(source['outgoing'].encode()).hexdigest() == destination['incoming']
    assert hashlib.sha256(destination['outgoing'].encode()).hexdigest() == source['incoming']
    # Replaying an invitation cannot create or replace a pairing.
    monkeypatch.setattr(peers, 'identity', lambda ip: server)
    with pytest.raises(sqlite3.IntegrityError): peers.invite(server['ip'], client['ip'])


def test_tls_pinning_with_real_local_listener(isolated, tmp_path, monkeypatch):
    import socket
    import ssl
    import subprocess
    import threading
    key, cert = tmp_path/'key.pem', tmp_path/'cert.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    encoded = base64.b64encode(der).decode()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert,key)
    listener = socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(); listener.settimeout(4)
    port = listener.getsockname()[1]
    seen=[]
    def serve():
        try:
            raw,_ = listener.accept()
            with raw, context.wrap_socket(raw,server_side=True) as stream:
                seen.append(stream.recv(8192))
                stream.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{"ok":true}')
        except (OSError,ssl.SSLError): pass
    worker=threading.Thread(target=serve);worker.start()
    original=socket.create_connection
    monkeypatch.setattr(peers.socket,'create_connection',lambda address,timeout: original(('127.0.0.1',port),timeout))
    peer={'id':'1'*32,'ip':'192.0.2.20','origin':'https://remote.example','certificate':encoded,'outgoing':'2'*64}
    try:
        status,_,body = peers.exchange(peer,'GET','/api/web/host-metrics')
        assert status == 200 and json.loads(body)['ok'] is True
        assert b'X-MYKOS-Peer-Key: '+b'2'*64 in seen[0]
    finally:
        listener.close();worker.join(timeout=5)
        assert not worker.is_alive()
    # A pin mismatch stops before HTTP headers (and the secret) are sent.
    class FakeTLS:
        def close(self): pass
        def getpeercert(self,binary_form=False): return b'different certificate'
    class FakeContext:
        verify_mode=ssl.CERT_REQUIRED
        check_hostname=False
        post_handshake_auth=False
        def wrap_socket(self,*args,**kwargs): return FakeTLS()
    raw_closed=[]
    class Raw:
        def close(self): raw_closed.append(True)
    monkeypatch.setattr(peers,'certificate_context',lambda value:(FakeContext(),b'0'*32))
    monkeypatch.setattr(peers.socket,'create_connection',lambda *a,**k:Raw())
    with pytest.raises(HTTPException) as error: peers.exchange(peer,'GET','/api/web/host-metrics')
    assert error.value.status_code==502 and raw_closed


def test_existing_machine_console_rechecks_revocation(isolated):
    from desktop_proxy import authorize
    from starlette.requests import Request
    identifier, node, key = row()
    headers = [(k.lower().encode(), v.encode()) for k,v in machine(identifier,node,key).items()]
    request = Request({'type':'http','path':'/console/01234567/websockify','headers':headers,'state':{}})
    request.scope['state']['mykos_peer'] = peers.authenticate(request.scope)
    authorize(request)
    with peers.connect() as conn:
        conn.execute("UPDATE peers SET state='revoked',incoming=NULL,outgoing=NULL WHERE id=?", (identifier,))
    with pytest.raises(HTTPException): authorize(request)


def test_preview_validation_links_stay_canonical_until_navigation(isolated):
    identifier, _, _ = row()
    path = '/mykos/api/web/snapshot-preview/' + 'a'*32 + '/page'
    result = peers_api.rewrite_response(identifier, 'api/web/example', 'application/json', json.dumps({'url': path}).encode())
    assert json.loads(result)['url'] == path
    settings = {'path': '/mykos/console/preview-' + 'b'*32 + '/websockify', 'host':'remote.example','port':'443'}
    result = peers_api.rewrite_response(identifier, 'console/preview-'+'b'*32+'/mandatory.json','application/json',json.dumps(settings).encode())
    data=json.loads(result)
    assert data['host']=='testserver'
    assert data['path'].startswith('/mykos/api/web/peers/'+identifier+'/proxy/console/')


def test_rpm_replaced_template_still_activates_apache(monkeypatch):
    import importlib.machinery, importlib.util
    path=ROOT/'bin/mykos-install'
    loader=importlib.machinery.SourceFileLoader('peer_install_activation',str(path));spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    class Result: returncode=0
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:Result())
    calls=[];monkeypatch.setattr(module,'command',lambda args:calls.append(list(map(str,args))))
    module.activate_apache('/usr/bin/python3.11')
    assert calls[0][-1]=='--apply' and 'mykos-console-proxy' in calls[0][1]
    assert calls[1:]==[['apachectl','configtest'],['systemctl','reload','httpd']]
    def fail(args):
        calls.append(args)
        if args==['apachectl','configtest']:raise RuntimeError('Invalid Apache config')
    calls.clear();monkeypatch.setattr(module,'command',fail)
    with pytest.raises(RuntimeError):module.activate_apache('/usr/bin/python3.11')
    assert ['systemctl','reload','httpd'] not in calls
    calls.clear();Result.returncode=3
    module.activate_apache('/usr/bin/python3.11')
    assert not calls
