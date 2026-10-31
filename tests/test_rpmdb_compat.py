from pathlib import Path
from types import SimpleNamespace
import sys,tempfile
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pos'))
import rpmdb_compat as compat
import prepare_ks


def rootfs(tmp_path):
 root=tmp_path/'root';(root/'etc').mkdir(parents=True);(root/'etc/redhat-release').write_text('Red Hat Enterprise Linux Server release 7.9 (Maipo)\n')
 (root/'var/lib/rpm').mkdir(parents=True);(root/'var/tmp').mkdir(parents=True)
 return root


def test_inventory_compares_native_package_identities_without_key_pseudo_packages():
 assert compat.inventory('bash\t(none)\t4.2\t1.el7\tx86_64\ngpg-pubkey\t(none)\tx\ty\t(none)\n')==[('bash',0,'4.2','1.el7','x86_64')]
 with pytest.raises(ValueError):compat.inventory('warning: bad database\n')


def test_root_and_release_guards_prevent_host_or_escaped_database_writes(tmp_path):
 with pytest.raises(ValueError):compat.checked_root(Path('/'),7)
 root=rootfs(tmp_path)
 with pytest.raises(ValueError,match='release'):compat.checked_root(root,8)
 db=root/'var/lib/rpm';db.rmdir();outside=tmp_path/'outside';outside.mkdir();db.symlink_to(outside,target_is_directory=True)
 with pytest.raises(ValueError,match='escapes'):compat.checked_root(root,7)


def test_native_legacy_builder_needs_no_conversion_or_new_packages(tmp_path):
 root=rootfs(tmp_path)
 assert compat.normalize(root,7,['https://mirror.test/'])['converted'] is False


def test_failed_post_swap_validation_restores_original_database(tmp_path,monkeypatch):
 root=rootfs(tmp_path);database=root/'var/lib/rpm';(database/'rpmdb.sqlite').write_bytes(b'original installer database')
 header={'name':'bash','epoch':None,'version':'4.2','release':'1.el7','arch':'x86_64'};expected=[('bash',0,'4.2','1.el7','x86_64')]
 monkeypatch.setitem(sys.modules,'rpm',SimpleNamespace(TransactionSet=lambda root:SimpleNamespace(dbMatch=lambda:[header])))
 monkeypatch.setattr(compat,'native_inventory',lambda root,db=None:expected if db else [])
 def download(packages,repositories,major,directory):
  folder=directory/'packages';folder.mkdir();(folder/'0.rpm').write_bytes(b'package fixture');return folder
 monkeypatch.setattr(compat,'download_exact',download)
 calls=[];monkeypatch.setattr(compat,'run',lambda args:calls.append(args) or '')
 original=tempfile.TemporaryDirectory
 monkeypatch.setattr(compat.tempfile,'TemporaryDirectory',lambda **kwargs:original(prefix=kwargs.get('prefix',''),dir=tmp_path))
 with pytest.raises(ValueError,match='after database replacement'):compat.normalize(root,7,['https://mirror.test/'])
 assert (database/'rpmdb.sqlite').read_bytes()==b'original installer database'
 assert not list((root/'var/lib').glob('.mykos-native-rpmdb-*'))
 install=next(c for c in calls if '--install' in c)
 assert {'--justdb','--noscripts','--notriggers'}<=set(install)


@pytest.mark.parametrize('name,converted',[('rh7.ks',True),('rh8.ks',True),('oracle8.ks',True),('rh9.ks',False)])
def test_database_fix_is_scoped_to_generated_legacy_build_copies(tmp_path,monkeypatch,name,converted):
 original=(Path(__file__).resolve().parents[1]/'pos/ks'/name).read_bytes();source=tmp_path/name;source.write_bytes(original)
 monkeypatch.setattr(prepare_ks,'builder_capabilities',lambda:dict(timesource=True,weakdeps='--exclude-weakdeps',authselect=True,rpm_backend='sqlite',instlangs='--inst-langs'))
 monkeypatch.setattr(prepare_ks,'builder_interpreter',lambda:['/vendor/python'])
 output=tmp_path/'build.ks';prepare_ks.prepare(source,output)
 assert source.read_bytes()==original
 assert ('/vendor/python' in output.read_text())==converted
 if converted:
  assert 'rpmdb_compat.py "$target_root" --major ' in output.read_text()
  assert '--nochroot --erroronfail --log=/tmp/mykos-native-rpmdb.log' in output.read_text()


def test_readable_el8_sqlite_is_rebuilt_and_write_opened_before_atomic_swap(tmp_path,monkeypatch):
 root=rootfs(tmp_path);(root/'etc/redhat-release').write_text('Red Hat Enterprise Linux release 8.10 (Ootpa)\n')
 database=root/'var/lib/rpm';(database/'rpmdb.sqlite').write_bytes(b'original read-only installer database')
 payload=root/'etc/application-data';payload.write_bytes(b'untouched')
 header={'name':'bash','epoch':None,'version':'4.4','release':'1.el8','arch':'x86_64'};expected=[('bash',0,'4.4','1.el8','x86_64')]
 monkeypatch.setitem(sys.modules,'rpm',SimpleNamespace(TransactionSet=lambda root:SimpleNamespace(dbMatch=lambda:[header])))
 monkeypatch.setattr(compat,'native_inventory',lambda root,db=None:expected)
 monkeypatch.setattr(compat,'download_exact',lambda *a:pytest.fail('Readable target headers need no package downloads'))
 calls=[]
 def run(args):
  calls.append(args);db=root/args[4].lstrip('/')
  if '--rebuilddb' in args:(db/'rpmdb.sqlite').unlink();(db/'Packages').write_bytes(b'native writable database')
  return ''
 monkeypatch.setattr(compat,'run',run)
 result=compat.normalize(root,8,['https://mirror.test/'])
 assert result['converted'] and result['packages']==1
 assert any('--rebuilddb' in c for c in calls) and any('--initdb' in c for c in calls)
 assert not any('--install' in c for c in calls)
 assert not (database/'rpmdb.sqlite').exists() and (database/'Packages').read_bytes()==b'native writable database'
 assert payload.read_bytes()==b'untouched'


def test_failed_el8_copy_rebuild_keeps_original_database(tmp_path,monkeypatch):
 root=rootfs(tmp_path);(root/'etc/redhat-release').write_text('Red Hat Enterprise Linux release 8.10 (Ootpa)\n')
 database=root/'var/lib/rpm';(database/'rpmdb.sqlite').write_bytes(b'original')
 header={'name':'bash','epoch':None,'version':'4.4','release':'1.el8','arch':'x86_64'}
 monkeypatch.setitem(sys.modules,'rpm',SimpleNamespace(TransactionSet=lambda root:SimpleNamespace(dbMatch=lambda:[header])))
 monkeypatch.setattr(compat,'native_inventory',lambda *a:[('bash',0,'4.4','1.el8','x86_64')])
 def fail(args):raise compat.subprocess.CalledProcessError(1,args)
 monkeypatch.setattr(compat,'run',fail)
 with pytest.raises(compat.subprocess.CalledProcessError):compat.normalize(root,8,['https://mirror.test/'])
 assert (database/'rpmdb.sqlite').read_bytes()==b'original'
 assert not list((root/'var/lib').glob('.mykos-native-rpmdb-*'))
