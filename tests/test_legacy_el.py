"""Legacy guest trust boundaries; live boot/capture is qualified separately on dev."""
import hashlib,importlib.util,json
from pathlib import Path
import pytest
import mykos_runtime as runtime
import mykos_cgroups as cgroups
ROOT=Path(__file__).resolve().parents[1]
CATALOG=json.loads((ROOT/'pos/legacy_el.json').read_text())

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

@pytest.mark.parametrize('name',sorted(CATALOG))
def test_final_versions_and_pinned_vendor_keys(name):
 spec=CATALOG[name]
 assert spec['major'] in (6,7)
 assert spec['version']=={6:'6.10',7:'7.9'}[spec['major']]
 assert name in runtime.NATIVE_OS_NAMES and name in runtime.STAGE_OS_NAMES
 assert all(r['url'].startswith('https://') for r in spec['repositories'])
 for key in spec['keys']:
  data=(ROOT/'pos/legacy-keys'/(key['sha256']+'.asc')).read_bytes()
  assert hashlib.sha256(data).hexdigest()==key['sha256']
  assert b'-----BEGIN PGP PUBLIC KEY BLOCK-----' in data

@pytest.mark.parametrize('name',['oracle7','centos7','scientific7','springdale7'])
def test_all_el7_clones_require_existing_isolated_cgroup_profile(name,monkeypatch,tmp_path):
 marker=tmp_path/'unified';marker.touch();monkeypatch.setattr(cgroups,'UNIFIED',marker)
 assert cgroups.legacy_image(name)
 assert cgroups.init_command(name)==['/usr/bin/python','/usr/local/libexec/mykos-rh7-init']

@pytest.mark.parametrize('name',['rh6','oracle6','centos6','scientific6','springdale6'])
def test_el6_retains_vendor_init_without_enabling_el7_mounts(name,monkeypatch,tmp_path):
 marker=tmp_path/'unified';marker.touch();monkeypatch.setattr(cgroups,'UNIFIED',marker)
 monkeypatch.setattr(cgroups,'run',lambda *a,**k:pytest.fail('EL6 must not enable EL7 cgroups'))
 assert cgroups.arguments(name)==[] and cgroups.init_command(name)==['/sbin/init']
 assert runtime.native_build_os(['python3','./build_legacy_el.py',name])==name

@pytest.mark.parametrize('name',['../../rh6','alma6','rocky7','centos6;reboot'])
def test_builder_rejects_unknown_or_unavailable_guests_before_podman(name,monkeypatch):
 builder=load('legacy_builder_validation',ROOT/'pos/build_legacy_el.py')
 monkeypatch.setattr(builder.os,'geteuid',lambda:0)
 monkeypatch.setattr(builder.subprocess,'run',lambda *a,**k:pytest.fail('Invalid selection must not invoke Podman'))
 with pytest.raises(ValueError,match='supported'):builder.build(name)

def test_repository_worker_has_no_host_credentials_or_publication_mounts(tmp_path):
 sync=load('legacy_repository_validation',ROOT/'repo/bin/sync_repos.py')
 cmd=sync.worker_command('centos6',tmp_path,'a'*32)
 assert '--cpus=1' in cmd and '--memory=2g' in cmd and '--pids-limit=256' in cmd
 assert not any('entitlement' in a or '/var/www/html:' in a or '/home/mykos-repositories:' in a for a in cmd)
 assert str(tmp_path/'stage')+':/stage:rw' in cmd
 assert sync.repositories(6)==['rhel-6-server-rpms','rhel-6-server-optional-rpms','rhel-6-server-extras-rpms']
 assert sync.repositories('scientific7')==[r['name'] for r in CATALOG['scientific7']['repositories']]


@pytest.mark.parametrize('name',sorted(CATALOG))
def test_legacy_identity_and_prepared_image_inventory(name,monkeypatch):
 import mykos_host_monitoring as monitor
 import mykos_vm
 spec=CATALOG[name]
 titles={'centos':'CentOS','oracle':'Oracle Linux','scientific':'Scientific Linux','springdale':'Springdale Linux','rhel':'Red Hat Enterprise Linux'}
 labels={'io.mykos.os':name,'io.mykos.os.version':spec['version']}
 data=monitor.image_os({'Image':'a'*64,'ImageName':'a'*64},{'Labels':labels})
 assert data['os_type']==titles[spec['distribution']]
 assert data['os_version']==spec['version']
 monkeypatch.setattr(runtime,'run',lambda *a,**k:json.dumps([{'Names':[name],'Image':'localhost/'+name+':latest','State':'running'}]))
 monkeypatch.setattr(mykos_vm,'prepared_templates',lambda:[])
 assert runtime.stage_images()==[{'os':name,'image':'localhost/'+name+':latest'}]


def test_el7_exec_boundary_check_is_read_only_and_rejects_foreign_groups(tmp_path,monkeypatch):
 import mykos_criu_cgroups as cg
 proc=tmp_path/'proc';proc.mkdir();namespace=tmp_path/'pid-namespace';namespace.write_text('fixture')
 def process(pid,membership,other=False):
  p=proc/str(pid);(p/'ns').mkdir(parents=True)
  if other:(p/'ns/pid').write_text('different namespace')
  else:(p/'ns/pid').hardlink_to(namespace)
  (p/'cgroup').write_text('1:name=systemd:'+membership+'\n0::/fixture\n')
 process(100,'/mykos/'+'a'*64);process(101,'/mykos/'+'a'*64+'/system.slice/sshd.service');process(999,'/',True)
 monkeypatch.setattr(cg,'PROC',proc);state={'State':{'Pid':100}}
 before={str(p.relative_to(proc)):p.read_bytes() for p in proc.rglob('*') if p.is_file()}
 cg.check_legacy_processes(state,'/mykos/'+'a'*64)
 assert before=={str(p.relative_to(proc)):p.read_bytes() for p in proc.rglob('*') if p.is_file()}
 process(102,'/')
 with pytest.raises(ValueError,match='Close exec terminals'):cg.check_legacy_processes(state,'/mykos/'+'a'*64)
 assert (proc/'102/cgroup').read_text()=='1:name=systemd:/\n0::/fixture\n'


def test_legacy_aliases_are_explicit_and_lan_only():
 aliases=load('legacy_alias_validation',ROOT/'repo/bin/legacy_aliases.py')
 names=aliases.repository_names();text=aliases.render('/home/test-repositories')
 assert len(names)==19
 for name in names:
  assert 'Alias /'+name+'/ /home/test-repositories/'+name+'/' in text
  assert 'Alias /repos/'+name+'/ /home/test-repositories/'+name+'/' in text
 assert text.count('AuthMerging Off')==len(names)
 assert text.count('Require ip 127.0.0.1 ::1 192.168.222.0/24 192.168.22.0/24')==len(names)
 assert 'Require all granted' not in text
 assert not any(x in text for x in ('updateos','el8','el9','el10'))

@pytest.mark.parametrize('root',['relative','/','/tmp/../../etc','/tmp/repo"\nRequire all granted'])
def test_legacy_alias_render_rejects_injected_paths(root):
 aliases=load('legacy_alias_injection_validation',ROOT/'repo/bin/legacy_aliases.py')
 with pytest.raises(ValueError):aliases.render(root)


@pytest.mark.parametrize('data',[b'',b'centos6',bytes(range(256))*9000])
def test_rootfs_receipt_hash_works_without_python311_file_digest(data,tmp_path,monkeypatch):
 builder=load('legacy_builder_python39_receipt',ROOT/'pos/build_legacy_el.py')
 monkeypatch.delattr(hashlib,'file_digest',raising=False)
 archive=tmp_path/'rootfs.tar';archive.write_bytes(data)
 assert builder.sha256_file(archive)==hashlib.sha256(data).hexdigest()
 archive.unlink()
