"""Real Libreswan negotiation in disposable network+mount namespaces only."""
import importlib.machinery,importlib.util,json,re,os,pathlib,shutil,signal,subprocess,tempfile,time,uuid
P=pathlib.Path;root=P(tempfile.mkdtemp(prefix='mykos-ike-nat133-'));root.chmod(0o700)
source=P('/home/mgreen/mykos-network133-test-source');prefix='mi'+uuid.uuid4().hex[:7];names={x:prefix+x for x in ('a','d','h','b')};created=[];daemons={};logs={}
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
 def pair(a,an,b,bn):
  left=prefix+a+an;right=prefix+b+bn
  cmd(['ip','link','add',left,'type','veth','peer','name',right])
  cmd(['ip','link','set',left,'netns',names[a]]);cmd(['ip','link','set',right,'netns',names[b]])
  ns(a,'ip','link','set',left,'name',an);ns(b,'ip','link','set',right,'name',bn)
  ns(a,'ip','link','set',an,'up');ns(b,'ip','link','set',bn,'up')
 pair('a','eth0','h','va');pair('d','eth0','h','vd');pair('h','wan','b','wan')
 ns('h','ip','link','add','vpnbr','type','bridge');ns('h','ip','link','set','vpnbr','up')
 for dev in ('va','vd'):ns('h','ip','link','set',dev,'master','vpnbr')
 for side,dev,address in [('a','eth0','192.168.22.2/24'),('d','eth0','192.168.22.3/24'),('h','vpnbr','192.168.22.1/24'),('h','wan','198.18.0.1/30'),('b','wan','198.18.0.2/30'),('b','wan','198.18.0.6/32')]:ns(side,'ip','addr','add',address,'dev',dev)
 for side in ('a','d'):ns(side,'ip','route','add','default','via','192.168.22.1')
 ns('h','ip','route','add','default','via','198.18.0.2');ns('h','sysctl','-q','-w','net.ipv4.ip_forward=1')
 ns('h','iptables','-t','nat','-A','POSTROUTING','-s','192.168.22.0/24','-o','wan','-j','MASQUERADE')
 ns('h','iptables','-P','FORWARD','DROP');ns('h','iptables','-A','FORWARD','-i','vpnbr','-o','wan','-s','192.168.22.0/24','-j','ACCEPT')
 ns('h','iptables','-A','FORWARD','-i','wan','-o','vpnbr','-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT')
 for addr in ('10.77.0.1/24','10.88.0.1/24','203.0.113.10/32'):ns('b','ip','addr','add',addr,'dev','lo')
 ns('b','ip','route','add','192.168.22.0/24','via','198.18.0.1')
 for side in ('a','b','d'):
  folder=root/side;folder.mkdir(mode=0o700);(folder/'ipsec.d').mkdir(mode=0o700);(folder/'nss').mkdir(mode=0o700)
  cmd(['certutil','-N','--empty-password','-d','sql:'+str(folder/'nss')])
  config='config setup\n  protostack=netkey\n  dnssec-enable=no\n  logfile='+str(folder/'pluto.log')+'\n\ninclude /etc/ipsec.d/*.conn.conf\n'
  (folder/'ipsec.conf').write_text(config);(folder/'ipsec.secrets').write_text('include /etc/ipsec.d/*.secrets\n');(folder/'ipsec.secrets').chmod(0o600)
 a=root/'a';b=root/'b'
 writer.save_connection(a,'office','198.18.0.2','192.168.22.2/32','10.77.0.0/24',secret,local_id='guest.test',remote_id='198.18.0.2',native_directory=a/'ipsec.d',full_tunnel_hook=(source/'vpn-updown').read_text())
 writer.save_connection(a,'second','198.18.0.6','192.168.22.2/32','10.88.0.0/24',secret,local_id='guest.test',remote_id='198.18.0.6',native_directory=a/'ipsec.d',full_tunnel_hook=(source/'vpn-updown').read_text())
 # Preserve the generated configuration except for selecting this namespace's
 # explicit WAN address in place of its route lookup; real guest uses default route.
 for path in (a/'ipsec.d').glob('*.conn.conf'):path.write_text(path.read_text().replace('auto=start','auto=add').replace('dpddelay=30s','dpddelay=3s').replace('retransmit-timeout=45s','retransmit-timeout=6s'))
 d=root/'d';second_secret='test-only-'+uuid.uuid4().hex
 writer.save_connection(d,'office','198.18.0.2','192.168.22.3/32','10.77.0.0/24',second_secret,local_id='guest-two.test',remote_id='198.18.0.2',native_directory=d/'ipsec.d',full_tunnel_hook=(source/'vpn-updown').read_text())
 for path in (d/'ipsec.d').glob('*.conn.conf'):path.write_text(path.read_text().replace('auto=start','auto=add').replace('dpddelay=30s','dpddelay=3s').replace('retransmit-timeout=45s','retransmit-timeout=6s'))
 for name,subnet in [('office','10.77.0.0/24'),('second','10.88.0.0/24')]:
  text=('conn '+name+'\n  ikev2=insist\n  authby=secret\n  left=198.18.0.2\n  leftid=198.18.0.2\n  leftsubnet='+subnet+'\n  right=198.18.0.1\n  rightid=@guest.test\n  rightsubnet=192.168.22.2/32\n  ike=aes128-sha1-dh21\n  esp=aes128-sha1\n  pfs=no\n  leftupdown=/bin/true\n  dpddelay=3s\n  retransmit-timeout=6s\n  auto=add\n')
  text+='\n'+text.replace('conn '+name+'\n','conn '+name+'-mykos-default\n',1).replace('  leftsubnet='+subnet+'\n','  leftsubnet=0.0.0.0/0\n',1)
  if name=='second':text=text.replace('left=198.18.0.2','left=198.18.0.6').replace('leftid=198.18.0.2','leftid=198.18.0.6')
  (b/'ipsec.d'/(name+'.conn.conf')).write_text(text)
 text=(b/'ipsec.d/office.conn.conf').read_text().replace('conn office','conn guest-two').replace('@guest.test','@guest-two.test').replace('rightsubnet=192.168.22.2/32','rightsubnet=192.168.22.3/32')
 (b/'ipsec.d/guest-two.conn.conf').write_text(text)
 (b/'ipsec.d/peer.secrets').write_text('198.18.0.2 @guest.test : PSK "'+secret+'"\n198.18.0.6 @guest.test : PSK "'+secret+'"\n198.18.0.2 @guest-two.test : PSK "'+second_secret+'"\n');(b/'ipsec.d/peer.secrets').chmod(0o600)
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
 start('b');start('a');start('d')
 for name in ('office','office-mykos-default','second','second-mykos-default','guest-two','guest-two-mykos-default'):inside('b','ipsec','auto','--add',name)
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
 inside('d','ipsec','auto','--add','office');inside('d','ipsec','auto','--up','office',timeout=30)
 def ping_two():return inside('d','ping','-n','-I','192.168.22.3','-c','1','-W','1','203.0.113.10',check=False).returncode==0
 wait_for(ping_two,20)
 assert ping('203.0.113.10'),'The second NAT guest replaced the first guest tunnel'
 print('TWO_GUESTS_SAME_NAT_PUBLIC_IP_UNIQUE_IKE_IDS_PASS',flush=True)

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
 assert ping_two(),'Restarting guest one broke guest two sharing the same public NAT IP'
 print('RESTART_GUARD_AND_RECONNECT_PASS',flush=True)
 print('ONE_GUEST_TWO_PUBLIC_ENDPOINTS_AND_DISTINCT_DEFAULTS_PASS',flush=True)
 # Real peer loss: accelerated test-only IKE liveness timers remove stale SAs.
 ns('b','ip','link','set','wan','down')
 def no_sa():return 'proto esp' not in inside('a','ip','xfrm','state','list','nokeys').stdout
 wait_for(no_sa,45)
 peer=daemons['b'];os.killpg(peer.pid,signal.SIGTERM);peer.wait(timeout=10);logs['b'].close()
 ns('b','ip','link','set','wan','up')
 # Restore the fixture's static return route after its WAN interface cycle.
 # This models a router retaining/reapplying its configured routes.
 ns('b','ip','route','replace','192.168.22.0/24','via','198.18.0.1')
 # The plain Internet-test address is reachable, but a protected guest must not leak.
 assert ns('h','ping','-n','-c','1','-W','1','203.0.113.10',check=False).returncode==0
 assert not ping('203.0.113.10'),'Peer loss leaked protected guest traffic in cleartext'
 start('b')
 for connection in ('second','second-mykos-default','guest-two','guest-two-mykos-default'):inside('b','ipsec','auto','--add',connection)
 wait_for(lambda:full() and ping('203.0.113.10') and ping_two(),60)
 print('REAL_PEER_LOSS_DPD_FAIL_CLOSED_AND_AUTORECONNECT_PASS',flush=True)
 print('PRIVATE_DIAGNOSTICS',root,flush=True)
except Exception:
 for side in ('a','d','b','h'):
  for args in [('ip','route','show','table','all'),('ip','xfrm','policy'),('ip','xfrm','state','list','nokeys'),('iptables-save','-c'),('cat','/proc/net/xfrm_stat')]:
   try:print('FAIL_DIAGNOSTIC',side,args,ns(side,*args,check=False).stdout,flush=True)
   except Exception:pass
 raise
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
