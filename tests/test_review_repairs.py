"""Review212 regressions: private state, fake transports and mocked host commands."""
import asyncio,ast,importlib.machinery,importlib.util,json,os,shutil,subprocess,sys,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'services/web'),str(ROOT/'services/helpers'),str(ROOT/'pos')]
import app,api_v2,mykos_users,mykos_api_keys,login_guard,mykos_runtime,build_rootfs


def test_throttle_limits_account_across_sources_and_recovers(tmp_path):
 p=tmp_path/'limits.db'
 for i in range(10):login_guard.reserve('person',str(i),p,now=100)
 with pytest.raises(HTTPException) as e:login_guard.reserve('person','different',p,now=101)
 assert e.value.status_code==429 and e.value.headers['Retry-After']=='299'
 login_guard.reserve('person','different',p,now=400)
 assert 'person' not in p.read_bytes().decode('latin1')


def test_throttle_source_cannot_reset_by_success_or_change_accounts(tmp_path):
 p=tmp_path/'limits.db'
 for i in range(20):
  login_guard.reserve('person'+str(i),'source',p,now=100)
  login_guard.success('person'+str(i),p)
 with pytest.raises(HTTPException) as e:login_guard.reserve('another','source',p,now=101)
 assert e.value.status_code==429
 login_guard.reserve('another','source',p,now=160)


def test_throttle_success_resets_only_account_and_failure_is_closed(tmp_path):
 p=tmp_path/'limits.db'
 for i in range(9):login_guard.reserve('person',str(i),p,now=100)
 login_guard.success('person',p)
 for i in range(10):login_guard.reserve('person','new'+str(i),p,now=101)
 with pytest.raises(HTTPException) as e:login_guard.reserve('x','s',tmp_path/'missing'/'limits.db')
 assert e.value.status_code==503


def test_login_http_limits_hash_work_and_header_spoofing(tmp_path,monkeypatch):
 monkeypatch.setattr(mykos_users,'DB',tmp_path/'auth.db');monkeypatch.setenv('MYKOS_PUBLIC_ORIGIN','https://testserver')
 mykos_users.set_password('review','synthetic-review-password',create=True);calls=[]
 monkeypatch.setattr(app,'digest',lambda *a:(calls.append(1) or 'wrong'))
 client=TestClient(app.app,base_url='https://testserver')
 codes=[client.post('/api/login',headers={'Origin':'https://testserver','X-Forwarded-For':'192.0.2.'+str(i)},json={'username':'review','password':'bad'}).status_code for i in range(15)]
 assert codes==[401]*10+[429]*5 and len(calls)==10
 assert client.post('/api/login',headers={'Origin':'https://testserver'},json={'username':'review','password':'x'*4097}).status_code==422
 client.close()


@pytest.mark.parametrize('path',['/api/terminal/host','/api/v2/terminal/host','/mykos/api/v2/terminal/01234567','/api/web/snapshot-preview/'+'a'*32+'/terminal'])
def test_terminal_revocation_closes_idle_and_active_transport(tmp_path,monkeypatch,path):
 monkeypatch.setattr(mykos_users,'DB',tmp_path/'auth.db');monkeypatch.setattr(mykos_api_keys,'DB',tmp_path/'keys.db')
 mykos_users.set_password('review','synthetic-review-password',role='admin',create=True)
 import hashlib
 with mykos_users.connect() as db:db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(b'session').hexdigest(),'review',int(time.time())+300))
 key=mykos_api_keys.create('review','admin');real_authority=app.mykos_terminal_authority;written=[];closed=[];revoked=False
 preview='owner' if 'snapshot-preview' in path else None
 import container_admin
 monkeypatch.setattr(container_admin,'invoke',lambda *a:{'state':'ready'})
 class WS:
  scope={'path':path,'state':{}}
  cookies={'mykos_session':'session'}
  headers={'X-MYKOS-API-Key':key}
  async def accept(self):pass
  async def receive(self):await asyncio.sleep(10)
  async def send_bytes(self,b):pass
  async def close(self,**kw):closed.append(kw)
 class Reader:
  async def readline(self):return b'MYKOS_TERMINAL_READY\n'
  async def read(self,n):await asyncio.sleep(10)
 class Writer:
  def write(self,b):written.append(b)
  async def drain(self):pass
  def close(self):closed.append('writer')
  async def wait_closed(self):pass
 async def connection(*a):return Reader(),Writer()
 checks=[]
 def authority(ws,shop,owner):
  checks.append(1)
  if len(checks)==3:
   with mykos_users.connect() as db:db.execute('DELETE FROM sessions')
   with mykos_api_keys.connect() as db:db.execute('UPDATE api_keys SET revoked_at=1')
  return real_authority(ws,shop,owner)
 monkeypatch.setattr(app,'mykos_terminal_authority',authority);monkeypatch.setattr(app,'TERMINAL_AUTH_INTERVAL',.01);monkeypatch.setattr(asyncio,'open_unix_connection',connection)
 asyncio.run(asyncio.wait_for(app.mykos_terminal_bridge(WS(),'host',preview_owner=preview),1))
 assert len(checks)>=3 and 'writer' in closed and {'code':1008} in closed
 assert len(written)==1


def test_terminal_demotion_and_preview_expiry_are_denied(tmp_path,monkeypatch):
 monkeypatch.setattr(mykos_users,'DB',tmp_path/'auth.db');mykos_users.set_password('review','synthetic-review-password',role='user',create=True)
 import hashlib
 with mykos_users.connect() as db:db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(b'session').hexdigest(),'review',int(time.time())+300))
 ws=SimpleNamespace(scope={'path':'/api/terminal/host','state':{}},cookies={'mykos_session':'session'})
 with pytest.raises(HTTPException):app.mykos_terminal_authority(ws,'host')
 with mykos_users.connect() as db:db.execute("UPDATE users SET role='admin'")
 import container_admin
 def expired(*args):raise HTTPException(403,'Preview expired')
 monkeypatch.setattr(container_admin,'invoke',expired)
 with pytest.raises(HTTPException):app.mykos_terminal_authority(ws,'a'*32,'owner')


def test_openapi_private_note_get_and_put_use_session():
 schema=api_v2.openapi_v2(None)
 for path in ['/api/v2/personal-notes','/api/v2/notes/{pane}']:
  for method in ['get','put']:assert schema['paths'][path][method]['security']==[{'MYKOSNotesSession':[]}]
 assert schema['paths']['/api/v2/host']['get']['security']==[{'MYKOSApiKey':[]}]


def installer_module():
 loader=importlib.machinery.SourceFileLoader('review_installer',str(ROOT/'bin/mykos-install'));spec=importlib.util.spec_from_loader(loader.name,loader);m=importlib.util.module_from_spec(spec);loader.exec_module(m);return m


def test_rpm_remove_calls_canonical_complete_unit_stop(monkeypatch):
 m=installer_module();calls=[];monkeypatch.setattr(m,'command',lambda args:calls.append(args));monkeypatch.setattr(m.os,'geteuid',lambda:0)
 m.Installer(SimpleNamespace(root='/',action='stop_services',dry_run=False)).stop_services()
 assert set(m.UNITS+m.OPTIONAL_UNITS)<=set(calls[0][2:])
 assert set(m.UNITS+m.OPTIONAL_UNITS)<=set(calls[1][2:])
 assert 'mykos-podman-*' not in str(calls) and 'podman' not in str(calls)
 spec=(ROOT/'packaging/rpm/mykos.spec').read_text().split('%preun')[1].split('%postun')[0]
 assert 'mykos-install stop_services' in spec and '|| exit 1' in spec


def test_private_build_recognition_preserves_old_and_new_guards():
 for name in mykos_runtime.NATIVE_OS_NAMES:
  assert mykos_runtime.native_build_os(['anaconda','--kickstart','/var/tmp/'+name+'.ks'])==name
  assert mykos_runtime.native_build_os(['livemedia-creator','--ks=/var/tmp/containers/mykos-native-'+name+'.ABCDEFGH/'+name+'.ks'])==name
 assert mykos_runtime.native_build_os(['--ks=/var/tmp/containers/mykos-native-rh7.ABC/rh8.ks']) is None


def test_speed_documentation_matches_code_and_timer():
 import network_speed
 text=(ROOT/'docs/API.md').read_text().split('### Host internet speed measurements')[1]
 assert 'per minute' not in text and 'older than three minutes' not in text
 assert '13 hours' in text and 'midnight and noon' in text
 assert network_speed.INTERVAL==43200 and network_speed.STALE_AFTER==46800
 assert '00,12:00:00 America/Chicago' in (ROOT/'services/systemd/mykos-speedtest-collector.timer').read_text()


def test_concurrent_admission_and_bounded_table(tmp_path,monkeypatch):
 from concurrent.futures import ThreadPoolExecutor
 p=tmp_path/'limits.db';login_guard.reserve('seed','seed',p,now=100)
 def attempt(i):
  try:login_guard.reserve('same','source'+str(i),p,now=101);return True
  except HTTPException as e:assert e.status_code==429;return False
 with ThreadPoolExecutor(max_workers=8) as pool:assert sum(pool.map(attempt,range(20)))==10
 monkeypatch.setattr(login_guard,'MAX_BUCKETS',2)
 with pytest.raises(HTTPException) as e:login_guard.reserve('new','new',p,now=102)
 assert e.value.status_code==429


@pytest.mark.skipif(not shutil.which('flock'),reason='Native shell admission requires Linux flock')
def test_native_pipeline_blocks_interleaving_and_cleans_each_context(tmp_path):
 import signal
 text=(ROOT/'pos/pos').read_text()
 functions=text[text.index('check_image_builder() {'):text.index('\nstop_container() {')]
 functions+='\n'+text[text.index('build-image() ('):text.index('\nbuild-all-images() {')]
 functions=functions.replace('/var/tmp/containers',str(tmp_path/'containers')).replace('/usr/local/sbin/mykos-native-image-start','native_start')
 (tmp_path/'prepare_ks.py').write_text('import sys;from pathlib import Path;Path(sys.argv[2]).write_text("generated copy")\n')
 (tmp_path/'image_version.py').write_text('print("review-version")\n')
 (tmp_path/'build_rootfs.py').write_text('import os,sys,time;from pathlib import Path\nroot=Path(os.environ["REVIEW_ROOT"])\nif sys.argv[1]=="rh7":\n (root/"ready").touch()\n for _ in range(1000):\n  if (root/"release").exists():break\n  time.sleep(.01)\n else:raise RuntimeError("review timeout")\nout=Path(sys.argv[sys.argv.index("--output-directory")+1]);(out/(sys.argv[1]+"-docker.tar.xz")).write_bytes(b"rootfs")\n')
 script=r"""set -eu
cd "$REVIEW_ROOT"
ROOTDIR="$REVIEW_ROOT"
MYKOS_PYTHON="$REVIEW_PYTHON"
REPOSERVER=review.invalid
renice() { :; }; ionice() { :; }
curl() { while [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then shift; printf '#!/bin/sh\nexit 0\n' > "$1"; return; fi; shift; done; }
native_start() { echo mock-native-base; }
podman() {
 case "$1" in
 image) return 1;;
 build) cp Dockerfile "$REVIEW_ROOT/result-$3"; printf '%s\n' "$PWD" > "$REVIEW_ROOT/context-$3";;
 *) return 0;;
 esac
}
"""+functions+'\nbuild-image "$1"\n'
 # Convert the final Python test string's escaped newlines only in its appended command.
 script=script.replace('\\nbuild-image','\nbuild-image').replace('"$1"\\n','"$1"\n')
 path=tmp_path/'run';path.write_text(script)
 env={**os.environ,'REVIEW_ROOT':str(tmp_path),'REVIEW_PYTHON':sys.executable}
 first=subprocess.Popen(['/bin/bash',str(path),'rh7'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
 try:
  deadline=time.monotonic()+10
  while not (tmp_path/'ready').exists() and time.monotonic()<deadline:
   if first.poll() is not None:break
   time.sleep(.01)
  assert (tmp_path/'ready').exists(),first.communicate(timeout=1)
  second=subprocess.run(['/bin/bash',str(path),'rh8'],env=env,capture_output=True,text=True,timeout=10)
  assert second.returncode!=0 and 'Another native image build' in second.stderr
  (tmp_path/'release').touch();stdout,stderr=first.communicate(timeout=15);assert first.returncode==0,stdout+stderr
  second=subprocess.run(['/bin/bash',str(path),'rh8'],env=env,capture_output=True,text=True,timeout=15);assert second.returncode==0,second.stdout+second.stderr
  assert 'ADD rh7-docker.tar.xz /' in (tmp_path/'result-rh7').read_text()
  assert 'ADD rh8-docker.tar.xz /' in (tmp_path/'result-rh8').read_text()
  assert (tmp_path/'context-rh7').read_text()!=(tmp_path/'context-rh8').read_text()
  assert list((tmp_path/'containers').iterdir())==[]
 finally:
  if first.poll() is None:
   os.killpg(first.pid,signal.SIGKILL);first.communicate(timeout=5)
