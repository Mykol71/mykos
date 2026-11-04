from pathlib import Path
import sys,json,time
import pytest
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'services/web'),str(ROOT/'services/helpers')]
import app,mykos_users,pxe_store as store,pxe_admin
@pytest.fixture
def env(tmp_path,monkeypatch):
 monkeypatch.setattr(mykos_users,'DB',tmp_path/'auth.db');monkeypatch.setattr(store,'ROOT',tmp_path/'pxe');monkeypatch.setenv('MYKOS_PUBLIC_ORIGIN','https://testserver')
 mykos_users.set_password('operator','test-password-fixture',role='admin',create=True)
 mykos_users.set_password('reader','test-password-fixture',role='user',create=True)
 return tmp_path
def client(name):
 c=TestClient(app.app,base_url='https://testserver');r=c.post('/api/login',headers={'origin':'https://testserver'},json={'username':name,'password':'test-password-fixture'});assert r.status_code==200;return c
def test_disabled_view_never_initializes_or_enrolls(env):
 c=client('operator');r=c.get('/api/web/pxe');assert r.status_code==200 and r.json()['enabled'] is False and not store.ROOT.exists()
def test_reader_cannot_view_or_arm(env):
 c=client('reader');assert c.get('/api/web/pxe').status_code==403
 r=c.post('/api/web/pxe/jobs',headers={'origin':'https://testserver'},json={'mac':'52:54:00:12:34:56','customer':'00000000','profile':'windows11','password':'never-copy-this-secret'});assert r.status_code==403 and 'never-copy' not in r.text and not store.ROOT.exists()
def test_password_and_erase_validation_are_safe(env,monkeypatch):
 c=client('operator');monkeypatch.setattr(pxe_admin,'readable_assets',lambda:({name:{} for name in pxe_admin.REQUIRED_MEDIA},''));called=[];monkeypatch.setattr(store,'enroll',lambda *a,**k:called.append((a,k)) or {'id':'test'})
 data={'mac':'52:54:00:12:34:56','customer':'00000000','profile':'windows11','password':'a-private-test-password','allow_wipe':True,'confirmation':'52:54:00:12:34:57'}
 assert c.post('/api/web/pxe/jobs',headers={'origin':'https://testserver'},json=data).status_code==400 and not called
 data['confirmation']=data['mac'];r=c.post('/api/web/pxe/jobs',headers={'origin':'https://testserver'},json=data);assert r.status_code==200 and 'a-private-test-password' not in r.text and called[0][1]['allow_wipe']
def test_csrf_cannot_arm(env):
 c=client('operator');assert c.post('/api/web/pxe/jobs',headers={'origin':'https://evil.example'},json={'mac':'52:54:00:12:34:56','customer':'00000000','profile':'windows11','password':'test-password-fixture'}).status_code==403


def test_unreadable_media_is_not_ready_and_cannot_arm(env,monkeypatch):
 store.configure('https://testserver',['192.168.222.0/24'])
 monkeypatch.setattr(store,'assets',lambda:{'boot.wim':{'sha256':'a'*64,'size':1}})
 def inaccessible(name):raise PermissionError('sandbox media is hidden')
 monkeypatch.setattr(store,'asset',inaccessible)
 c=client('operator');view=c.get('/api/web/pxe').json();assert view['assets']=={} and 'read-only media mount' in view['readiness_error']
 monkeypatch.setattr(store,'enroll',lambda *a,**kw:pytest.fail('Unreadable media reached enrollment'))
 response=c.post('/api/web/pxe/jobs',headers={'origin':'https://testserver'},json={'mac':'52:54:00:12:34:56','customer':'00000000','profile':'windows11','password':'test-password-fixture'})
 assert response.status_code==503 and store.jobs()==[]
