"""Disposable four-namespace gateway test. Never changes host routes/firewall/guests."""
import json,os,pathlib,subprocess,sys,tempfile,uuid
sys.path[:0]=[str(pathlib.Path(__file__).parent),'/usr/local/sbin']
import mykos_vpn_gateway as g
import mykos_vpn_host as host
prefix='mv'+uuid.uuid4().hex[:7]
names={x:prefix+x for x in ('c','h','g','r')}
created=[];process=None;private_state=None

def cmd(args,ok=True):
 p=subprocess.run(args,capture_output=True,text=True,timeout=15)
 if ok and p.returncode:raise RuntimeError(repr(args)+': '+p.stderr[-2000:])
 return p

def ns(n,*args):return cmd(['ip','netns','exec',names[n],*args])
try:
 for name in names.values():cmd(['ip','netns','add',name]);created.append(name)
 for n in names:ns(n,'ip','link','set','lo','up')
 def pair(a,an,b,bn):
  # Temporary veth ends exist in the host only until moved to their owned namespace.
  left=prefix+a+an;right=prefix+b+bn
  cmd(['ip','link','add',left,'type','veth','peer','name',right])
  cmd(['ip','link','set',left,'netns',names[a]]);cmd(['ip','link','set',right,'netns',names[b]])
  ns(a,'ip','link','set',left,'name',an);ns(b,'ip','link','set',right,'name',bn)
  ns(a,'ip','link','set',an,'up');ns(b,'ip','link','set',bn,'up')
 pair('c','eth0','h','vc');pair('g','eth0','h','vg');pair('g','wan','r','wan');pair('h','upl','r','ctrl')
 ns('h','ip','link','add','name','vpnbr','type','bridge');ns('h','ip','link','set','vpnbr','up')
 for dev in ('vc','vg'):ns('h','ip','link','set',dev,'master','vpnbr')
 for n,dev,addr in [('h','vpnbr','192.168.22.1/24'),('c','eth0','192.168.22.3/24'),('g','eth0','192.168.22.2/24'),('g','wan','198.18.0.1/30'),('r','wan','198.18.0.2/30'),('r','lo','10.77.0.1/24'),('r','lo','203.0.113.10/32'),('r','lo','8.8.8.8/32'),('h','upl','198.19.0.1/30'),('r','ctrl','198.19.0.2/30')]:ns(n,'ip','addr','add',addr,'dev',dev)
 ns('c','ip','route','add','default','via','192.168.22.1')
 ns('h','ip','route','add','default','via','198.19.0.2')
 ns('g','ip','route','add','default','via','198.18.0.2')
 ns('g','ip','route','add','198.19.0.0/30','via','192.168.22.1')
 ns('r','ip','route','add','192.168.22.0/24','via','198.18.0.1')
 ns('h','sysctl','-q','-w','net.ipv4.ip_forward=1')
 ns('h','iptables','-P','FORWARD','DROP')
 ns('h','iptables','-A','FORWARD','-i','vpnbr','-o','upl','-s','192.168.22.2/32','-j','ACCEPT')
 ns('h','iptables','-A','FORWARD','-i','upl','-o','vpnbr','-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT')
 # Model Podman's source NAT; the controller's exact scoped exemption must win.
 ns('h','iptables','-t','nat','-A','POSTROUTING','-s','192.168.22.0/24','!','-d','192.168.22.0/24','-j','MASQUERADE')
 process=subprocess.Popen(['ip','netns','exec',names['g'],'sleep','600'])
 pid=process.pid
 state={'Id':'test-gateway','Name':'12345678.test','Config':{'Labels':{'io.mykos.os':'rh9'}},'State':{'Running':True,'Pid':pid},'NetworkSettings':{'Networks':{'mykos-lan':{'IPAddress':'192.168.22.2'}}}}
 g.inspect_gateway=lambda *args:state
 g.vm.inspect=lambda *args:state
 g.config=lambda:{'native_network':'mykos-lan'}
 def run(args,timeout=10):
  actual=args if args[0]==g.NSENTER else ['ip','netns','exec',names['h'],*args]
  return cmd(actual).stdout.strip()
 g.run=run
 def check(args):
  actual=args if args[0]==g.NSENTER else ['ip','netns','exec',names['h'],*args]
  p=cmd(actual,False)
  if p.returncode not in (0,1):raise RuntimeError(p.stderr)
  return p.returncode==0
 g.checked_command=check
 v={'shop':'12345678','connection':'test','remote':'0.0.0.0/0','local':'192.168.22.0/24','address':'192.168.22.2','bridge':'vpnbr','table':23001,'container_id':'test-gateway'}
 g.apply(v)
 def ping(destination='10.77.0.1'):return cmd(['ip','netns','exec',names['c'],'ping','-n','-c','1','-W','1',destination],False).returncode==0
 assert not ping(),'Cleartext packet must be rejected when IPsec is absent'
 # The gateway's own outer IKE traffic must escape the shared route and receive WAN NAT.
 server_code="import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('198.19.0.2',500));s.settimeout(5);data,peer=s.recvfrom(100);assert peer[0]=='198.19.0.1',peer;s.sendto(data,peer)"
 server=subprocess.Popen(['ip','netns','exec',names['r'],'python3','-c',server_code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 try:
  import time;time.sleep(.2)
  ns('g','python3','-c',"import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('192.168.22.2',0));s.settimeout(3);s.sendto(b'ike-probe',('198.19.0.2',500));assert s.recv(100)==b'ike-probe'")
  assert server.wait(timeout=5)==0,server.stderr.read().decode()
 finally:
  if server.poll() is None:server.kill();server.wait()
 # Isolated kernel ESP tunnel using test-only keys and reserved benchmarking addresses.
 key='0x'+'11'*20
 for n in ('g','r'):
  for src,dst,spi in [('198.18.0.1','198.18.0.2','0x101'),('198.18.0.2','198.18.0.1','0x102')]:
   ns(n,'ip','xfrm','state','add','src',src,'dst',dst,'proto','esp','spi',spi,'reqid','1','mode','tunnel','aead','rfc4106(gcm(aes))',key,'128')
 for n,outsrc,outdst,src,dst in [('g','192.168.22.0/24','0.0.0.0/0','198.18.0.1','198.18.0.2'),('r','0.0.0.0/0','192.168.22.0/24','198.18.0.2','198.18.0.1')]:
  ns(n,'ip','xfrm','policy','add','priority','1000','src',outsrc,'dst',outdst,'dir','out','tmpl','src',src,'dst',dst,'proto','esp','reqid','1','mode','tunnel')
  for direction in ('in','fwd'):
   ns(n,'ip','xfrm','policy','add','priority','1000','src',outdst,'dst',outsrc,'dir',direction,'tmpl','src',dst,'dst',src,'proto','esp','reqid','1','mode','tunnel')
 assert ping(),'Client must reach remote subnet through the gateway ESP tunnel'
 # Host-originated internet packets use this same shared gateway only while enabled.
 with tempfile.TemporaryDirectory(prefix='mykos-host-vpn-probe-') as private:
  g.vm.POS=pathlib.Path(private);directory=g.vm.POS/'.12345678';directory.mkdir()
  (directory/'test.vpn-peer').write_text('198.18.0.2\n')
  namespace='/proc/'+str(os.getpid())+'/fd/'+str(os.open('/proc/'+str(pid)+'/ns/net',os.O_RDONLY))
  try:assert host.full_tunnel_active(v,namespace),'Actual kernel selectors/state must confirm a full tunnel'
  finally:os.close(int(namespace.rsplit('/',1)[1]))
 def host_echo(expected):
  code="import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('203.0.113.10',4242));s.settimeout(5);data,peer=s.recvfrom(100);assert peer[0]=="+repr(expected)+",peer;s.sendto(data,peer)"
  server=subprocess.Popen(['ip','netns','exec',names['r'],'python3','-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  try:
   import time;time.sleep(.15)
   ns('h','python3','-c',"import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('198.19.0.1',0));s.settimeout(3);s.sendto(b'host-internet',('203.0.113.10',4242));assert s.recv(100)==b'host-internet'")
   assert server.wait(timeout=5)==0,server.stderr.read().decode()
  finally:
   if server.poll() is None:server.kill();server.wait()
   if server.returncode:
    print('UDP server diagnostic:',server.stderr.read().decode(),flush=True)
    for n,args in [('h',['iptables','-t','mangle','-nvL']),('h',['iptables','-t','nat','-nvL']),('h',['iptables','-nvL','FORWARD']),('h',['ip','xfrm','policy','list']),('g',['iptables','-nvL','FORWARD']),('h',['ip','route','get','203.0.113.10','from','198.19.0.1','mark','0x4d590000']),('h',['sysctl','net.ipv4.conf.all.rp_filter','net.ipv4.conf.vpnbr.rp_filter'])]:
     print(n,args,ns(n,*args).stdout,flush=True)
 def dns_echo(client='h',protocol='udp',expected='192.168.22.1',blocked=False):
  # The resolver lives ONLY inside the remote namespace. No Internet request.
  socket_type='SOCK_DGRAM' if protocol=='udp' else 'SOCK_STREAM'
  code="import socket;s=socket.socket(socket.AF_INET,socket."+socket_type+");s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('8.8.8.8',53));s.settimeout(3);"
  if protocol=='udp':code+="data,peer=s.recvfrom(100);assert peer[0]=="+repr(expected)+",peer;s.sendto(data,peer)"
  else:code+="s.listen(1);c,peer=s.accept();assert peer[0]=="+repr(expected)+",peer;data=c.recv(100);c.sendall(data);c.close()"
  server=subprocess.Popen(['ip','netns','exec',names['r'],'python3','-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  try:
   import time;time.sleep(.12)
   # Query a LAN/bridge resolver: DNAT must redirect it to 8.8.8.8 and the
   # exact remote-observed source proves the protected guest/VPN path.
   target='198.19.0.2' if client=='h' else '192.168.22.1'
   code="import socket;s=socket.socket(socket.AF_INET,socket."+socket_type+");s.settimeout(1);s.connect(("+repr(target)+",53));s.send(b'vpn-dns-probe');assert s.recv(100)==b'vpn-dns-probe'"
   result=cmd(['ip','netns','exec',names[client],'python3','-c',code],False)
   if blocked:
    assert result.returncode!=0,'Guest DNS escaped during SA outage'
   else:
    if result.returncode:
     print('DNS failure',client,protocol,flush=True)
     for n,command in [('h',['iptables','-t','mangle','-nvL']),('h',['iptables','-t','nat','-nvL']),('h',['iptables','-nvL','FORWARD']),('h',['ip','xfrm','policy','list']),('g',['iptables','-nvL','FORWARD']),('h',['ip','route','get','8.8.8.8','from','198.19.0.1','mark','0x4d590000']),('g',['ip','-s','xfrm','state','list','nokeys'])]:
      print(n,command,ns(n,*command).stdout,flush=True)
    if result.returncode and server.poll() is not None:print('DNS server:',server.stderr.read().decode(),flush=True)
    assert result.returncode==0,result.stderr
    assert server.wait(timeout=4)==0,server.stderr.read().decode()
  finally:
   if server.poll() is None:server.kill();server.wait()
 host_echo('198.19.0.1')
 networks=['127.0.0.0/8','192.168.22.0/24','198.19.0.0/30']
 private_state=tempfile.TemporaryDirectory(prefix='mykos-host-state128-');g.ROOT=pathlib.Path(private_state.name)
 host.update(v,True)
 host_echo('192.168.22.1')
 for protocol in ('udp','tcp'):
  dns_echo(protocol=protocol);dns_echo(client='c',protocol=protocol,expected='192.168.22.1')
 host.update(v,True);host_echo('192.168.22.1')
 host.update(v,False)
 assert ns('h','sysctl','-n','net.ipv4.conf.vpnbr.rp_filter').stdout.strip()=='1','Restore exact original bridge RPF'
 host_echo('198.19.0.1')
 print('PASS: host full-SA verification, marked internet diversion and source translation, idempotence, normal WAN restoration',flush=True)

 assert ping('203.0.113.10'),'Default traffic beyond the site subnet must cross ESP'
 print('Owned rules:',ns('h','ip','-N','-j','rule','show').stdout,flush=True)
 print('Owned table:',ns('h','ip','-j','route','show','table',str(v['table'])).stdout,flush=True)
 g.apply(v);assert ping() and ping('203.0.113.10'),'Reconcile must be idempotent'
 for n in ('g','r'):ns(n,'ip','xfrm','state','flush')
 assert not ping('203.0.113.10'),'Lost ESP state must not leak default traffic'
 for protocol in ('udp','tcp'):dns_echo(client='c',protocol=protocol,blocked=True)
 # Reinstall the test states without replacing policies.
 for n in ('g','r'):
  for src,dst,spi in [('198.18.0.1','198.18.0.2','0x101'),('198.18.0.2','198.18.0.1','0x102')]:
   ns(n,'ip','xfrm','state','add','src',src,'dst',dst,'proto','esp','spi',spi,'reqid','1','mode','tunnel','aead','rfc4106(gcm(aes))',key,'128')
 state['State']['Running']=False;g.apply(v);assert not ping(),'Stopped gateway must fail closed'
 state['State']['Running']=True;g.apply(v);assert ping(),'Restarted gateway must recover without changing client routing'
 print('PASS: gateway outer IKE bypass/WAN NAT, full-default and site ESP forwarding, lost-SA rejection, encrypted forwarding, scoped NAT exemption, no-SA cleartext rejection, idempotence, stopped-gateway block and recovery')
 # Repeat using the MYKOS host itself as the IPsec terminator (scenario 2A).
 g.firewall(v,pid,remove=True);g.policy(v,remove=True)
 ns('h','ip','route','flush','table',str(v['table']),'proto','242')
 for n in ('g','r'):
  ns(n,'ip','xfrm','state','flush');ns(n,'ip','xfrm','policy','flush')
 g.HOST_IPSEC=pathlib.Path(private_state.name)/'host-ipsec';g.HOST_IPSEC.mkdir()
 (g.HOST_IPSEC/'test.vpn-peer').write_text('198.19.0.2\n')
 vh={**v,'shop':'host','mode':'host','container_id':'host','address':'192.168.22.1','table':23009}
 g.validate_profile(vh)
 g.apply_host(vh)
 assert not ping('203.0.113.10'),'Host VPN mode must block guest plaintext without an SA'
 for n in ('h','r'):
  for src,dst,spi in [('198.19.0.1','198.19.0.2','0x201'),('198.19.0.2','198.19.0.1','0x202')]:
   ns(n,'ip','xfrm','state','add','src',src,'dst',dst,'proto','esp','spi',spi,'reqid','2','mode','tunnel','aead','rfc4106(gcm(aes))',key,'128')
 for n,outsrc,outdst,src,dst in [('h','192.168.22.0/24','0.0.0.0/0','198.19.0.1','198.19.0.2'),('r','0.0.0.0/0','192.168.22.0/24','198.19.0.2','198.19.0.1')]:
  ns(n,'ip','xfrm','policy','add','priority','1000','src',outsrc,'dst',outdst,'dir','out','tmpl','src',src,'dst',dst,'proto','esp','reqid','2','mode','tunnel')
  for direction in ('in','fwd'):
   ns(n,'ip','xfrm','policy','add','priority','1000','src',outdst,'dst',outsrc,'dir',direction,'tmpl','src',dst,'dst',src,'proto','esp','reqid','2','mode','tunnel')
 assert g.apply_host(vh)['full_tunnel_sa'] is True
 assert ping('203.0.113.10'),'Guest traffic through host IPsec must succeed'
 host_echo('192.168.22.1')
 for protocol in ('udp','tcp'):
  dns_echo(protocol=protocol);dns_echo(client='c',protocol=protocol,expected='192.168.22.1')
 g.apply_host(vh);host_echo('192.168.22.1')
 for n in ('h','r'):
  ns(n,'ip','xfrm','state','flush');ns(n,'ip','xfrm','policy','flush')
 assert g.apply_host(vh)['full_tunnel_sa'] is False
 assert not ping('203.0.113.10'),'Guest traffic must stay blocked when host IPsec is down'
 for protocol in ('udp','tcp'):dns_echo(client='c',protocol=protocol,blocked=True)
 host_echo('198.19.0.1')
 g.firewall(vh,None,remove=True)
 print('PASS: UDP and TCP guest/host DNS redirect through ESP in both shared modes; guest DNS cannot bypass during SA outage',flush=True)
 print('PASS: scenario2A host IPsec carries guest and host internet, idempotent activation, no-SA guest block and normal host WAN fallback',flush=True)

finally:
 if private_state:private_state.cleanup()
 if process:
  process.terminate()
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 for name in reversed(created):cmd(['ip','netns','del',name],False)
 print('Removed all owned test namespaces')
