from pathlib import Path
import hashlib,importlib.machinery,importlib.util,json,os,sys
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'services/helpers'))
import mykos_extensions as extensions


def module(root,name='business',target='/opt/mykos-web/index.html',data=b'private business view',base=b'public core view',mutable=False):
 folder=root/'usr/share/mykos-extensions'/name;(folder/'payload').mkdir(parents=True)
 payload=folder/'payload/view';payload.write_bytes(data);payload.chmod(0o644)
 entry={'path':target,'source':'payload/view','sha256':hashlib.sha256(data).hexdigest(),'base_sha256':hashlib.sha256(base).hexdigest() if base is not None else None,'mode':0o640,'owner':'web-read','mutable':mutable}
 manifest={'format':1,'id':name,'version':'1.0-1','files':[entry]};(folder/'manifest.json').write_text(json.dumps(manifest));return folder,manifest


def base():return {'/opt/mykos-web/index.html':{'data':b'public core view','mode':0o640,'owner':'web-read','mutable':False}}


def test_signed_payload_shape_merges_runtime_with_provenance(tmp_path):
 module(tmp_path);original=base();result,info=extensions.merge(tmp_path,original,lambda n:True)
 assert result['/opt/mykos-web/index.html']['data']==b'private business view'
 assert result['/opt/mykos-web/index.html']['extension']=='business'
 assert original==base() and info[0]['id']=='business' and info[0]['files']==1


def test_wrong_core_build_and_payload_tampering_fail_before_change(tmp_path):
 folder,manifest=module(tmp_path);original=base();manifest['files'][0]['base_sha256']='0'*64;(folder/'manifest.json').write_text(json.dumps(manifest))
 with pytest.raises(ValueError,match='core build'):extensions.merge(tmp_path,original,lambda n:True)
 manifest['files'][0]['base_sha256']=hashlib.sha256(b'public core view').hexdigest();(folder/'manifest.json').write_text(json.dumps(manifest));(folder/'payload/view').write_bytes(b'tampered')
 with pytest.raises(ValueError,match='checksum mismatch'):extensions.merge(tmp_path,original,lambda n:True)
 assert original==base()


@pytest.mark.parametrize('target',['/opt/mykos-web/data','/opt/mykos-web/.venv','/opt/mykos-web/data/auth.db','/usr/local/share/mykos/.envtype','/etc/mykos/config.json','/etc/passwd','/opt/mykos-web/../data/auth.db'])
def test_extensions_cannot_supply_credentials_or_host_state(tmp_path,target):
 module(tmp_path,target=target,base=None)
 with pytest.raises(ValueError):extensions.merge(tmp_path,base(),lambda n:True)


def test_payload_symlink_cannot_read_an_external_secret(tmp_path):
 folder,_=module(tmp_path);secret=tmp_path/'outside';secret.write_bytes(b'not for payloads');(folder/'payload/view').unlink();(folder/'payload/view').symlink_to(secret)
 with pytest.raises(ValueError,match='symlink'):extensions.merge(tmp_path,base(),lambda n:True)


def test_world_writable_payload_and_permission_weakening_rejected(tmp_path):
 folder,manifest=module(tmp_path);(folder/'payload/view').chmod(0o666)
 with pytest.raises(ValueError,match='ownership, mode'):extensions.merge(tmp_path,base(),lambda n:True)
 (folder/'payload/view').chmod(0o644);manifest['files'][0]['mode']=0o644;(folder/'manifest.json').write_text(json.dumps(manifest))
 with pytest.raises(ValueError,match='weaken core'):extensions.merge(tmp_path,base(),lambda n:True)


def test_two_extensions_cannot_replace_the_same_runtime_file(tmp_path):
 module(tmp_path,'first');module(tmp_path,'second')
 with pytest.raises(ValueError,match='must not overlap'):extensions.merge(tmp_path,base(),lambda n:True)


def test_core_build_entries_ignore_installed_business_payloads(tmp_path):
 folder,_=module(tmp_path);(folder/'manifest.json').write_text('invalid JSON must not be consulted by core builders')
 loader=importlib.machinery.SourceFileLoader('installer_no_private_build',str(ROOT/'bin/mykos-install'));spec=importlib.util.spec_from_loader(loader.name,loader);installer=importlib.util.module_from_spec(spec);loader.exec_module(installer)
 args=SimpleNamespace(root=str(tmp_path),python='/usr/bin/python3.11',public_origin='https://example.test')
 assert installer.Installer(args).entries(include_extensions=False)['/opt/mykos-web/index.html']['data']==(ROOT/'services/web/index.html').read_bytes()
 with pytest.raises(json.JSONDecodeError):installer.Installer(args).entries()
 assert 'entries(include_extensions=False)' in (ROOT/'bin/mykos-package').read_text()
 assert 'entries(include_extensions=False)' in (ROOT/'packaging/rpm/install_payload.py').read_text()


def test_staged_extension_install_remove_preserves_private_data_and_restores_core(tmp_path):
 import subprocess
 def install(ok=True):
  result=subprocess.run([sys.executable,str(ROOT/'bin/mykos-install'),'install','--root',str(tmp_path)],capture_output=True,text=True)
  assert (result.returncode==0)==ok,result.stdout+result.stderr
 install()
 config=tmp_path/'etc/mykos/config.json';config.write_text('{"private_setting":"preserve"}')
 identity=tmp_path/'opt/mykos-web/data/auth.db';identity.write_bytes(b'private identity sentinel')
 original=(ROOT/'services/web/index.html').read_bytes()
 folder,manifest=module(tmp_path,base=original,data=b'private separately packaged view')
 extra=folder/'payload/recipe';extra.write_text('private image recipe\n');extra.chmod(0o644)
 target='/usr/local/share/mykos/pos/ks/example.ks'
 manifest['files'].append({'path':target,'source':'payload/recipe','sha256':hashlib.sha256(extra.read_bytes()).hexdigest(),'base_sha256':None,'mode':0o640,'owner':'root','mutable':True})
 (folder/'manifest.json').write_text(json.dumps(manifest));install()
 assert (tmp_path/'opt/mykos-web/index.html').read_bytes()==b'private separately packaged view'
 state=json.loads((tmp_path/'var/lib/mykos-installer/installed.json').read_text());assert state['extensions'][0]['id']=='business'
 assert (tmp_path/target.lstrip('/')).is_file()
 # A tampered package is rejected before replacing the working runtime.
 (folder/'payload/view').write_bytes(b'tampered');install(ok=False)
 assert (tmp_path/'opt/mykos-web/index.html').read_bytes()==b'private separately packaged view'
 import shutil
 shutil.rmtree(folder);install()
 assert (tmp_path/'opt/mykos-web/index.html').read_bytes()==original
 assert not (tmp_path/target.lstrip('/')).exists()
 assert config.read_text()=='{"private_setting":"preserve"}' and identity.read_bytes()==b'private identity sentinel'


@pytest.mark.parametrize('target',['/etc/systemd/system/other.service','/etc/systemd/system/mykos-example.service.d/override.conf','/etc/systemd/system/mykos-example.service/evil'])
def test_extension_units_are_confined_to_flat_mykos_units(tmp_path,target):
 module(tmp_path,target=target,base=None)
 with pytest.raises(ValueError,match='Unmanaged'):extensions.merge(tmp_path,base(),lambda n:True)


def test_extension_units_participate_in_activation_and_cleanup(tmp_path):
 target='/etc/systemd/system/mykos-example.timer'
 folder,manifest=module(tmp_path,target=target,base=None,data=b'[Timer]\nOnBootSec=5min\n')
 entry=manifest['files'][0];entry.update(mode=0o644,owner='root');(folder/'manifest.json').write_text(json.dumps(manifest))
 loader=importlib.machinery.SourceFileLoader('installer_extension_units',str(ROOT/'bin/mykos-install'));spec=importlib.util.spec_from_loader(loader.name,loader);installer=importlib.util.module_from_spec(spec);loader.exec_module(installer)
 args=SimpleNamespace(root=str(tmp_path),python='/usr/bin/python3.11',public_origin='https://example.test')
 instance=installer.Installer(args);entries=instance.entries()
 assert entries[target]['extension']=='business'
 assert 'mykos-example.timer' in instance.activation_units(entries)
 assert 'mykos-example.timer' in instance.managed_units(entries)
 assert not any('@.' in name for name in instance.managed_units(entries))



def test_retiring_RPM_removed_unit_stops_active_owner_and_removes_only_its_links(tmp_path,monkeypatch):
 loader=importlib.machinery.SourceFileLoader('installer_retired_units',str(ROOT/'bin/mykos-install'));spec=importlib.util.spec_from_loader(loader.name,loader);mod=importlib.util.module_from_spec(spec);loader.exec_module(mod)
 instance=mod.Installer(SimpleNamespace(root=str(tmp_path),python='/usr/bin/python3.11',public_origin='https://example.test'))
 units=tmp_path/'etc/systemd/system';wants=units/'multi-user.target.wants';wants.mkdir(parents=True)
 (wants/'mykos-retired.service').symlink_to('../mykos-retired.service')
 unrelated=wants/'other.service';unrelated.symlink_to('../other.service')
 calls=[];monkeypatch.setattr(mod,'command',lambda args:calls.append(args))
 monkeypatch.setattr(mod.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout='active\n',returncode=0))
 instance.retire_units(['mykos-retired.service'])
 assert calls==[['systemctl','stop','mykos-retired.service']]
 assert not (wants/'mykos-retired.service').is_symlink() and unrelated.is_symlink()
