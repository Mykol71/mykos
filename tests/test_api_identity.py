"""HTTP authorization and identity lifecycle tests with mocked host helpers."""
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import pytest
from fastapi.testclient import TestClient

WEB=Path(__file__).resolve().parents[1]/'services/web'
sys.path.insert(0,str(WEB))
import app
import api_v2
import container_admin
import mykos_api_keys
import mykos_users
import monitor_api

@pytest.fixture
def environment(tmp_path,monkeypatch):
    base=tmp_path.resolve();data=base/'data';data.mkdir()
    import host_history
    monkeypatch.setattr(host_history,'LIVE',data/'host-metrics.json')
    monkeypatch.setattr(host_history,'DATABASE',data/'host-history.sqlite3')
    monkeypatch.setattr(app,'BASE',base)
    monkeypatch.setattr(mykos_users,'DB',data/'auth.db')
    monkeypatch.setattr(mykos_api_keys,'DB',data/'api_keys.db')
    monkeypatch.setattr(api_v2,'ROOT',base);monkeypatch.setattr(api_v2,'DATA',data)
    monkeypatch.setattr(monitor_api,'BASE',base);monkeypatch.setattr(monitor_api,'TOKEN_FILE',data/'api-token')
    monkeypatch.setenv('MYKOS_PUBLIC_ORIGIN','https://testserver')
    (base/'inventory.json').write_text(json.dumps({'nodes':[{'id':'01234567','name':'Example','hub':'Test'}]}))
    (data/'status.json').write_text(json.dumps({'collected_at':int(time.time()),'observations':{'01234567':{'status':'online','vpn_status':'up'}}}))
    calls=[]
    def fake(*args):
        calls.append(args)
        if args[0]=='diskfree':return {'free':'10 GiB','free_bytes':10*1024**3}
        if args[0]=='list':return [{'shop':'01234567','name':'01234567.example','state':'running'}]
        return {'shop':args[1],'state':'queued','output':'test output'}
    monkeypatch.setattr(container_admin,'invoke',fake)
    monkeypatch.setattr(app,'mykos_stage_invoke',fake)
    mykos_users.set_password('reader','reader-password-123',create=True)
    mykos_users.set_password('operator','operator-password-123',role='admin',create=True)
    return base,calls

def login(name,password):
    client=TestClient(app.app,base_url='https://testserver')
    response=client.post('/api/login',headers={'Origin':'https://testserver'},json={'username':name,'password':password})
    assert response.status_code==200,response.text
    return client


def test_desktop_endpoints_require_admin_and_return_matching_ports(environment,monkeypatch):
    data={'host':'192.0.2.10','desktop_port':23017,'console_port':18023,'protocol':'rdp','running':True,
          'console_url':'http://192.0.2.10:18023/','login_supported':True}
    calls=[]
    monkeypatch.setattr(container_admin,'invoke',lambda *args: calls.append(args) or data)
    client=TestClient(app.app,base_url='https://testserver')
    assert client.get('/api/web/desktop/01234567').status_code==401
    reader=login('reader','reader-password-123')
    assert reader.get('/api/web/desktop/01234567').status_code==403
    admin=login('operator','operator-password-123')
    assert admin.get('/api/web/desktop/01234567').json()==data
    response=admin.get('/api/web/desktop/01234567/rdp')
    assert response.status_code==200 and '192.0.2.10:23017' in response.text
    assert calls[-1]==('desktop-info','01234567')
    key=mykos_api_keys.create('desktop-admin','admin')
    assert client.get('/api/v2/nodes/01234567/desktop',headers={'X-MYKOS-API-Key':key}).json()==data


def test_host_action_permissions_and_targets(environment, monkeypatch):
    calls = []
    monkeypatch.setattr(container_admin, 'invoke', lambda *args: calls.append(args) or {'output':'host output'})
    client = TestClient(app.app, base_url='https://testserver')
    assert client.get('/api/web/host/logs').status_code == 401
    reader = login('reader', 'reader-password-123')
    for action in ('logs', 'top', 'ipsec'):
        assert reader.get('/api/web/host/' + action).status_code == 200
        assert calls[-1] == ('host-' + action,)
    assert reader.post('/api/web/host/reboot', headers={'Origin':'https://testserver'}, json={'hostname':'host.example'}).status_code == 403
    admin = login('operator', 'operator-password-123')
    assert admin.post('/api/web/host/reboot', json={'hostname':'host.example'}).status_code == 403
    assert admin.post('/api/web/host/reboot', headers={'Origin':'https://testserver'}, json={'hostname':'host.example'}).status_code == 200
    assert calls[-1] == ('host-reboot', 'host.example')
    assert reader.get('/api/web/host/exec').status_code == 404
    read_key = mykos_api_keys.create('host-reader', 'read')
    admin_key = mykos_api_keys.create('host-admin', 'admin')
    assert client.post('/api/v2/host/reboot',headers={'X-MYKOS-API-Key':read_key},json={'hostname':'host.example'}).status_code == 403
    assert client.post('/api/v2/host/reboot',headers={'X-MYKOS-API-Key':admin_key},json={'hostname':'host.example'}).status_code == 200


def test_host_terminal_requires_admin_and_targets_host(environment, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    calls = []
    async def bridge(ws, shop, action='shell'):
        calls.append((shop, action))
        await ws.accept()
        await ws.send_text('host terminal')
        await ws.close()
    monkeypatch.setattr(app, 'mykos_terminal_bridge', bridge)
    reader = login('reader', 'reader-password-123')
    with pytest.raises(WebSocketDisconnect):
        with reader.websocket_connect('wss://testserver/api/terminal/host', headers={'Origin':'https://testserver'}): pass
    admin = login('operator', 'operator-password-123')
    with admin.websocket_connect('wss://testserver/api/terminal/host', headers={'Origin':'https://testserver'}) as ws:
        assert ws.receive_text() == 'host terminal'
    with admin.websocket_connect('wss://testserver/api/terminal/host?action=add-vpn', headers={'Origin':'https://testserver'}) as ws:
        assert ws.receive_text() == 'host terminal'
    with pytest.raises(WebSocketDisconnect):
        with reader.websocket_connect('wss://testserver/api/terminal/host?action=add-vpn', headers={'Origin':'https://testserver'}): pass
    key = mykos_api_keys.create('host-terminal', 'admin')
    with admin.websocket_connect('/api/v2/terminal/host',headers={'X-MYKOS-API-Key':key}) as ws:
        assert ws.receive_text() == 'host terminal'
    with admin.websocket_connect('/api/v2/terminal/host?action=add-vpn',headers={'X-MYKOS-API-Key':key}) as ws:
        assert ws.receive_text() == 'host terminal'
    assert calls == [('host','shell'),('host','add-vpn'),('host','shell'),('host','add-vpn')]

def test_user_admin_boundaries_and_invalid_key_no_fallback(environment):
    base,calls=environment
    client=login('reader','reader-password-123')
    assert client.get('/api/v2/nodes').status_code==401
    assert client.get('/api/admin/containers/01234567/logs').status_code==200
    assert client.get('/api/terminal/permission').json()=={'allowed':False}
    for path in ['/api/admin/containers/01234567/checkit']:
        assert client.get(path).status_code==403
    assert client.post('/api/v2/actions/stage',json={'shop':'01234567'}).status_code==401
    assert not any(x[0] in ('checkit','stage-start') for x in calls)
    assert client.get('/api/v2/nodes',headers={'X-MYKOS-API-Key':'invalid'}).status_code==401
    admin=login('operator','operator-password-123')
    assert admin.get('/api/terminal/permission').json()=={'allowed':True}
    assert admin.get('/api/v2/jobs/01234567').status_code==401
    assert admin.get('/api/v2/nodes/01234567/checkit').status_code==401
    assert admin.post('/api/v2/actions/stage',json={'shop':'01234567'}).status_code==401
    assert admin.post('/api/web/stage',headers={'Origin':'https://testserver'},json={'shop':'01234567'}).status_code==200
    assert admin.get('/api/admin/containers/01234567/checkit').status_code==200

def test_key_scopes_revocation_and_browser_separation(environment):
    client=TestClient(app.app,base_url='https://testserver')
    read=mykos_api_keys.create('monitor','read');admin=mykos_api_keys.create('automation','admin')
    h={'X-MYKOS-API-Key':read}
    assert client.get('/api/v2/nodes',headers=h).status_code==200
    assert client.post('/api/v2/actions/stage',headers=h,json={'shop':'01234567'}).status_code==403
    assert client.get('/api/nodes',headers=h).status_code==401
    h={'X-MYKOS-API-Key':admin}
    assert client.post('/api/v2/actions/stage',headers=h,json={'shop':'01234567'}).status_code==200
    with mykos_api_keys.connect() as c:
        rows=c.execute('SELECT digest FROM api_keys').fetchall()
        assert all(admin not in r[0] and read not in r[0] for r in rows)
        c.execute('UPDATE api_keys SET revoked_at=?',(int(time.time()),))
    assert client.get('/api/v2/nodes',headers=h).status_code==401

@pytest.mark.parametrize('pane', ['repos','terminal'])
def test_pane_notes_private_to_mgreen_and_conflicts(environment,pane):
    anonymous=TestClient(app.app,base_url='https://testserver')
    for path in (f'/api/web/notes/{pane}',f'/api/v2/notes/{pane}'):
        assert anonymous.get(path).status_code==401
        for name in ('reader','operator'):
            client=login(name,name+'-password-123')
            assert client.get(path).status_code==403
            assert client.put(path,headers={'Origin':'https://testserver'},json={'text':'denied','revision':0}).status_code==403
        for scope in ('read','admin'):
            key=mykos_api_keys.create('not-a-user',scope)
            assert anonymous.get(path,headers={'X-MYKOS-API-Key':key}).status_code==401
    mykos_users.set_password('mgreen','mgreen-test-password-123',role='admin',create=True)
    owner=login('mgreen','mgreen-test-password-123')
    payload={'text':'Private change request <script>plain text</script>','revision':0}
    for path in (f'/api/web/notes/{pane}',f'/api/v2/notes/{pane}'):
        assert owner.get(path).json()['revision']==0
        assert owner.put(path,json=payload).status_code==403
    assert owner.put(f'/api/web/notes/{pane}',headers={'Origin':'https://testserver'},json=payload).status_code==200
    assert owner.get(f'/api/v2/notes/{pane}').json()['text']==payload['text']
    assert owner.put(f'/api/v2/notes/{pane}',headers={'Origin':'https://testserver'},json=payload).status_code==409
    assert owner.put(f'/api/v2/notes/{pane}',headers={'Origin':'https://testserver'},json={'text':'','revision':1}).status_code==200
    assert owner.get(f'/api/web/notes/{pane}').json()['text']==''
    assert owner.put(f'/api/web/notes/{pane}',headers={'Origin':'https://testserver'},json={'text':'x'*20001,'revision':2}).status_code==422


def test_role_and_password_changes_invalidate_sessions(environment):
    client=login('operator','operator-password-123')
    mykos_users.set_password('operator','replacement-password')
    assert client.get('/api/nodes').status_code==401
    assert mykos_users.is_admin('operator')
    with mykos_users.connect() as c:c.execute("UPDATE users SET role='user' WHERE name='operator'")
    client=login('operator','replacement-password')
    assert not client.get('/api/terminal/permission').json()['allowed']
    with mykos_users.connect() as c:c.execute("DELETE FROM users WHERE name='operator'")
    assert client.get('/api/nodes').status_code==401

def test_legacy_users_migrate_without_implicit_admin(environment):
    base,_=environment
    # Simulate the old three-column table in a separate temporary identity database.
    old=base/'legacy.db'
    with sqlite3.connect(old) as c:
        c.execute('CREATE TABLE users(name TEXT PRIMARY KEY,salt TEXT,hash TEXT)')
        c.execute("INSERT INTO users VALUES('admin','salt','hash')")
    previous=mykos_users.DB;mykos_users.DB=old
    try:
        with mykos_users.connect() as c:assert c.execute('SELECT role FROM users').fetchone()[0]=='user'
        assert not mykos_users.is_admin('admin')
    finally:mykos_users.DB=previous

def test_api_validation_freshness_and_openapi(environment):
    base,_=environment
    key=mykos_api_keys.create('test','read');client=TestClient(app.app,base_url='https://testserver')
    h={'X-MYKOS-API-Key':key}
    assert client.get('/api/v2/nodes/not-a-shop',headers=h).status_code==400
    assert client.get('/api/v2/nodes/99999999',headers=h).status_code==404
    assert client.get('/api/v2/nodes?include=bad',headers=h).status_code==400
    (base/'data/status.json').write_text(json.dumps({'collected_at':0,'observations':{'01234567':{'status':'online'}}}))
    value=client.get('/api/v2/nodes',headers=h).json()
    assert not value['fresh']['status'] and value['nodes'][0]['status']=='unknown'
    schema=client.get('/api/v2/openapi.json',headers=h).json()
    assert schema['servers']==[{'url':'/mykos'}]
    assert '/api/v2/actions/stage' in schema['paths']
    docs = client.get('/api/v2/docs')
    assert docs.status_code == 200
    assert '<style id="mykos-api-theme">' in docs.text
    assert 'color-scheme: only light' in docs.text
    assert '<meta name="color-scheme" content="only light">' in docs.text
    assert '/mykos/api/v2/docs/theme.css' not in docs.text
    assert client.get('/api/v2/docs/theme.css').status_code == 200
    assert client.get('/api/v2/openapi.json').status_code==200
    for path,entry in schema['paths'].items():
        for operation in entry.values():
            if isinstance(operation, dict) and 'responses' in operation:
                assert operation['security']==[{'MYKOSNotesSession' if '/notes/' in path else 'MYKOSApiKey': []}]


def test_image_os_details_available_to_dashboard_and_api(environment):
    base, _ = environment
    details = {'image': 'localhost/rti:9', 'os_type': 'Red Hat Enterprise Linux',
               'os_version': '9.6', 'running': False}
    (base/'data/details.json').write_text(json.dumps({
        'collected_at': int(time.time()), 'details': {'01234567': details}}))
    browser = login('reader', 'reader-password-123')
    assert browser.get('/api/nodes/01234567/details').json()['details'] == details
    client = TestClient(app.app, base_url='https://testserver')
    key = mykos_api_keys.create('os-monitor', 'read')
    response = client.get('/api/v2/nodes/01234567/details',
                          headers={'X-MYKOS-API-Key': key})
    assert response.status_code == 200
    assert response.json() == {'shopcode': '01234567', **details}
    status = client.get('/api/v2/nodes/01234567/status', headers={'X-MYKOS-API-Key': key}).json()
    assert {field: status[field] for field in ('image', 'os_type', 'os_version')} == {
        field: details[field] for field in ('image', 'os_type', 'os_version')}


def test_host_metrics_require_dashboard_login(environment, monkeypatch):
    import host_metrics
    metrics = {'cpu_percent': 25, 'memory': {'percent': 50}}
    monkeypatch.setattr(host_metrics, 'snapshot', lambda: metrics)
    client = TestClient(app.app, base_url='https://testserver')
    assert client.get('/api/web/host-metrics').status_code == 401
    browser = login('reader', 'reader-password-123')
    assert browser.get('/api/web/host-metrics').json() == metrics

def test_local_managers_user_and_key_lifecycle(environment,monkeypatch,capsys):
    import os
    import manage
    base,_=environment
    monkeypatch.setattr(manage,'BASE',base)
    monkeypatch.setattr(manage.getpass,'getpass',lambda prompt:'local-management-password')
    old_mask=os.umask(0o077)
    def cli(*args):
        monkeypatch.setattr(sys,'argv',['manage.py',*args]);manage.main()
        return capsys.readouterr().out
    try:
        cli('users','create','newadmin','--role','admin')
        assert mykos_users.is_admin('newadmin')
        cli('users','create','newuser')
        assert not mykos_users.is_admin('newuser')
        cli('users','role','newuser','admin')
        assert mykos_users.is_admin('newuser')
        cli('users','passwd','newuser')
        assert mykos_users.is_admin('newuser')
        cli('users','revoke-sessions','newuser')
        cli('users','delete','newuser')
        assert 'newuser' not in cli('users','list')
        key=cli('keys','create','temporary','read').strip()
        assert mykos_api_keys.authenticate(key)=='read'
        entries=json.loads(cli('keys','list'))
        assert key not in json.dumps(entries)
        cli('keys','revoke',entries[0]['id'])
        assert mykos_api_keys.authenticate(key) is None
    finally:os.umask(old_mask)


def test_mykos_menu_authorization_and_validation(environment, monkeypatch):
    import mykos_admin_api
    calls = []
    monkeypatch.setattr(mykos_admin_api, '_cache', {})
    monkeypatch.setattr(mykos_admin_api, 'invoke', lambda *args: calls.append(args) or {'ok': True})
    anonymous = TestClient(app.app, base_url='https://testserver')
    assert anonymous.get('/api/web/mykos/backups').status_code == 401
    reader = login('reader', 'reader-password-123')
    assert reader.get('/api/web/mykos/backups').status_code == 200
    assert reader.get('/api/web/mykos/repos').status_code == 200
    for action in ('create', 'key', 'subscriber', 'delete', 'keys'):
        assert reader.post('/api/web/mykos/backups', headers={'Origin': 'https://testserver'},
                           json={'action': action, 'code': '01234567'}).status_code == 403
    admin = login('operator', 'operator-password-123')
    assert admin.post('/api/web/mykos/repos', json={'action': 'sync'}).status_code == 403
    assert admin.post('/api/web/mykos/repos', headers={'Origin': 'https://testserver'},
                      json={'action': 'sync'}).status_code == 200
    assert calls[-1][:3] == ('repo-action', 'sync', 'all')
    assert admin.post('/api/web/mykos/backups', headers={'Origin': 'https://testserver'},
                      json={'action': 'delete', 'code': '../../etc'}).status_code == 422
    assert admin.post('/api/web/mykos/repos', headers={'Origin': 'https://testserver'},
                      json={'action': 'exec'}).status_code == 422


@pytest.fixture
def api_dashboard(environment, monkeypatch):
    import host_metrics
    import mykos_admin_api
    monkeypatch.setattr(mykos_admin_api, '_cache', {})
    calls = []
    accounts = {'accounts': [{'code': '01234567', 'subscriber': True}], 'storage': {'free_bytes': 100}}
    repos = {'repos': [{'name': 'rhel-9-rpms', 'used_bytes': 123}], 'service': 'active', 'sync': 'inactive'}
    metrics = {'cpu_percent': 12.5, 'cpu_count': 8, 'load_average': [1, 2, 3],
               'memory': {'percent': 25}, 'disk': {'percent': 50}, 'uptime_seconds': 90061}
    monkeypatch.setattr(host_metrics, 'snapshot', lambda: metrics)
    def invoke(*args):
        calls.append(args)
        if args[0] == 'backup-list': return accounts
        if args[0] == 'repo-list': return repos
        if args[1] == 'keys': return {'public_key': 'test-public', 'private_key': 'test-private'}
        return {'ok': True}
    monkeypatch.setattr(mykos_admin_api, 'invoke', invoke)
    client = TestClient(app.app, base_url='https://testserver')
    read = {'X-MYKOS-API-Key': mykos_api_keys.create('dashboard-read', 'read')}
    admin = {'X-MYKOS-API-Key': mykos_api_keys.create('dashboard-admin', 'admin')}
    return client, read, admin, calls, metrics


def test_dashboard_api_reads_and_cookie_separation(api_dashboard):
    client, read, admin, calls, metrics = api_dashboard
    for path in ('/host/metrics', '/backups', '/backups/01234567', '/repos', '/repos/rhel-9-rpms'):
        assert client.get('/api/v2' + path).status_code == 401
        assert client.get('/api/v2' + path, headers=read).status_code == 200
    assert client.get('/api/v2/host/metrics', headers=read).json() == metrics
    assert client.get('/api/v2/host', headers=read).json()['metrics'] == metrics
    assert client.get('/api/v2/overview', headers=read).json()['host']['metrics'] == metrics
    assert client.get('/api/v2/backups/99999999', headers=read).status_code == 404
    assert client.get('/api/v2/backups/bad', headers=read).status_code == 400
    assert client.get('/api/v2/repos/missing', headers=read).status_code == 404
    assert client.get('/api/v2/repos/--all', headers=read).status_code == 400
    # Refresh bypasses the shared inventory cache.
    before = calls.count(('repo-list',))
    client.get('/api/v2/repos?refresh=true', headers=read)
    assert calls.count(('repo-list',)) == before + 1
    browser = login('operator', 'operator-password-123')
    assert browser.get('/api/v2/backups').status_code == 401
    assert browser.get('/api/v2/backups', headers=read).status_code == 200


@pytest.mark.parametrize('action', ['create', 'key', 'subscriber', 'delete', 'keys'])
def test_backup_api_admin_actions_without_cookies_or_origin(api_dashboard, action):
    client, read, admin, calls, _ = api_dashboard
    body = {'action': action, 'code': '01234567', 'key': 'ssh-ed25519 test',
            'subscriber': True, 'remove_data': False}
    assert client.post('/api/v2/backups', json=body).status_code == 401
    assert client.post('/api/v2/backups', headers=read, json=body).status_code == 403
    assert not calls
    assert client.post('/api/v2/backups', headers=admin, json=body).status_code == 200
    assert calls[-1][:3] == ('backup-action', action, '01234567')
    assert json.loads(calls[-1][3])['subscriber'] is True


@pytest.mark.parametrize('action', ['delete', 'enable', 'disable', 'sync'])
def test_repository_api_admin_actions_without_cookies_or_origin(api_dashboard, action):
    client, read, admin, calls, _ = api_dashboard
    body = {'action': action, 'name': 'rhel-9-rpms' if action == 'delete' else 'all'}
    assert client.post('/api/v2/repos', json=body).status_code == 401
    assert client.post('/api/v2/repos', headers=read, json=body).status_code == 403
    assert not calls
    assert client.post('/api/v2/repos', headers=admin, json=body).status_code == 200
    assert calls[-1][:3] == ('repo-action', action, body['name'])


def test_private_backup_keys_and_validation(api_dashboard):
    client, read, admin, calls, _ = api_dashboard
    path = '/api/v2/backups/01234567/keys'
    assert client.get(path).status_code == 401
    assert client.get(path, headers=read).status_code == 403
    assert not calls
    response = client.get(path, headers=admin)
    assert response.json()['private_key'] == 'test-private'
    assert response.headers['cache-control'] == 'no-store'
    for body in ({'action': 'delete', 'code': '../../etc'},
                 {'action': 'exec', 'code': '01234567'}):
        assert client.post('/api/v2/backups', headers=admin, json=body).status_code == 422
    assert client.post('/api/v2/repos', headers=admin,
                       json={'action': 'delete', 'name': '../outside'}).status_code == 422
    with mykos_api_keys.connect() as connection:
        connection.execute('UPDATE api_keys SET revoked_at=?', (int(time.time()),))
    assert client.get(path, headers=admin).status_code == 401


def test_terminal_api_key_and_browser_policies(api_dashboard, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    client, read, admin, _, _ = api_dashboard
    calls = []
    async def bridge(ws, shop):
        calls.append(shop)
        await ws.accept()
        await ws.send_text('test terminal')
        await ws.close()
    monkeypatch.setattr(app, 'mykos_terminal_bridge', bridge)
    for headers, shop in [({}, '01234567'), (read, '01234567'),
                          ({'X-MYKOS-API-Key': 'invalid'}, '01234567'), (admin, 'bad')]:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect('/api/v2/terminal/' + shop, headers=headers): pass
        assert error.value.code == 1008
    assert not calls
    with client.websocket_connect('/api/v2/terminal/01234567', headers=admin) as ws:
        assert ws.receive_text() == 'test terminal'
    browser = login('operator', 'operator-password-123')
    with pytest.raises(WebSocketDisconnect):
        with browser.websocket_connect('wss://testserver/api/v2/terminal/01234567'): pass
    with browser.websocket_connect('wss://testserver/api/terminal/01234567',
                                   headers={'Origin': 'https://testserver'}) as ws:
        assert ws.receive_text() == 'test terminal'
    with pytest.raises(WebSocketDisconnect):
        with browser.websocket_connect('wss://testserver/api/terminal/01234567',
                                       headers={'Origin': 'https://wrong.example'}): pass
    assert calls == ['01234567', '01234567']



def test_dashboard_api_schema_and_action_discovery(api_dashboard):
    client, read, _, _, _ = api_dashboard
    schema = client.get('/api/v2/openapi.json').json()
    for path, methods in {
            '/api/v2/host/metrics': ['get'], '/api/v2/backups': ['get', 'post'],
            '/api/v2/backups/{code}': ['get'], '/api/v2/backups/{code}/keys': ['get'],
            '/api/v2/repos': ['get', 'post'], '/api/v2/repos/{name}': ['get']}.items():
        for method in methods:
            assert schema['paths'][path][method]['security'] == [{'MYKOSApiKey': []}]
    options = client.get('/api/v2/actions', headers=read).json()
    assert options['actions']['terminal'] == 'WS /api/v2/terminal/{shop}'
    assert options['action_options']['backups'] == ['create', 'key', 'subscriber', 'delete', 'keys']
    assert all(isinstance(value, str) for value in options['actions'].values())


def test_staging_os_choices_and_selection_through_browser_and_api(environment, monkeypatch):
    _, _ = environment
    calls = []
    def helper(*args):
        calls.append(args)
        return {'images': [{'os': 'rh9', 'image': 'sha256:abc123'}]} if args[0] == 'stage-images' else {'state': 'queued'}
    monkeypatch.setattr(app, 'mykos_stage_invoke', helper)
    monkeypatch.setattr(container_admin, 'invoke', helper)
    browser = login('operator', 'operator-password-123')
    reader = login('reader', 'reader-password-123')
    assert reader.get('/api/web/stage-images').status_code == 200
    assert browser.get('/api/web/stage-images').json()['images'][0]['os'] == 'rh9'
    payload = {'shop': '01234567', 'os': 'rh9', 'restore': False}
    assert browser.post('/api/web/stage', headers={'Origin': 'https://testserver'}, json=payload).status_code == 200
    key = mykos_api_keys.create('staging-test', 'admin')
    client = TestClient(app.app, base_url='https://testserver')
    headers = {'X-MYKOS-API-Key': key}
    assert client.get('/api/v2/images', headers=headers).status_code == 200
    assert client.post('/api/v2/actions/stage', headers=headers, json=payload).status_code == 200
    assert calls == [('stage-images',), ('stage-images',), ('stage-start', '01234567', 'no', 'rh9'),
                     ('stage-images',), ('stage-start', '01234567', 'no', 'rh9')]


def test_add_vpn_browser_and_api_permissions(api_dashboard, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    client, read, admin, _, _ = api_dashboard
    calls = []
    async def bridge(ws, shop, action='shell'):
        calls.append((shop, action))
        await ws.accept(); await ws.send_text('VPN prompts'); await ws.close()
    monkeypatch.setattr(app, 'mykos_terminal_bridge', bridge)
    with client.websocket_connect('/api/v2/terminal/01234567?action=add-vpn', headers=admin) as ws:
        assert ws.receive_text() == 'VPN prompts'
    for path, headers in [('/api/v2/terminal/01234567?action=add-vpn', read),
                          ('/api/v2/terminal/01234567?action=bad', admin)]:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path, headers=headers): pass
    browser = login('operator', 'operator-password-123')
    with browser.websocket_connect('wss://testserver/api/terminal/01234567?action=add-vpn', headers={'Origin':'https://testserver'}) as ws:
        assert ws.receive_text() == 'VPN prompts'
    with pytest.raises(WebSocketDisconnect):
        with browser.websocket_connect('wss://testserver/api/terminal/01234567?action=add-vpn', headers={'Origin':'https://wrong.example'}): pass
    assert calls == [('01234567', 'add-vpn'), ('01234567', 'add-vpn')]


def test_host_internal_ip_available_in_dashboard_and_api(api_dashboard, monkeypatch):
    import host_metrics
    client, read, _, _, _ = api_dashboard
    monkeypatch.setattr(host_metrics, 'internal_ip', lambda: '192.168.222.5')
    browser = login('reader', 'reader-password-123')
    assert browser.get('/api/web/host-version').json()['internal_ip'] == '192.168.222.5'
    data = client.get('/api/v2/host', headers=read).json()
    assert data['internal_ip'] == '192.168.222.5'
    assert 'hostname' in data


def test_migrated_api_key_keeps_original_digest(environment):
    import hashlib
    key='previous2_'+'a'*16+'_'+'b'*43
    with mykos_api_keys.connect() as c:
        c.execute('INSERT INTO api_keys VALUES (?,?,?,?,?,NULL)',
                  ('a'*16,'existing-monitor','read',hashlib.sha256(key.encode()).hexdigest(),int(time.time())))
    client=TestClient(app.app,base_url='https://testserver')
    assert client.get('/api/v2/nodes',headers={'X-MYKOS-API-Key':key}).status_code==200
    assert client.get('/api/v2/nodes',headers={'X-MYKOS-API-Key':key[:-1]+'c'}).status_code==401
    assert mykos_api_keys.create('new-monitor','read').startswith('mykos2_')


def test_snapshot_lifecycle_browser_and_key_authorization(environment,monkeypatch):
    calls=[]
    monkeypatch.setattr(container_admin,'invoke',lambda *args:calls.append(args) or {'snapshots':[],'state':'queued'})
    anonymous=TestClient(app.app,base_url='https://testserver')
    reader=login('reader','reader-password-123');operator=login('operator','operator-password-123')
    admin_key=mykos_api_keys.create('snapshot-admin','admin');read_key=mykos_api_keys.create('snapshot-reader','read')
    admin={'X-MYKOS-API-Key':admin_key};read={'X-MYKOS-API-Key':read_key}
    identifier='1792900000-abcdef123456'
    assert anonymous.get('/api/web/snapshots').status_code==401
    assert reader.get('/api/web/snapshots').status_code==200
    assert anonymous.get('/api/v2/snapshots',headers=read).status_code==200
    for suffix in ('start','stop','snapshot','snapshots/'+identifier+'/restore'):
        browser='/api/web/containers/01234567/'+suffix
        api='/api/v2/nodes/01234567/'+suffix
        assert reader.post(browser,headers={'Origin':'https://testserver'}).status_code==403
        assert operator.post(browser).status_code==403
        assert operator.post(browser,headers={'Origin':'https://evil.test'}).status_code==403
        assert operator.post(browser,headers={'Origin':'https://testserver'}).status_code==200
        assert anonymous.post(api,headers=read).status_code==403
        assert anonymous.post(api,headers=admin).status_code==200
    assert ('restore-snapshot','01234567',identifier) in calls
    assert anonymous.post('/api/v2/nodes/01234567/snapshots/not-a-real-snapshot/restore',headers=admin).status_code==400
    assert anonymous.post('/api/v2/nodes/not-a-code/snapshot',headers=admin).status_code==400
    schema=anonymous.get('/api/v2/openapi.json').json()['paths']
    for path in ('/api/v2/snapshots','/api/v2/nodes/{shop}/snapshots/{identifier}/restore'):
        assert path in schema
        assert next(iter(schema[path].values()))['security']==[{'MYKOSApiKey':[]}]



def test_delete_snapshot_browser_and_api_auth(environment,monkeypatch):
    calls=[]
    monkeypatch.setattr(container_admin,'invoke',lambda *args:calls.append(args) or {'state':'queued'})
    anonymous=TestClient(app.app,base_url='https://testserver')
    reader=login('reader','reader-password-123');admin=login('operator','operator-password-123')
    read_key=mykos_api_keys.create('snapshot-delete-reader','read');admin_key=mykos_api_keys.create('snapshot-delete-admin','admin')
    identifier='1792900000-abcdef123456'
    browser='/api/web/containers/01234567/snapshots/'+identifier
    api='/api/v2/nodes/01234567/snapshots/'+identifier
    assert reader.delete(browser,headers={'Origin':'https://testserver'}).status_code==403
    assert admin.delete(browser).status_code==403
    assert admin.delete(browser,headers={'Origin':'https://evil.test'}).status_code==403
    assert admin.delete(browser,headers={'Origin':'https://testserver'}).status_code==200
    assert anonymous.delete(api).status_code==401
    assert anonymous.delete(api,headers={'X-MYKOS-API-Key':read_key}).status_code==403
    assert anonymous.delete(api,headers={'X-MYKOS-API-Key':admin_key}).status_code==200
    assert calls==[('delete-snapshot','01234567',identifier)]*2
    assert anonymous.delete('/api/v2/nodes/01234567/snapshots/bad',headers={'X-MYKOS-API-Key':admin_key}).status_code==400
    schema=anonymous.get('/api/v2/openapi.json').json()
    assert schema['paths']['/api/v2/nodes/{shop}/snapshots/{identifier}']['delete']['security']==[{'MYKOSApiKey':[]}]


def test_scheduled_commands_require_admin_key_even_for_results(environment,monkeypatch):
    calls=[]
    monkeypatch.setattr(container_admin,'invoke',lambda *args:calls.append(args) or {'id':args[1] if len(args)>1 else 'all','state':'pending'})
    client=TestClient(app.app,base_url='https://testserver')
    reader=mykos_api_keys.create('job-reader','read')
    admin=mykos_api_keys.create('job-admin','admin')
    body={'target':'01234567','command':'Get-Date','run_at':'2026-10-25T12:00:00-05:00'}
    for method,url,payload in [('put','/api/v2/commands/example',{'json':body}),('get','/api/v2/commands',{}),
                               ('get','/api/v2/commands/example',{}),('post','/api/v2/commands/example/cancel',{})]:
        assert getattr(client,method)(url,**payload).status_code==401
        assert getattr(client,method)(url,headers={'X-MYKOS-API-Key':reader},**payload).status_code==403
        assert getattr(client,method)(url,headers={'X-MYKOS-API-Key':admin},**payload).status_code==200
    assert calls[0][0]=='command-put' and json.loads(calls[0][2])['command']=='Get-Date'
    assert login('operator','operator-password-123').put('/api/v2/commands/cookie',json=body).status_code==401
    monkeypatch.setattr(container_admin,'invoke',lambda *a:{'queue_error':'Already claimed','status_code':409})
    assert client.put('/api/v2/commands/example',headers={'X-MYKOS-API-Key':admin},json=body).status_code==409
    assert client.put('/api/v2/commands/bad.name',headers={'X-MYKOS-API-Key':admin},json=body).status_code==400
    assert '/api/v2/commands/{command_id}' in client.get('/api/v2/openapi.json').json()['paths']


def test_delete_container_requires_admin_origin_and_exact_id(environment,monkeypatch):
    calls=[]
    monkeypatch.setattr(container_admin,'invoke',lambda *args:calls.append(args) or {'state':'queued'})
    client=TestClient(app.app,base_url='https://testserver')
    reader=login('reader','reader-password-123');operator=login('operator','operator-password-123')
    read_key=mykos_api_keys.create('container-delete-reader','read')
    admin_key=mykos_api_keys.create('container-delete-admin','admin')
    browser='/api/web/containers/01234567';api='/api/v2/nodes/01234567'
    body={'confirmation':'01234567'};origin={'Origin':'https://testserver'}
    assert client.request('DELETE',browser,json=body,headers=origin).status_code==401
    assert reader.request('DELETE',browser,json=body,headers=origin).status_code==403
    assert operator.request('DELETE',browser,json=body).status_code==403
    assert operator.request('DELETE',browser,json=body,headers={'Origin':'https://evil.test'}).status_code==403
    assert operator.request('DELETE',browser,json={'confirmation':'76543210'},headers=origin).status_code==400
    assert operator.request('DELETE',browser,json=body,headers=origin).status_code==200
    assert client.request('DELETE',api,json=body).status_code==401
    assert client.request('DELETE',api,json=body,headers={'X-MYKOS-API-Key':read_key}).status_code==403
    assert client.request('DELETE',api,json={'confirmation':'76543210'},headers={'X-MYKOS-API-Key':admin_key}).status_code==400
    assert client.request('DELETE',api,json=body,headers={'X-MYKOS-API-Key':admin_key}).status_code==200
    assert calls==[('delete-pos','01234567','01234567')]*2
    assert client.request('DELETE',api,json={},headers={'X-MYKOS-API-Key':admin_key}).status_code==422
    schema=client.get('/api/v2/openapi.json').json()
    assert schema['paths']['/api/v2/nodes/{shop}']['delete']['security']==[{'MYKOSApiKey':[]}]


def test_purge_api_is_admin_only_and_browser_requires_origin(environment,monkeypatch):
    calls=[]
    monkeypatch.setattr(container_admin,'invoke',lambda *args:calls.append(args) or {'state':'queued','confirmation':'PURGE testserver'})
    client=TestClient(app.app,base_url='https://testserver')
    reader=login('reader','reader-password-123');operator=login('operator','operator-password-123')
    read_key=mykos_api_keys.create('purge-reader','read');admin_key=mykos_api_keys.create('purge-admin','admin')
    body={'confirmation':'PURGE testserver'};origin={'Origin':'https://testserver'}
    for path in ['/api/web/system/purge','/api/web/system/purge/status']:
        assert client.get(path).status_code==401
        assert reader.get(path).status_code==403
        assert operator.get(path).status_code==200
    assert client.post('/api/web/system/purge',headers=origin,json=body).status_code==401
    assert reader.post('/api/web/system/purge',headers=origin,json=body).status_code==403
    assert operator.post('/api/web/system/purge',json=body).status_code==403
    assert operator.post('/api/web/system/purge',headers={'Origin':'https://wrong.test'},json=body).status_code==403
    assert operator.post('/api/web/system/purge',headers=origin,json=body).status_code==200
    assert calls[-1]==('purge-start','PURGE testserver')
    for path in ['/api/v2/system/purge','/api/v2/system/purge/status']:
        assert client.get(path).status_code==401
        assert client.get(path,headers={'X-MYKOS-API-Key':read_key}).status_code==403
        assert client.get(path,headers={'X-MYKOS-API-Key':admin_key}).status_code==200
    assert client.post('/api/v2/system/purge',json=body).status_code==401
    assert client.post('/api/v2/system/purge',headers={'X-MYKOS-API-Key':read_key},json=body).status_code==403
    assert client.post('/api/v2/system/purge',headers={'X-MYKOS-API-Key':admin_key},json=body).status_code==200
    assert calls[-1]==('purge-start','PURGE testserver')
    for body in [{},{'confirmation':''},{'confirmation':'PURGE testserver','path':'/etc'}]:
        assert client.post('/api/v2/system/purge',headers={'X-MYKOS-API-Key':admin_key},json=body).status_code==422
    schema=client.get('/api/v2/openapi.json').json()
    assert schema['paths']['/api/v2/system/purge']['post']['security']==[{'MYKOSApiKey':[]}]


def test_desktop_proxy_http_auth_assets_and_public_websocket_settings(environment,monkeypatch):
    import desktop_proxy
    calls=[]
    monkeypatch.setattr(container_admin,'invoke',lambda *a:calls.append(a) or {'host':'127.0.0.1','port':18006})
    requests=[]
    def asset(*a):
        requests.append(a)
        return 200,'application/json',b'{"autoconnect":true,"host":"dev.example","port":"18006"}'
    monkeypatch.setattr(desktop_proxy,'fetch',asset)
    client=TestClient(app.app,base_url='https://testserver')
    reader=login('reader','reader-password-123');operator=login('operator','operator-password-123')
    url='/console/ubuntu24.04/mandatory.json'
    assert client.get(url).status_code==401
    assert reader.get(url).status_code==403
    response=operator.get(url)
    assert response.status_code==200
    settings=response.json()
    assert settings['host']=='testserver' and settings['port']=='443' and settings['encrypt'] is True
    assert settings['path']=='/mykos/console/ubuntu24.04/websockify'
    assert calls[-1]==('console-target','ubuntu24.04')
    assert requests[-1]==('127.0.0.1',18006,'mandatory.json','','GET')
    assert response.headers['cache-control']=='no-store'
    read_key=mykos_api_keys.create('console-read','read');key=mykos_api_keys.create('console-admin','admin')
    api='/api/v2/console/01234567/mandatory.json'
    assert client.get(api).status_code==401
    assert client.get(api,headers={'X-MYKOS-API-Key':read_key}).status_code==403
    response=client.get(api,headers={'X-MYKOS-API-Key':key})
    assert response.status_code==200 and response.json()['path']=='/mykos/api/v2/console/01234567/websockify'
    assert calls[-1]==('console-target','01234567')
    before=len(calls)
    assert operator.get('/console/not-managed/app/ui.js').status_code==400
    assert len(calls)==before


def test_desktop_proxy_websocket_auth_binary_bridge_and_logout(environment,monkeypatch):
    import desktop_proxy,asyncio
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(desktop_proxy,'AUTH_INTERVAL',.01)
    monkeypatch.setattr(container_admin,'invoke',lambda *a:{'host':'127.0.0.1','port':18006})
    connections=[]
    class Upstream:
        subprotocol=None
        async def __aenter__(self):
            self.messages=asyncio.Queue();await self.messages.put(b'RFB 003.008\n');return self
        async def __aexit__(self,*args):pass
        def __aiter__(self):return self
        async def __anext__(self):return await self.messages.get()
        async def send(self,data):await self.messages.put(data)
    def connect(*a,**kw):connections.append((a,kw));return Upstream()
    monkeypatch.setattr(desktop_proxy,'connect',connect)
    client=TestClient(app.app,base_url='https://testserver')
    reader=login('reader','reader-password-123');operator=login('operator','operator-password-123')
    url='wss://testserver/console/ubuntu24.04/websockify';origin={'Origin':'https://testserver'}
    for who,headers in [(client,origin),(reader,origin),(operator,{'Origin':'https://evil.test'})]:
        with pytest.raises(WebSocketDisconnect):
            with who.websocket_connect(url,headers=headers):pass
    assert not connections
    with operator.websocket_connect(url,headers=origin) as ws:
        assert ws.receive_bytes()==b'RFB 003.008\n'
        ws.send_bytes(b'client frame');assert ws.receive_bytes()==b'client frame'
        assert connections[-1][0]==('ws://127.0.0.1:18006/websockify',)
        assert connections[-1][1]['proxy'] is None
        with mykos_users.connect() as db:db.execute("DELETE FROM sessions WHERE name='operator'")
        with pytest.raises(WebSocketDisconnect):ws.receive_bytes()
    key=mykos_api_keys.create('console-ws-admin','admin')
    with client.websocket_connect('/api/v2/console/01234567/websockify',headers={'X-MYKOS-API-Key':key}) as ws:
        assert ws.receive_bytes()==b'RFB 003.008\n'



def test_balloon_api_is_admin_only_and_validates_targets(environment):
    client=TestClient(app.app,base_url='https://testserver')
    read_key=mykos_api_keys.create('memory-reader','read')
    admin_key=mykos_api_keys.create('memory-admin','admin')
    path='/api/v2/nodes/01234567/memory'
    assert client.get(path).status_code==401
    assert client.get(path,headers={'X-MYKOS-API-Key':read_key}).status_code==403
    assert client.put(path,headers={'X-MYKOS-API-Key':read_key},json={'target_mib':2048}).status_code==403
    headers={'X-MYKOS-API-Key':admin_key}
    assert client.get(path,headers=headers).status_code==200
    assert environment[1][-1]==('memory-info','01234567')
    assert client.put(path,headers=headers,json={'target_mib':2048}).status_code==200
    assert environment[1][-1]==('memory-set','01234567','2048')
    for target in (2047,4097):assert client.put(path,headers=headers,json={'target_mib':target}).status_code==400
    for target in (True,'3968'):assert client.put(path,headers=headers,json={'target_mib':target}).status_code==422



def test_application_monitoring_fields_are_removed_even_from_old_caches(environment):
    base,calls=environment
    now=int(time.time())
    (base/'data/details.json').write_text(json.dumps({'collected_at':now,'details':{'01234567':{'image':'localhost/rh8:latest','rti_version':'old-app-version'}}}))
    (base/'data/extended.json').write_text(json.dumps({'collected_at':now,'nodes':{'01234567':{'running':True,'rti':{'version':'old-app-version'},'resources':{'cpu':'1%'}}}}))
    client=TestClient(app.app,base_url='https://testserver')
    headers={'X-MYKOS-API-Key':mykos_api_keys.create('generic-monitoring','read')}
    node=client.get('/api/v2/nodes/01234567',headers=headers).json()
    assert 'rti' not in node and 'rti' not in node['extended']
    assert 'rti_version' not in node['details']
    assert node['resources']=={'cpu':'1%'}
    assert client.get('/api/v2/nodes/01234567/rti',headers=headers).status_code==404
    assert client.get('/api/v2/nodes?include=rti',headers=headers).status_code==400
    browser=login('operator','operator-password-123')
    assert 'rti_version' not in browser.get('/api/nodes/01234567/details').json()['details']
    assert 'rti' not in browser.get('/api/web/extended').json()['nodes']['01234567']
    assert 'rti_version' not in monitor_api.snapshot()['nodes'][0]['details']


def test_cold_memory_allocation_requires_admin_and_exact_confirmation(environment):
    client=TestClient(app.app,base_url='https://testserver')
    path='/api/v2/nodes/01234567/memory/allocation'
    headers={'X-MYKOS-API-Key':mykos_api_keys.create('cold-memory-admin','admin')}
    read={'X-MYKOS-API-Key':mykos_api_keys.create('cold-memory-read','read')}
    body={'target_mib':2048,'confirmation':'01234567'}
    assert client.put(path,json=body).status_code==401
    assert client.put(path,headers=read,json=body).status_code==403
    assert client.put(path,headers=headers,json={**body,'confirmation':'87654321'}).status_code==400
    assert client.put(path,headers=headers,json=body).status_code==200
    assert environment[1][-1]==('memory-configure','01234567','2048','01234567')


def test_ubuntu_vpn_configuration_requires_admin_key_and_keeps_secret_out_of_response(environment):
    base,calls=environment
    client=TestClient(app.app,base_url='https://testserver')
    body={'name':'office','peer':'vpn.example.com','remote':'10.20.30.0/24','secret':'private-test-psk'}
    url='/api/v2/nodes/01234567/vpn'
    assert client.put(url,json=body).status_code==401
    read=mykos_api_keys.create('vpn-read','read')
    assert client.put(url,headers={'X-MYKOS-API-Key':read},json=body).status_code==403
    admin=mykos_api_keys.create('vpn-admin','admin')
    result=client.put(url,headers={'X-MYKOS-API-Key':admin},json=body)
    assert result.status_code==200 and 'private-test-psk' not in result.text
    assert calls[-1][:2]==('ubuntu-vpn-add','01234567')
    assert json.loads(calls[-1][2])['secret']=='private-test-psk'


def test_image_builds_require_admin_keys_and_are_in_schema(environment,monkeypatch):
    import image_build_api
    calls=[]
    monkeypatch.setattr(image_build_api,'invoke',lambda *args:calls.append(args) or {'state':'queued'})
    client=TestClient(app.app,base_url='https://testserver')
    path='/api/v2/images/win10/build'
    assert client.put(path,json={}).status_code==401
    reader=mykos_api_keys.create('image-read','read')
    assert client.put(path,headers={'X-MYKOS-API-Key':reader},json={}).status_code==403
    assert client.get(path,headers={'X-MYKOS-API-Key':reader}).status_code==200
    admin=mykos_api_keys.create('image-admin','admin')
    response=client.put(path,headers={'X-MYKOS-API-Key':admin},json={'replace':True})
    assert response.status_code==200
    assert calls[-1][0:2]==('image-build-queue','win10')
    assert 'password' not in response.text
    assert client.put('/api/v2/images/bad/build',headers={'X-MYKOS-API-Key':admin},json={}).status_code==400
    assert client.post('/api/image-build/mac15/complete').status_code==403
    schema=client.get('/api/v2/openapi.json').json()
    assert '/api/v2/images/{os_name}/build' in schema['paths']


def test_host_history_requires_browser_session_or_api_key(environment, monkeypatch):
    import host_history
    base, _ = environment
    monkeypatch.setattr(host_history, 'DATABASE', base / 'data/history.sqlite3')
    monkeypatch.setattr(host_history, 'LIVE', base / 'data/latest.json')
    host_history.record({'collected_at': int(time.time()), 'cpu_percent': 12.5})
    client = TestClient(app.app, base_url='https://testserver')
    assert client.get('/api/web/host-history').status_code == 401
    assert client.get('/api/v2/host/history').status_code == 401
    reader = login('reader', 'reader-password-123')
    response = reader.get('/api/web/host-history')
    assert response.status_code == 200
    assert response.json()['retention_seconds'] == 1800
    assert response.json()['samples'][0]['cpu_percent'] == 12.5
    assert reader.get('/api/v2/host/history').status_code == 401
    headers = {'X-MYKOS-API-Key': mykos_api_keys.create('history', 'read')}
    assert client.get('/api/v2/host/history', headers=headers).status_code == 200
    assert client.get('/api/v2/host/history?since=-1', headers=headers).status_code == 400


def test_personal_notes_private_separate_and_revisioned(environment):
    anonymous=TestClient(app.app,base_url='https://testserver')
    paths=('/api/web/personal-notes','/api/v2/personal-notes')
    for path in paths:
        assert anonymous.get(path).status_code==401
        for name in ('reader','operator'):
            client=login(name,name+'-password-123')
            assert client.get(path).status_code==403
            assert client.put(path,headers={'Origin':'https://testserver'},json={'text':'denied','revision':0}).status_code==403
        key=mykos_api_keys.create('not-personal-owner','admin')
        assert anonymous.get(path,headers={'X-MYKOS-API-Key':key}).status_code==401
    mykos_users.set_password('mgreen','mgreen-test-password-123',role='admin',create=True)
    owner=login('mgreen','mgreen-test-password-123')
    payload={'text':'Personal text, never a change request','revision':0}
    assert owner.put(paths[0],json=payload).status_code==403
    assert owner.put(paths[0],headers={'Origin':'https://testserver'},json=payload).status_code==200
    assert owner.get(paths[1]).json()['text']==payload['text']
    assert owner.get('/api/web/notes/host').json()['text']==''
    assert owner.put(paths[1],headers={'Origin':'https://testserver'},json=payload).status_code==409
    assert owner.put('/api/v2/notes/host',headers={'Origin':'https://testserver'},json={'text':'change request','revision':0}).status_code==200
    assert owner.get(paths[0]).json()['text']==payload['text']
    with mykos_users.connect() as c:
        assert c.execute('SELECT text FROM pane_notes').fetchall()==[('change request',)]
        c.execute('DELETE FROM pane_notes')
    assert owner.get(paths[1]).json()['text']==payload['text']
    assert owner.put(paths[0],headers={'Origin':'https://testserver'},json={'text':'x'*20001,'revision':1}).status_code==422


def test_prepared_image_removal_requires_admin_and_exact_os(environment,monkeypatch):
    import image_build_api
    calls=[];monkeypatch.setattr(image_build_api,'invoke',lambda *a:calls.append(a) or {'state':'queued'})
    client=TestClient(app.app,base_url='https://testserver')
    path='/api/v2/images/rocky9';body={'confirmation':'rocky9'}
    assert client.request('DELETE',path,json=body).status_code==401
    reader=mykos_api_keys.create('image-removal-reader','read')
    assert client.request('DELETE',path,json=body,headers={'X-MYKOS-API-Key':reader}).status_code==403
    admin=mykos_api_keys.create('image-removal-admin','admin');headers={'X-MYKOS-API-Key':admin}
    assert client.request('DELETE',path,json=body,headers=headers).status_code==200
    assert calls[-1]==('image-remove','rocky9','rocky9')
    assert client.get(path+'/delete',headers=headers).status_code==200
    for os_name in ('centos7','fedora45','rocky7','alma11'):
        assert client.request('DELETE','/api/v2/images/'+os_name,json={'confirmation':os_name},headers=headers).status_code==400
    schema=client.get('/api/v2/openapi.json').json()
    assert 'delete' in schema['paths']['/api/v2/images/{os_name}']


def test_image_terminal_requires_admin_valid_os_and_origin(environment,monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    calls=[]
    async def bridge(ws,shop,action='shell'):
        calls.append((shop,action));await ws.accept();await ws.send_text('base shell');await ws.close()
    monkeypatch.setattr(app,'mykos_terminal_bridge',bridge)
    reader=login('reader','reader-password-123');admin=login('operator','operator-password-123')
    url='wss://testserver/api/terminal/rh9?action=image-shell'
    with admin.websocket_connect(url,headers={'Origin':'https://testserver'}) as ws:
        assert ws.receive_text()=='base shell'
    for client,path,origin in [(reader,url,'https://testserver'),(admin,url,'https://wrong.example'),
                               (admin,url.replace('rh9','host'),'https://testserver'),
                               (admin,url.replace('rh9','12345678'),'https://testserver'),
                               (admin,url.replace('?action=image-shell',''),'https://testserver')]:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path,headers={'Origin':origin}):pass
    key=mykos_api_keys.create('image-console','admin')
    with admin.websocket_connect('/api/v2/terminal/rh9?action=image-shell',headers={'X-MYKOS-API-Key':key}) as ws:
        assert ws.receive_text()=='base shell'
    assert calls==[('rh9','image-shell'),('rh9','image-shell')]


def test_base_reset_requires_administrator_and_browser_origin(environment,monkeypatch):
    import image_build_api
    calls=[];monkeypatch.setattr(image_build_api,'invoke',lambda *a:calls.append(a) or {'state':'queued'})
    reader=login('reader','reader-password-123');admin=login('operator','operator-password-123')
    path='/api/web/images/rh9/reset';body={'confirmation':'rh9'}
    assert reader.post(path,json=body,headers={'Origin':'https://testserver'}).status_code==403
    assert admin.post(path,json=body,headers={'Origin':'https://wrong.example'}).status_code==403
    assert admin.post(path,json=body,headers={'Origin':'https://testserver'}).status_code==200
    key=mykos_api_keys.create('base-reset','admin')
    assert admin.post('/api/v2/images/rh9/reset',json=body,headers={'X-MYKOS-API-Key':key}).status_code==200
    assert calls==[('image-reset','rh9','rh9'),('image-reset','rh9','rh9')]


def test_base_console_reports_busy_message_without_creating_a_second_session(environment,monkeypatch):
    class Reader:
        async def readline(self):return b'MYKOS_TERMINAL_ERROR This base image already has an open console\n'
    class Writer:
        def write(self,data):pass
        async def drain(self):pass
        def close(self):pass
        async def wait_closed(self):pass
    async def connect(*args,**kwargs):return Reader(),Writer()
    monkeypatch.setattr(app._mykos_asyncio,'open_unix_connection',connect)
    admin=login('operator','operator-password-123')
    with admin.websocket_connect('wss://testserver/api/terminal/rh9?action=image-shell',headers={'Origin':'https://testserver'}) as ws:
        assert 'already has an open console' in ws.receive_text()
        assert ws.receive()['code']==1013


def test_network_draft_gateway_api_requires_admin_and_exact_confirmation(environment):
    _,calls=environment
    client=TestClient(app.app,base_url='https://testserver')
    path='/api/v2/nodes/01234567/vpn/gateway'
    data={'connection':'office','remote':'10.77.0.0/24','confirmation':'01234567'}
    assert client.put(path,json=data).status_code==401
    reader={'X-MYKOS-API-Key':mykos_api_keys.create('gateway-read','read')}
    admin={'X-MYKOS-API-Key':mykos_api_keys.create('gateway-admin','admin')}
    assert client.put(path,headers=reader,json=data).status_code==403
    assert client.delete(path+'/office',headers=reader).status_code==403
    # No root mutations before authentication and confirmation checks pass.
    assert not any(x[0].startswith('vpn-gateway') for x in calls)
    assert client.put(path,headers=admin,json={**data,'confirmation':'wrong'}).status_code==400
    assert client.put(path,headers=admin,json=data).status_code==200
    assert calls[-1]==('vpn-gateway-configure','01234567','office','10.77.0.0/24')
    assert client.delete(path+'/office',headers=admin).status_code==200
    assert calls[-1]==('vpn-gateway-remove','01234567','office')


def test_standalone_terminal_windows_share_login_and_validate_targets(environment):
    import re
    client=TestClient(app.app,base_url='https://testserver')
    assert client.get('/api/terminal/window/01234567').status_code==401
    reader=login('reader','reader-password-123')
    assert reader.get('/api/terminal/window/01234567').status_code==403
    admin=login('operator','operator-password-123')
    for target in ('01234567','87654321','host'):
        response=admin.get('/api/terminal/window/'+target)
        assert response.status_code==200
        data=json.loads(re.search(r'<script id="terminal-config" type="application/json">(.*?)</script>',response.text).group(1))
        assert data['shop']==target and data['username']=='operator'
        assert admin.cookies.get('mykos_session') not in response.text
        assert response.headers['cache-control']=='no-store'
    assert admin.get('/api/terminal/window/not-an-id').status_code==400
    assert admin.get('/api/terminal/window/01234567?action=add-vpn').status_code==400
    assert admin.get('/api/terminal/window/not-an-os?action=image-shell').status_code==400
    assert admin.get('/api/terminal/window/rh9?action=image-shell').status_code==200
    assert admin.get('/api/terminal/permission?include_user=true').json()=={'allowed':True,'username':'operator'}
    admin.post('/api/logout',headers={'Origin':'https://testserver'})
    assert admin.get('/api/terminal/window/01234567').status_code==401


def test_two_terminal_connections_remain_independent_with_one_login(environment,monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    async def bridge(ws,shop,action='shell'):
        await ws.accept();await ws.send_text(shop)
        try:
            while True:await ws.send_text(shop+':'+await ws.receive_text())
        except WebSocketDisconnect:pass
    monkeypatch.setattr(app,'mykos_terminal_bridge',bridge)
    admin=login('operator','operator-password-123')
    with admin.websocket_connect('wss://testserver/api/terminal/01234567',headers={'Origin':'https://testserver'}) as first:
        assert first.receive_text()=='01234567'
        with admin.websocket_connect('wss://testserver/api/terminal/87654321',headers={'Origin':'https://testserver'}) as second:
            assert second.receive_text()=='87654321'
            first.send_text('one');second.send_text('two')
            assert first.receive_text()=='01234567:one'
            assert second.receive_text()=='87654321:two'
        first.send_text('still-open');assert first.receive_text()=='01234567:still-open'


def test_geography_read_api_retains_optional_metadata_without_host_secrets(environment):
    base,calls=environment
    (base/'inventory.json').write_text(json.dumps({'nodes':[{'id':'01234567','name':'Store','shop_name':'Flower shop','pos_type':'rti','lat':41,'lon':-90,'app_info':{'version':'example'}}],
        'hosts':[{'hostname':'host.example','lat':40,'lon':-91,'private_setting':'not-a-map-field'}]}))
    client=TestClient(app.app,base_url='https://testserver')
    assert client.get('/api/v2/geography').status_code==401
    key=mykos_api_keys.create('map-reader','read')
    response=client.get('/api/v2/geography',headers={'X-MYKOS-API-Key':key})
    assert response.status_code==200
    value=response.json();assert value['nodes'][0]['pos_type']=='rti'
    assert value['nodes'][0]['shop_name']=='Flower shop'
    assert value['hosts']==[{'hostname':'host.example','lat':40,'lon':-91}]
    assert calls==[]


def test_shared_customer_notes_readers_edit_and_revision_conflicts(environment):
    base,calls=environment
    anonymous=TestClient(app.app,base_url='https://testserver')
    path='/api/web/id-notes/01234567'
    assert anonymous.get(path).status_code==401
    reader=login('reader','reader-password-123')
    admin=login('operator','operator-password-123')
    assert reader.get(path).json()['revision']==0
    update={'text':'Customer-specific setup <script> is plain text','revision':0}
    assert reader.put(path,json=update).status_code==403
    assert reader.put(path,headers={'Origin':'https://evil.example'},json=update).status_code==403
    saved=reader.put(path,headers={'Origin':'https://testserver'},json=update)
    assert saved.status_code==200,saved.text
    assert saved.json()['updated_by']=='reader' and saved.json()['revision']==1
    assert admin.get(path).json()['text']==update['text']
    assert admin.put(path,headers={'Origin':'https://testserver'},json={'text':'stale overwrite','revision':0}).status_code==409
    assert admin.get(path).json()['text']==update['text']
    assert reader.get('/api/web/id-notes/87654321').json()['text']==''
    for target in ('abc','123','123456789','１２３４５６７８'):
        assert reader.get('/api/web/id-notes/'+target).status_code==422
    assert reader.put(path,headers={'Origin':'https://testserver'},json={'text':'x'*20001,'revision':1}).status_code==422
    key=mykos_api_keys.create('notes-read','read')
    assert anonymous.get(path,headers={'X-MYKOS-API-Key':key}).status_code==401
    with mykos_users.connect() as c:
        assert c.execute('SELECT text FROM shared_id_notes WHERE shop=?',('01234567',)).fetchone()[0]==update['text']
    assert calls==[]


def test_full_access_account_can_preview_every_os_pane(environment):
    reader=login('reader','reader-password-123')
    admin=login('operator','operator-password-123')
    assert reader.get('/api/web/current-user').json()=={'username':'reader','preview_all_panes':False}
    assert admin.get('/api/web/current-user').json()=={'username':'operator','preview_all_panes':True}
