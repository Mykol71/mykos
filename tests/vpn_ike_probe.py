"""Real Libreswan negotiation in disposable network+mount namespaces only."""
import importlib.machinery,importlib.util,json,re,os,pathlib,shutil,signal,subprocess,tempfile,time,uuid
P=pathlib.Path;root=P(tempfile.mkdtemp(prefix='mykos-ike133-'));root.chmod(0o700)
source=P('/home/mgreen/mykos-network133-test-source');prefix='mi'+uuid.uuid4().hex[:7];names={x:prefix+x for x in ('a','b')};created=[];daemons={};logs={}
def cmd(args,check=True,timeout=20,**kw):
 p=subprocess.run(args,text=True,capture_output=True,timeout=timeout,**kw)
 if check and p.returncode:raise RuntimeError(str(args[:5])+': '+(p.stderr+p.stdout)[-1500:])
 return p

def ns(side,*args,**kw):return cmd(['ip','netns','exec',names[side],*args],**kw)
def inside(side,*args,**kw):return cmd(['nsenter','--target',str(daemons[side].pid),'--mount','--net','--',*args],**kw)
def wait_for(fn,seconds=20):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  if fn():return
  time.sleep(.2)
 raise RuntimeError('Timed out waiting for isolated IKE state')

loader=importlib.machinery.SourceFileLoader('probe_vpn_writer',str(source/'host-vpn'));spec=importlib.util.spec_from_loader(loader.name,loader);writer=importlib.util.module_from_spec(spec);loader.exec_module(writer)
secret='test-only-'+uuid.uuid4().hex
try:
 for side,name in names.items():
  cmd(['ip','netns','add',name]);created.append(name)
  ns(side,'ip','link','set','lo','up')
 left=prefix+'l';right=prefix+'r';cmd(['ip','link','add',left,'type','veth','peer','name',right])
 for side,dev,addr in [('a',left,'198.18.0.1/30'),('b',right,'198.18.0.2/30')]:
  cmd(['ip','link','set',dev,'netns',names[side]]);ns(side,'ip','link','set',dev,'name','wan');ns(side,'ip','link','set','wan','up');ns(side,'ip','addr','add',addr,'dev','wan')
 ns('a','ip','addr','add','192.168.22.2/32','dev','lo');ns('a','ip','route','add','default','via','198.18.0.2')
 for addr in ('10.77.0.1/24','10.88.0.1/24','203.0.113.10/32'):ns('b','ip','addr','add',addr,'dev','lo')
 ns('b','ip','route','add','192.168.22.2/32','via','198.18.0.1')
 for side in names:
  folder=root/side;folder.mkdir(mode=0o700);(folder/'ipsec.d').mkdir(mode=0o700);(folder/'nss').mkdir(mode=0o700)
  cmd(['certutil','-N','--empty-password','-d','sql:'+str(folder/'nss')])
  config='config setup\n  protostack=netkey\n  dnssec-enable=no\n  logfile='+str(folder/'pluto.log')+'\n\ninclude /etc/ipsec.d/*.conn.conf\n'
  (folder/'ipsec.conf').write_text(config);(folder/'ipsec.secrets').write_text('include /etc/ipsec.d/*.secrets\n');(folder/'ipsec.secrets').chmod(0o600)
 a=root/'a';b=root/'b'
 writer.save_connection(a,'office','198.18.0.2','192.168.22.2/32','10.77.0.0/24',secret,local_id='guest.test',remote_id='198.18.0.2',native_directory=a/'ipsec.d',full_tunnel_hook=(source/'vpn-updown').read_text())
 writer.save_connection(a,'second','198.18.0.2','192.168.22.2/32','10.88.0.0/24',secret,local_id='guest.test',remote_id='198.18.0.2',native_directory=a/'ipsec.d',full_tunnel_hook=(source/'vpn-updown').read_text())
 # Preserve the generated configuration except for selecting this namespace's
 # explicit WAN address in place of its route lookup; real guest uses default route.
 for path in (a/'ipsec.d').glob('*.conn.conf'):path.write_text(path.read_text().replace('left=%defaultroute','left=198.18.0.1').replace('auto=start','auto=add'))
 for name,subnet in [('office','10.77.0.0/24'),('second','10.88.0.0/24')]:
  text=('conn '+name+'\n  ikev2=insist\n  authby=secret\n  left=198.18.0.2\n  leftid=198.18.0.2\n  leftsubnet='+subnet+'\n  right=198.18.0.1\n  rightid=@guest.test\n  rightsubnet=192.168.22.2/32\n  ike=aes128-sha1-dh21\n  esp=aes128-sha1\n  pfs=no\n  auto=add\n')
  text+='\n'+text.replace('conn '+name+'\n','conn '+name+'-mykos-default\n',1).replace('  leftsubnet='+subnet+'\n','  leftsubnet=0.0.0.0/0\n',1)
  (b/'ipsec.d'/(name+'.conn.conf')).write_text(text)
 (b/'ipsec.d/peer.secrets').write_text('198.18.0.2 @guest.test : PSK "'+secret+'"\n');(b/'ipsec.d/peer.secrets').chmod(0o600)
 boot=root/'boot.py';boot.write_text('''import os,pathlib,subprocess,sys
p=pathlib.Path(sys.argv[1])
def run(a):subprocess.run(a,check=True,capture_output=True)
run(['mount','-t','tmpfs','-o','mode=755','mykos-private-run','/run']);pathlib.Path('/run/pluto').mkdir()
for src,dst in [(p/'ipsec.d','/etc/ipsec.d'),(p/'ipsec.conf','/etc/ipsec.conf'),(p/'ipsec.secrets','/etc/ipsec.secrets')]:
 run(['mount','--bind',str(src),dst])
pathlib.Path('/run/mykos-private-test').write_text(str(p))
for hook in sorted((p/'ipsec.d').glob('*.updown')):
 if (p/'ipsec.d/.mykos-first-tunnel').exists():run(['/bin/bash',str(hook),'--restore']);break
os.execv('/usr/libexec/ipsec/pluto',['pluto','--nofork','--config',str(p/'ipsec.conf'),'--nssdir',str(p/'nss'),'--secretsfile',str(p/'ipsec.secrets'),'--rundir','/run/pluto','--logfile',str(p/'pluto.log')])
''')
 def start(side):
  folder=root/side;handle=(folder/'daemon.stderr').open('a');logs[side]=handle
  daemons[side]=subprocess.Popen(['ip','netns','exec',names[side],'unshare','--mount','--propagation','private','python3',str(boot),str(folder)],stdout=handle,stderr=handle,start_new_session=True)
  def ready():
   if daemons[side].poll() is not None:return True
   marker=P('/proc')/str(daemons[side].pid)/'root/run/mykos-private-test'
   try:
    if marker.read_text()!=str(folder):return False
   except (OSError,ValueError):return False
   return inside(side,'test','-S','/run/pluto/pluto.ctl',check=False).returncode==0
  wait_for(ready)
  if daemons[side].poll() is not None:raise RuntimeError('Private Pluto exited: '+(folder/'daemon.stderr').read_text()[-1500:])
  inside(side,'ipsec','auto','--ready');inside(side,'ipsec','auto','--rereadsecrets')
 start('b');start('a')
 for name in ('office','office-mykos-default','second','second-mykos-default'):inside('b','ipsec','auto','--add',name)
 inside('a','ipsec','auto','--add','office');inside('a','ipsec','auto','--up','office',timeout=30)
 def full():
  return bool(re.search(r'src 192\.168\.22\.2(?:/32)? dst 0\.0\.0\.0/0\s+dir out',inside('a','ip','xfrm','policy').stdout))
 try:wait_for(full,15)
 except RuntimeError:
  diagnostic=inside('a','ipsec','auto','--add','office-mykos-default',check=False)
  print('DEFAULT_LOAD_DIAGNOSTIC',diagnostic.returncode,(diagnostic.stdout+diagnostic.stderr)[-2000:],flush=True)
  raise
 assert (a/'ipsec.d/.mykos-first-tunnel').read_text().strip()=='office'
 def ping(addr):return inside('a','ping','-n','-I','192.168.22.2','-c','1','-W','1',addr,check=False).returncode==0
 assert ping('203.0.113.10'),'Real negotiated default CHILD_SA did not carry internet test traffic'
 inside('a','ipsec','auto','--add','second');inside('a','ipsec','auto','--up','second',timeout=30)
 assert (a/'ipsec.d/.mykos-first-tunnel').read_text().strip()=='office','A later tunnel stole default ownership'
 assert ping('10.88.0.1')
 # Rekey the selected default CHILD_SA using the real daemon control interface.
 inside('a','ipsec','whack','--name','office-mykos-default','--rekey-ipsec',check=True)
 wait_for(lambda:ping('203.0.113.10'),15)
 # Deleting a primary must not leave the remaining established site with no default.
 inside('a','/bin/bash','/etc/ipsec.d/office.updown','--remove','office')
 inside('a','ipsec','auto','--delete','office')
 for suffix in ('.conn.conf','.secrets','.updown','.vpn-peer'):(a/'ipsec.d'/('office'+suffix)).unlink(missing_ok=True)
 wait_for(lambda:(a/'ipsec.d/.mykos-first-tunnel').exists() and (a/'ipsec.d/.mykos-first-tunnel').read_text().strip()=='second' and ping('203.0.113.10'),20)
 print('NEGOTIATION_REKEY_FIRST_OWNER_PASS',flush=True)
 print('AFTER_PRIMARY_DELETE',{'first':(a/'ipsec.d/.mykos-first-tunnel').read_text().strip() if (a/'ipsec.d/.mykos-first-tunnel').exists() else None,'internet':ping('203.0.113.10')},flush=True)
 # Simulate a guest/Pluto restart: only owned namespace firewall tables reset.
 old=daemons['a'];os.killpg(old.pid,signal.SIGTERM);old.wait(timeout=10);logs['a'].close()
 for binary in ('iptables','ip6tables'):
  ns('a',binary,'-F');ns('a',binary,'-X');ns('a',binary,'-t','nat','-F')
 ns('a','ip','xfrm','policy','flush');ns('a','ip','xfrm','state','flush')
 start('a')
 assert not ping('203.0.113.10'),'Saved VPN guard failed to block cleartext during restart'
 inside('a','ipsec','auto','--add','second');inside('a','ipsec','auto','--up','second',timeout=30)
 wait_for(lambda:full() and ping('203.0.113.10'),20)
 assert (a/'ipsec.d/.mykos-first-tunnel').read_text().strip()=='second'
 print('RESTART_GUARD_AND_RECONNECT_PASS',flush=True)
 print('PRIVATE_DIAGNOSTICS',root,flush=True)
finally:
 for side,process in daemons.items():
  if process.poll() is None:
   os.killpg(process.pid,signal.SIGTERM)
   try:process.wait(timeout=8)
   except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
  logs[side].close()
 for name in reversed(created):
  for pid in cmd(['ip','netns','pids',name],check=False).stdout.split():
   try:os.kill(int(pid),signal.SIGKILL)
   except ProcessLookupError:pass
  cmd(['ip','netns','del',name],check=False)
 # Keep private logs for diagnosis; redact the test-only PSK files afterward.
 for path in root.rglob('*.secrets'):path.unlink()
 print('Owned namespaces removed; private diagnostics:',root,flush=True)
