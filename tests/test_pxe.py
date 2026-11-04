"""PXE jobs must never stage an unknown target or expose account credentials."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

WEB=Path(__file__).resolve().parents[1]/'services/web'
sys.path.insert(0,str(WEB))
import pxe_store as store
import pxe_api

MAC='52-54-00-24-24-01'

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'ROOT',tmp_path/'pxe')
    store.configure('https://stage.example.com',['192.0.2.0/24'])
    for name in ('boot.wim','install.wim','wimboot','curl.exe','curl-ca-bundle.crt','mykos-pe-guard.exe','chrome.msi'):
        path=tmp_path/name;path.write_bytes(('Fixture '+name).encode());store.register_asset(path,name)
    app=FastAPI();app.include_router(pxe_api.router)
    client=TestClient(app,base_url='https://stage.example.com',client=('192.0.2.11',54321))
    return tmp_path,client


def arm(**kwargs):
    return store.enroll(MAC,'00000000','windows11','Test-account-password',**kwargs)


def test_unknown_mac_and_network_cannot_claim(env):
    _,client=env;job=arm()
    assert client.get('/pxe/select.ipxe',params={'mac':'00-11-22-33-44-55'}).status_code==404
    outsider=TestClient(client.app,base_url='https://stage.example.com',client=('203.0.113.1',1))
    assert outsider.get('/pxe/select.ipxe',params={'mac':MAC}).status_code==403
    assert client.get('/pxe/select.ipxe',params={'mac':MAC}).status_code==200
    with pytest.raises(ValueError,match='active job'):arm()


def test_private_ticket_only_in_bootstrap_body_and_bound_to_claim(env):
    _,client=env;job=arm();response=client.get('/pxe/select.ipxe',params={'mac':MAC})
    assert response.status_code==200
    assert 'Test-account-password' not in response.text and 'TICKET=' not in response.text
    assert 'wimboot index=2' in response.text
    assert client.get('/pxe/jobs/'+job['id']+'/answer.xml').status_code==403
    bootstrap=client.get('/pxe/bootstrap.cmd',params={'mac':MAC})
    assert bootstrap.status_code==200 and 'Cache-Control' in bootstrap.headers
    with store.connect() as c:row=dict(c.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())
    ticket=row['ticket'];headers={'X-MYKOS-PXE-Ticket':ticket}
    answer=client.get('/pxe/jobs/'+job['id']+'/answer.xml',headers=headers)
    assert answer.status_code==200
    root=ET.fromstring(answer.content);ns={'u':'urn:schemas-microsoft-com:unattend'}
    assert root.find('.//u:Name',ns).text=='mykos'
    assert root.find('.//u:JoinWorkgroup',ns).text=='WORKGROUP'
    assert 'Test-account-password' not in answer.text
    assert 'value="false"' not in answer.text
    other=TestClient(client.app,base_url='https://stage.example.com',client=('192.0.2.12',1))
    assert other.get('/pxe/jobs/'+job['id']+'/answer.xml',headers=headers).status_code==403
    plain=TestClient(client.app,base_url='http://stage.example.com',client=('192.0.2.11',1))
    assert plain.get('/pxe/bootstrap.cmd',params={'mac':MAC}).status_code==403
    assert plain.get('/pxe/jobs/'+job['id']+'/answer.xml',headers=headers).status_code==403
    assert ticket not in json.dumps(store.jobs()) and 'password' not in json.dumps(store.jobs())


def test_job_state_disarms_disk_erase_and_cleans_password(env):
    _,client=env;job=arm();row=store.claim(MAC,'192.0.2.11');headers={'X-MYKOS-PXE-Ticket':row['ticket']};url='/pxe/jobs/'+job['id']
    assert client.post(url+'/status',headers=headers,json={'state':'complete'}).status_code==409
    assert client.post(url+'/status',headers=headers,json={'state':'applying'}).status_code==200
    assert client.get('/pxe/select.ipxe',params={'mac':MAC}).status_code==404
    assert client.post(url+'/status',headers=headers,json={'state':'applying'}).status_code==409
    assert client.post(url+'/status',headers=headers,json={'state':'firstboot'}).status_code==200
    assert client.get(url+'/answer.xml',headers=headers).status_code==410
    assert client.post(url+'/status',headers=headers,json={'state':'complete'}).status_code==200
    assert client.get(url+'/stage.ps1',headers=headers).status_code==403
    with store.connect() as c:row=dict(c.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())
    assert row['ticket']=='' and 'password' not in json.loads(row['config'])


def test_expiry_revoke_and_wrong_asset_revision(env):
    _,client=env;job=arm();store.claim(MAC,'192.0.2.11')
    with store.connect() as c:c.execute('UPDATE jobs SET expires=? WHERE id=?',(int(time.time())-1,job['id']))
    assert client.get('/pxe/select.ipxe',params={'mac':MAC}).status_code==404
    second=arm();store.revoke(second['id'])
    assert client.get('/pxe/select.ipxe',params={'mac':MAC}).status_code==404
    assert client.get('/pxe/assets/'+'0'*64+'/boot.wim').status_code==404
    assert client.get('/pxe/assets/'+'0'*64+'/..%2Fsettings.json').status_code==404


def test_cli_uses_explicit_wipe_and_no_password_arguments(env):
    _,client=env;job=arm();script=client.get('/pxe/bootstrap.cmd',params={'mac':MAC}).text
    assert 'mykos-pe-guard.exe 52-54-00-24-24-01 68719476736 1' in script
    assert script.index('mykos-pe-guard.exe')<script.index('echo clean')
    assert script.index('applying')<script.index('echo clean')
    assert 'Test-account-password' not in script
    source=(WEB.parents[1]/'services/helpers/mykos-pxe').read_text()
    assert '--password-stdin' in source and 'getpass.getpass' in source


def test_unconfigured_feature_is_disabled(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'ROOT',tmp_path/'disabled')
    app=FastAPI();app.include_router(pxe_api.router);client=TestClient(app)
    assert client.get('/pxe/autoexec.ipxe').status_code==503
    assert not store.ROOT.exists()


def test_media_cannot_change_under_an_active_install(env):
    tmp,client=env;job=arm()
    original=store.asset('install.wim')
    replacement=tmp/'new-image.wim';replacement.write_bytes(b'new installed OS')
    with pytest.raises(ValueError,match='active staging jobs'):
        store.register_asset(replacement,'install.wim')
    assert store.asset('install.wim')==original
    store.revoke(job['id'])
    assert store.register_asset(replacement,'install.wim')['sha256']!=original['sha256']


def test_staging_origin_cannot_change_under_an_active_install(env):
    tmp,client=env;job=arm()
    with pytest.raises(ValueError,match='active staging jobs'):
        store.configure('https://other.example.com',['192.0.2.0/24'])
    assert store.settings()['origin']=='https://stage.example.com'
    store.revoke(job['id'])
    assert store.configure('https://other.example.com',['192.0.2.0/24'])['origin']=='https://other.example.com'


@pytest.mark.parametrize('name,kind,build,arch,valid',[
    ('Windows 11 Pro','Client',26300,9,True),
    ('tiny11','Client',26200,9,True),
    ('Windows 10 Pro','Client',19045,9,False),
    ('Windows Server 2025','Server',26100,9,False),
    ('Windows 11 Pro','Client',26300,12,False),
])
def test_media_import_is_limited_to_windows11_x64(name,kind,build,arch,valid,monkeypatch):
    path=WEB.parents[1]/'services/helpers/mykos-pxe'
    loader=importlib.machinery.SourceFileLoader('pxe_cli_test',str(path))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    xml=f'<WIM><IMAGE INDEX="1"><NAME>{name}</NAME><WINDOWS><ARCH>{arch}</ARCH><INSTALLATIONTYPE>{kind}</INSTALLATIONTYPE><VERSION><MAJOR>10</MAJOR><BUILD>{build}</BUILD></VERSION></WINDOWS></IMAGE></WIM>'
    monkeypatch.setattr(module.subprocess,'check_output',lambda *a,**kw:xml.encode())
    if valid:module.validate_windows11(Path('fixture.wim'),1)
    else:
        with pytest.raises(ValueError,match='Windows 11'):module.validate_windows11(Path('fixture.wim'),1)


def test_expired_job_discards_recoverable_credentials(env):
    _,client=env;job=arm()
    with store.connect() as c:
        c.execute('UPDATE jobs SET expires=? WHERE id=?',(int(time.time())-1,job['id']))
    with store.connect() as c:
        row=dict(c.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())
    assert row['state']=='failed' and row['ticket']=='' and row['config']=='{}'
    assert row['customer']=='00000000' and row['mac']==MAC


def test_background_expiry_never_enables_unconfigured_pxe(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'ROOT',tmp_path/'unconfigured')
    store.expire()
    assert not store.ROOT.exists()


def test_background_expiry_discards_credentials_and_leases(env):
    _,client=env;job=arm()
    with store.connect() as c:c.execute('UPDATE jobs SET expires=? WHERE id=?',(int(time.time())-1,job['id']))
    store.expire()
    with store.connect() as c:row=dict(c.execute('SELECT state,ticket,config FROM jobs WHERE id=?',(job['id'],)).fetchone())
    assert row=={'state':'failed','ticket':'','config':'{}'}


def test_failed_winpe_console_stays_open_for_diagnostics():
    import pxe_api
    from unittest.mock import patch
    with patch.object(pxe_api.store,'settings',return_value={'origin':'https://mykos.example.com'}):
        body=pxe_api.shell().body.decode()
    assert 'cmd.exe, /k %SYSTEMROOT%' in body


def test_bootstrap_initrd_uses_explicit_injected_filename(env):
    _,client=env
    arm()
    body=client.get('/pxe/select.ipxe',params={'mac':MAC}).text
    assert 'initrd --name install.cmd https://' in body
    assert 'bootstrap.cmd?mac='+MAC in body
