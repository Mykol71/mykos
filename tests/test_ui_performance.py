"""Exercise real HTTP boundaries and served content-addressed resources."""
import json,re,time,gzip
from pathlib import Path
from fastapi.testclient import TestClient
import app
from test_api_identity import environment,login

def test_bulk_inventory_auth_and_public_field_boundary(environment):
    base,_=environment; now=int(time.time())
    (base/'data/details.json').write_text(json.dumps({'collected_at':now,'details':{'01234567':{'os_type':'Ubuntu','os_version':'24.04','image':'localhost/ubuntu24:latest','rti_secret':'private'},'99999999':{'os_type':'Not in inventory'}}}))
    (base/'data/extended.json').write_text(json.dumps({'collected_at':now,'nodes':{'01234567':{'resources':{'memory':'1 GiB / 2 GiB'}},'99999999':{'hidden':True}}}))
    client=TestClient(app.app,base_url='https://testserver')
    assert client.get('/api/web/inventory').status_code==401
    assert client.get('/api/web/inventory/01234567').status_code==401
    reader=login('reader','reader-password-123')
    response=reader.get('/api/web/inventory');assert response.status_code==200
    value=response.json();assert response.headers['cache-control']=='no-store'
    assert set(value['details'])=={'01234567'} and set(value['extended'])=={'01234567'}
    assert value['details']['01234567']['os_type']=='Ubuntu'
    if 'rti_secret' not in app.generic_details({'details':{'x':{'rti_secret':'private'}}})['details']['x']:
        assert 'rti_secret' not in value['details']['01234567']
    assert reader.get('/api/web/inventory').json()['revision']==value['revision']
    assert reader.get('/api/web/inventory/01234567').json()['node']['id']=='01234567'
    assert reader.get('/api/web/inventory/99999999').json()['node'] is None
    assert reader.get('/api/web/inventory/not-a-shop').status_code==400
    (base/'data/details.json').write_text(json.dumps({'collected_at':now-200,'details':value['details']}))
    stale=reader.get('/api/web/inventory').json();assert not stale['collection']['details']['fresh']
    assert stale['details']['01234567']['os_type']=='Ubuntu'
    (base/'data/details.json').write_text('broken')
    broken=reader.get('/api/web/inventory').json();assert broken['details']=={} and not broken['collection']['details']['fresh']

def test_served_shell_static_cache_compression_and_scope(monkeypatch):
    from dashboard_assets import encoded
    web=Path(app.__file__).parent;monkeypatch.setattr(app,'BASE',web)
    client=TestClient(app.app,base_url='https://testserver')
    shell=client.get('/',headers={'Accept-Encoding':'gzip'})
    assert shell.status_code==200 and shell.headers['cache-control']=='no-store'
    assert shell.headers['content-encoding']=='gzip'
    urls=re.findall(r'(?:src|href)="(/mykos/assets/[a-f0-9]{64}\.(?:js|css))"',shell.text)
    assert len(urls)>=12
    for url in set(urls):
        response=client.get(url.removeprefix('/mykos'),headers={'Accept-Encoding':'gzip'})
        assert response.status_code==200
        assert response.headers['cache-control']=='public, max-age=31536000, immutable'
        assert response.headers['content-encoding']=='gzip'
        assert response.headers['vary']=='Accept-Encoding'
        import hashlib
        assert hashlib.sha256(response.content).hexdigest()==url.rsplit('/',1)[1].split('.')[0]
    assert client.get('/assets/'+'0'*64+'.js').status_code==404
    assert client.get('/assets/auth.db').status_code==404
    assert client.get('/assets/../../data/auth.db').status_code==404
    raw=client.get('/',headers={'Accept-Encoding':'gzip;q=0, identity;q=1'})
    assert 'content-encoding' not in raw.headers
    assert raw.content==shell.content
    # Peer views call page() directly before inserting their own bootstrap.
    assert b'content-encoding' not in app.page().body and b'/mykos/assets/' in app.page().body
    assert len(shell.content)<200000
