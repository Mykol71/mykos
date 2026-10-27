"""Synthetic backup test in a separate VFS store; never touches customer data."""
import contextlib,hashlib,importlib.machinery,importlib.util,json,os,pathlib,shutil,stat,struct,subprocess,sys,tarfile,tempfile,time
root=pathlib.Path(tempfile.mkdtemp(prefix='mykos-backup130-'));root.chmod(0o700)
source=pathlib.Path('/home/mgreen/mykos-backup130-test-source')
sys.path.insert(0,'/usr/local/sbin');sys.path.insert(0,str(source))
import mykos_backup_identity as identity,mykos_backup_restore as backup
real_before=subprocess.check_output(['/usr/bin/podman','ps','--format','{{.ID}} {{.Names}} {{.StartedAt}}'],text=True)
store=root/'store';conf=root/'storage.conf'
conf.write_text('[storage]\ndriver="vfs"\nrunroot="'+str(store/'run')+'"\ngraphroot="'+str(store/'graph')+'"\n')
wrapper=root/'podman';wrapper.write_text('#!/bin/bash\nexport CONTAINERS_STORAGE_CONF='+str(conf)+'\nexec /usr/bin/podman --cgroup-manager=cgroupfs "$@"\n');wrapper.chmod(0o700)
def pod(*args):
 return subprocess.check_output([str(wrapper),*args],text=True,stderr=subprocess.PIPE,timeout=90).strip()
seed=root/'seed';seed.mkdir()
def put(rel,text,mode=0o644):
 p=seed/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);p.chmod(mode);return p
put('etc/passwd','root:x:0:0:root:/root:/bin/bash\ndaemon:x:50:50:daemon:/var/lib/daemon:/sbin/nologin\ntfrsync-01234567:x:1004:1004:backup:/home/tfrsync-01234567:/bin/bash\n')
put('etc/group','root:x:0:\ndaemon:x:50:\ntfrsync-01234567:x:1004:\n')
put('etc/shadow','root:locked-root:1:0:99999:7:::\n',0o600);put('etc/gshadow','root:!::\ndaemon:!::\n',0o600)
put('home/tfrsync-01234567/stale','must be removed')
owned=put('var/lib/daemon/owned','daemon data',0o4750);os.chown(owned,50,50);os.chmod(owned,0o4750)
link=seed/'var/lib/daemon/hardlink';os.link(owned,link)
acl=put('var/lib/daemon/acl-only','acl named identity')
acl_value=struct.pack('<IHHIHHIHHIHHIHHI',2,1,7,0xffffffff,2,4,50,4,0,0xffffffff,16,4,0xffffffff,32,0,0xffffffff)
os.setxattr(acl,'system.posix_acl_access',acl_value)
for binary in ('/bin/bash','/bin/sleep'):
 paths=[binary]
 output=subprocess.check_output(['ldd',binary],text=True)
 for line in output.splitlines():
  for part in line.split():
   if part.startswith('/') and pathlib.Path(part).is_file():paths.append(part)
 for path in paths:
  target=seed/path.lstrip('/');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target,follow_symlinks=True)
put('sbin/init','#!/bin/bash\necho live-write > /usr2/from-guest\ntrap "exit 0" TERM INT\nwhile :; do /bin/sleep 1; done\n',0o755)
for rel in ('proc','sys','dev','run','root','tmp'):(seed/rel).mkdir(exist_ok=True)
archive=root/'seed.tar'
with tarfile.open(archive,'w') as t:
 for path in seed.iterdir():t.add(path,arcname=path.name)
back=root/'backups/tfrsync-01234567';(back/'etc').mkdir(parents=True)
for rel in ('usr2','home/alice','home/tfrsync-01234567'):(back/rel).mkdir(parents=True)
(back/'home/tfrsync-01234567/key-marker').write_text('not exposed to guest')
(back/'etc/passwd').write_text('root:x:0:0:root:/root:/bin/bash\nalice:x:50:50:Alice:/home/alice:/bin/bash\ntfrsync-01234567:x:1004:1004:backup:/home/tfrsync-01234567:/bin/bash\n')
(back/'etc/group').write_text('root:x:0:\napp:x:50:alice\ntfrsync-01234567:x:1004:\n')
(back/'etc/shadow').write_text('root:source-root:1:0:99999:7:::\nalice:source-alice:1:0:99999:7:::\n')
(back/'etc/shadow').chmod(0o600)
original={str(p.relative_to(back)):(p.read_bytes(),p.stat().st_uid,p.stat().st_gid,stat.S_IMODE(p.stat().st_mode)) for p in back.rglob('*') if p.is_file()}
loader=importlib.machinery.SourceFileLoader('backup_stage',str(source/'mykos-stage-pos'));spec=importlib.util.spec_from_loader(loader.name,loader);stage=importlib.util.module_from_spec(spec);loader.exec_module(stage)
stage.PODMAN=str(wrapper);stage.vm.locked=contextlib.nullcontext
stage.config=lambda:{'backup_root':str(root/'backups')};stage.containers=lambda:[]
stage.socket.gethostname=lambda:'test.example';stage.network.arguments=lambda *args:['--network','none'];stage.profile.services=lambda:()
# Redirect only fixed runtime record/VPN paths, keeping rootfs verification real.
real_path=pathlib.Path
stage.Path=lambda p:root/'runtime'/str(p).lstrip('/') if str(p).startswith('/usr/local/share/mykos/pos/') else real_path(p)
created=None
try:
 pod('import','--change','ENTRYPOINT []',str(archive),'localhost/mykos-backup-test:latest')
 original_prepare=stage.prepare_backup
 def acl_fixture(cid,*args):
  mounted=pathlib.Path(pod('mount',cid))
  os.setxattr(mounted/'var/lib/daemon/acl-only','system.posix_acl_access',acl_value)
  pod('unmount',cid)
  return original_prepare(cid,*args)
 stage.prepare_backup=acl_fixture
 stage.stage('01234567','yes','localhost/mykos-backup-test:latest')
 value=json.loads(pod('inspect','01234567.test.example'))[0];created=value['Id'];assert value['State']['Running']
 guest=pathlib.Path('/proc')/str(value['State']['Pid'])/'root'
 passwd=(guest/'etc/passwd').read_text();assert 'alice:x:50:50:' in passwd and 'tfrsync-' not in passwd
 daemon=next(line.split(':') for line in passwd.splitlines() if line.startswith('daemon:'))
 expected=(int(daemon[2]),int(daemon[3]));assert expected!=(50,50)
 assert ((guest/'var/lib/daemon/owned').stat().st_uid,(guest/'var/lib/daemon/owned').stat().st_gid)==expected
 assert (guest/'var/lib/daemon/hardlink').stat().st_uid==expected[0]
 assert stat.S_IMODE((guest/'var/lib/daemon/owned').stat().st_mode)==0o4750
 actual_acl=os.getxattr(guest/'var/lib/daemon/acl-only','system.posix_acl_access');assert struct.unpack_from('<I',actual_acl,16)[0]==expected[0]
 assert not (guest/'home/tfrsync-01234567').exists()
 assert 'locked-root' in (guest/'etc/shadow').read_text() and 'source-root' not in (guest/'etc/shadow').read_text()
 for _ in range(30):
  if (back/'usr2/from-guest').exists():break
  time.sleep(.1)
 assert (back/'usr2/from-guest').read_text().strip()=='live-write'
 for rel,metadata in original.items():
  p=back/rel;assert (p.read_bytes(),p.stat().st_uid,p.stat().st_gid,stat.S_IMODE(p.stat().st_mode))==metadata
 pod('stop','--time','2',created);pod('start',created)
 value=json.loads(pod('inspect',created))[0];guest=pathlib.Path('/proc')/str(value['State']['Pid'])/'root'
 assert 'alice:x:50:50:' in (guest/'etc/passwd').read_text()
 assert real_before==subprocess.check_output(['/usr/bin/podman','ps','--format','{{.ID}} {{.Names}} {{.StartedAt}}'],text=True)
 print('PASS actual Podman direct-RW restore, account collisions, set-ID mode, hardlinks, ACL, backup account/home exclusion, source identity preservation, stop/start, existing guests unchanged')
except Exception:
 print('Owned diagnostic root:',root)
 raise
finally:
 try:
  for cid in pod('ps','-aq').splitlines():pod('rm','-f',cid)
 except Exception as e:print('Private-store cleanup error:',type(e).__name__)
 shutil.rmtree(root)
