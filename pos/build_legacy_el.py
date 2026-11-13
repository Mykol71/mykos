#!/usr/bin/env python3
"""Build final EL6/EL7 native guests from vendor-signed RPMs in an isolated worker."""
import hashlib,json,os,re,shutil,signal,subprocess,tempfile,uuid
from pathlib import Path
BASE='registry.access.redhat.com/ubi7/ubi:7.9-1445'
HERE=Path(__file__).resolve().parent

def run(args,timeout=1800):
 result=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
 if result.returncode:raise RuntimeError((result.stdout+result.stderr)[-5000:])
 return result.stdout.strip()
def build(os_name,tag=None):
 if os.geteuid()!=0:raise ValueError('Root is required')
 catalog=json.loads((HERE/'legacy_el.json').read_text())
 if os_name not in catalog:raise ValueError('Select a supported legacy EL guest')
 if catalog[os_name]['major']==6 and 'vsyscall=none' in Path('/proc/cmdline').read_text().split():raise ValueError('This host disables the legacy vsyscall ABI required by EL6; no host kernel settings were changed')
 tag=tag or 'localhost/'+os_name+':latest'
 if not re.fullmatch(r'localhost/[a-z0-9][a-z0-9._/-]*:[a-z0-9._-]+',tag):raise ValueError('Use a local image tag')
 exists=lambda ref:subprocess.run(['podman','image','exists',ref],capture_output=True).returncode==0
 if exists(tag):raise ValueError('Image already exists; remove it explicitly if unreferenced')
 os.setpriority(os.PRIO_PROCESS,0,max(15,os.getpriority(os.PRIO_PROCESS,0)))
 run(['ionice','-c','2','-n','7','-p',str(os.getpid())],timeout=30)
 owned=uuid.uuid4().hex;name='mykos-legacy-build-'+owned;prior=exists(BASE);success=False;created_id=None
 def interrupted(sig,frame):raise InterruptedError('Legacy image build interrupted')
 signals={sig:signal.signal(sig,interrupted) for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
 try:
  if not prior:run(['podman','pull','--arch','amd64','--tls-verify=true',BASE],timeout=300)
  value=json.loads(run(['podman','image','inspect',BASE],timeout=30))[0];digest=value['Digest']
  if not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):raise ValueError('Worker digest unavailable')
  with tempfile.TemporaryDirectory(prefix='mykos-legacy-'+os_name+'-',dir='/var/tmp') as temp:
   work=Path(temp);(work/'owner.json').write_text(json.dumps({'format':1,'run_id':owned,'worker_name':name,'os':os_name,'image_tag':tag,'pid':os.getpid(),'process_start':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]}));inputs=work/'input';inputs.mkdir();stage=work/'stage';stage.mkdir()
   for file in ('legacy_el.json','legacy_worker.py'):shutil.copy2(HERE/file,inputs/file)
   shutil.copytree(HERE/'legacy-keys',inputs/'legacy-keys');shutil.copy2('/etc/pki/tls/certs/ca-bundle.crt',inputs/'build-ca.crt')
   credentials=[]
   if catalog[os_name]['distribution']=='rhel':
    for source,target in [('/etc/pki/entitlement','/run/mykos-entitlement'),('/etc/rhsm/ca','/run/mykos-rhsm-ca')]:
     if not Path(source).is_dir():raise ValueError('RHEL guest requires existing host entitlement')
     credentials+=['--volume',source+':'+target+':ro']
   try:run(['podman','run','--rm','--name',name,'--label','io.mykos.legacy-build='+owned,'--cpus=1','--memory=2g','--memory-swap=2g','--pids-limit=256','--cpu-shares=64','--cgroup-conf=io.weight=default 10','--oom-score-adj=500','--cap-add=MKNOD','--security-opt=label=disable','--volume',str(inputs)+':/input:ro','--volume',str(stage)+':/stage:rw',*credentials,BASE.rsplit(':',1)[0]+'@'+digest,'/usr/bin/python','/input/legacy_worker.py',os_name],timeout=1800)
   finally:
    if subprocess.run(['podman','container','exists',name],capture_output=True).returncode==0:
     worker=json.loads(run(['podman','inspect',name],timeout=30))[0]
     if (worker.get('Config',{}).get('Labels') or {}).get('io.mykos.legacy-build')!=owned:raise RuntimeError('Worker ownership changed')
     run(['podman','stop','--time','15',name],timeout=30)
     if subprocess.run(['podman','container','exists',name],capture_output=True).returncode==0:run(['podman','rm',name],timeout=30)
   archive=stage/'rootfs.tar'
   if not archive.is_file() or archive.stat().st_size<1024:raise ValueError('Guest root filesystem missing')
   major=catalog[os_name]['major'];init='upstart' if major==6 else 'systemd'
   changes=['--change','CMD ["/sbin/init"]','--change','ENV container=podman']
   for key,value in {'io.mykos.os':os_name,'io.mykos.init':init,'io.mykos.os.version':catalog[os_name]['version'],'io.mykos.legacy':'true','io.mykos.build.worker':BASE.rsplit(':',1)[0]+'@'+digest}.items():changes+=['--change','LABEL '+key+'='+value]
   changes+=['--change','STOPSIGNAL '+('SIGPWR' if major==6 else 'SIGRTMIN+3')]
   if exists(tag):raise ValueError('Image appeared during build; refusing overwrite')
   image_id=run(['podman','import',*changes,str(archive),tag],timeout=180)
   created_id=image_id
   imported=json.loads(run(['podman','image','inspect',tag],timeout=30))[0]
   labels=imported.get('Labels') or imported.get('Config',{}).get('Labels') or {}
   if any(labels.get(k)!=v for k,v in {'io.mykos.os':os_name,'io.mykos.init':init,'io.mykos.os.version':catalog[os_name]['version']}.items()):raise RuntimeError('Imported guest identity mismatch')
   receipt={'os':os_name,'version':catalog[os_name]['version'],'image':tag,'image_id':image_id,'rootfs_sha256':hashlib.file_digest(archive.open('rb'),'sha256').hexdigest(),'worker':BASE.rsplit(':',1)[0]+'@'+digest,'packages':len((stage/'packages.txt').read_text().splitlines()),'rpm_signatures':'required; pinned vendor keys','legacy':True,'kernel':'host shared','init':init}
   success=True;return receipt
 finally:
  for sig,old in signals.items():signal.signal(sig,old)
  if subprocess.run(['podman','container','exists',name],capture_output=True).returncode==0:
   value=json.loads(run(['podman','inspect',name],timeout=30))[0]
   if (value.get('Config',{}).get('Labels') or {}).get('io.mykos.legacy-build')!=owned:raise RuntimeError('Worker ownership changed; cleanup refused')
   subprocess.run(['podman','stop','--time','15',name],check=True,capture_output=True,timeout=30)
   if subprocess.run(['podman','container','exists',name],capture_output=True).returncode==0:run(['podman','rm',name],timeout=30)
  if not success and created_id and exists(tag):
   # Import is the only operation that may have made this initially absent tag.
   # Refuse to remove any externally-created image with a different OS label.
   value=json.loads(run(['podman','image','inspect',tag],timeout=30))[0]
   if value.get('Id','').removeprefix('sha256:')==created_id.removeprefix('sha256:'):run(['podman','rmi',tag],timeout=60)
  if not prior and exists(BASE):run(['podman','rmi',BASE],timeout=60)
if __name__=='__main__':
 import sys
 if len(sys.argv)!=2:raise SystemExit('Usage: build_legacy_el.py OS')
 try:print(json.dumps(build(sys.argv[1])))
 except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as error:raise SystemExit(str(error))
