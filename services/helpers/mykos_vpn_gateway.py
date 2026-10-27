"""Opt-in native Linux VPN gateway; host-side policy routing for all VM/container types."""
import contextlib,hashlib,ipaddress,json,os,re,subprocess,time
from pathlib import Path
import mykos_vm as vm
import mykos_network as network
from mykos_runtime import config,run,shopcode,target

ROOT=Path('/var/lib/mykos/vpn-gateways')
HOST_IPSEC=Path('/etc/ipsec.d')
IP='/usr/sbin/ip'
IPTABLES='/usr/sbin/iptables'
NSENTER='/usr/bin/nsenter'


def key(shop,connection):
    if shop!='host':shopcode(shop)
    limit=69 if shop=='host' and isinstance(connection,str) and connection.startswith('mykos-') else 63
    if not isinstance(connection,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,'+str(limit)+'}',connection):raise ValueError('Invalid connection name')
    return shop+'-'+connection


def profiles():
    if not ROOT.exists():return []
    vm.safe_dir(ROOT)
    result=[]
    for p in sorted(ROOT.glob('*.json')):
        if p.is_symlink():raise ValueError('Unsafe gateway profile')
        v=json.loads(p.read_text())
        validate_profile(v)
        if p.stem!=key(v['shop'],v['connection']):raise ValueError('Invalid gateway profile')
        result.append(v)
    return result


def validate_profile(v):
    required={'shop','connection','remote','local','address','bridge','table','container_id'}
    if not isinstance(v,dict) or not required<=set(v) or set(v)-required-{'site_remote','mode'}:
        raise ValueError('Invalid gateway profile fields')
    key(v['shop'],v['connection'])
    for field in ('remote','local'):
        if not isinstance(v[field],str) or str(ipaddress.IPv4Network(v[field],strict=True))!=v[field]:
            raise ValueError('Invalid gateway network')
    if 'site_remote' in v:ipaddress.IPv4Network(v['site_remote'],strict=True)
    if ipaddress.IPv4Address(v['address']) not in ipaddress.IPv4Network(v['local']):
        raise ValueError('Gateway address is outside its guest network')
    if not isinstance(v['bridge'],str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}',v['bridge']):
        raise ValueError('Invalid gateway bridge')
    if v.get('mode','guest') not in ('guest','host'):raise ValueError('Invalid shared gateway mode')
    if v.get('mode')=='host' and (v['shop']!='host' or v['container_id']!='host'):raise ValueError('Invalid host gateway identity')
    if v.get('mode')!='host' and (not isinstance(v['container_id'],str) or not re.fullmatch(r'[a-f0-9]{64}',v['container_id'])):
        raise ValueError('Invalid gateway container identity')
    if type(v['table']) is not int or not 20000<=v['table']<40000:
        raise ValueError('Invalid gateway route table')


@contextlib.contextmanager
def pinned_namespace(state):
    # Hold the namespace itself, rather than repeatedly resolving a reusable PID.
    pid=state.get('State',{}).get('Pid')
    if type(pid) is not int or pid<=1:raise ValueError('Invalid gateway process')
    fd=os.open('/proc/'+str(pid)+'/ns/net',os.O_RDONLY|os.O_CLOEXEC)
    try:
        current=vm.inspect(state['Id'])
        if current.get('Id')!=state['Id'] or current.get('State',{}).get('Pid')!=pid or not current.get('State',{}).get('Running'):
            raise ValueError('Gateway changed while opening its network namespace')
        latest=os.stat('/proc/'+str(pid)+'/ns/net')
        original=os.fstat(fd)
        if (original.st_dev,original.st_ino)!=(latest.st_dev,latest.st_ino):
            raise ValueError('Gateway network namespace changed')
        yield '/proc/'+str(os.getpid())+'/fd/'+str(fd)
    finally:os.close(fd)


def namespace_prefix(namespace):
    if isinstance(namespace,str):return [NSENTER,'--net='+namespace,'--']
    return [NSENTER,'--target',str(namespace),'--net','--']


def save(value):
    vm.safe_dir(ROOT)
    path=ROOT/(key(value['shop'],value['connection'])+'.json')
    from mykos_image_build import write
    write(path,json.dumps(value)+'\n')


def inspect_gateway(shop,expected=None):
    name=target(shop);state=vm.inspect(name)
    labels=state.get('Config',{}).get('Labels') or {}
    if labels.get('io.mykos.vm.os') or not labels.get('io.mykos.os'):
        raise ValueError('Shared VPN gateway must be a native Linux guest')
    if expected and state['Id']!=expected:raise ValueError('Gateway container identity changed; reconfigure explicitly')
    return state


def bridge(settings):
    if settings.get('native_network_driver','bridge')!='bridge':raise ValueError('Shared VPN mode requires the MYKOS private NAT bridge')
    info=json.loads(run([vm.PODMAN,'network','inspect',network.name(settings)],timeout=10))[0]
    device=info.get('network_interface')
    if not isinstance(device,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}',device):raise ValueError('Cannot identify the MYKOS bridge')
    return device


def rules(v,pid=None):
    """Exact owned rules. No flushing chains or changing global firewall policies."""
    tag='mykos-vpn-'+key(v['shop'],v['connection'])
    local,remote,dev=v['local'],v['remote'],v['bridge']
    host=[('nat','POSTROUTING',['-o',dev,'-s',local,'-d',remote,'-m','comment','--comment',tag,'-j','ACCEPT']),
          ('filter','FORWARD',['-i',dev,'-o',dev,'-s',local,'-d',remote,'-m','comment','--comment',tag,'-j','ACCEPT']),
          ('filter','FORWARD',['-i',dev,'-o',dev,'-s',remote,'-d',local,'-m','comment','--comment',tag,'-j','ACCEPT'])]
    # Redirect guest resolver traffic before local delivery or forwarding. Keep
    # these rules for the profile's whole lifetime, including SA outages, so a
    # query to the bridge resolver cannot escape via the host's WAN fallback.
    dns=[('nat','PREROUTING',['-i',dev,'-s',local,'-p',protocol,'--dport','53',
          '-m','comment','--comment',tag+'-dns','-j','DNAT','--to-destination','8.8.8.8'])
         for protocol in ('udp','tcp')]
    # A native gateway can return directly to a sibling through the bridge,
    # bypassing the host's DNAT conntrack. Source-translate only redirected DNS
    # to the host bridge address so replies reliably traverse reverse NAT.
    dns_source=str(ipaddress.IPv4Network(local).network_address+1)
    dns_return=[('nat','POSTROUTING',['-s',local,'-d','8.8.8.8/32','-p',protocol,'--dport','53',
                '-m','conntrack','--ctstate','DNAT','-m','comment','--comment',tag+'-dns-return',
                '-j','SNAT','--to-source',dns_source]) for protocol in ('udp','tcp')]
    if v.get('mode')=='host':
        host=[('nat','POSTROUTING',['-s',local,'-m','policy','--dir','out','--pol','ipsec','-m','comment','--comment',tag,'-j','ACCEPT']),
              ('filter','FORWARD',['-s',local,'-m','policy','--dir','out','--pol','ipsec','-m','comment','--comment',tag,'-j','ACCEPT']),
              ('filter','FORWARD',['-s',local,'-m','comment','--comment',tag,'-j','REJECT']),
              ('filter','FORWARD',['-d',local,'-m','policy','--dir','in','--pol','ipsec','-m','comment','--comment',tag,'-j','ACCEPT']),
              ('filter','FORWARD',['-d',local,'-m','comment','--comment',tag,'-j','REJECT'])]
        return [([],dns_return+host+dns)]
    # Fail closed if a protected packet has no outbound IPsec policy/SA.
    guest=[('filter','FORWARD',['-s',local,'-d',remote,'-m','policy','--dir','out','--pol','ipsec','-m','comment','--comment',tag,'-j','ACCEPT']),
           ('filter','FORWARD',['-s',local,'-d',remote,'-m','comment','--comment',tag,'-j','REJECT']),
           ('filter','FORWARD',['-s',remote,'-d',local,'-m','policy','--dir','in','--pol','ipsec','-m','comment','--comment',tag,'-j','ACCEPT']),
           ('filter','FORWARD',['-s',remote,'-d',local,'-m','comment','--comment',tag,'-j','REJECT']),
           ('nat','POSTROUTING',['-s',local,'-d',remote,'-m','policy','--dir','out','--pol','ipsec','-m','comment','--comment',tag,'-j','ACCEPT'])]
    return [([],dns_return+host+dns)]+([(namespace_prefix(pid),guest)] if pid else [])


def checked_command(args):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
    if p.returncode not in (0,1):raise RuntimeError('Could not inspect managed firewall rules')
    return p.returncode==0


LOCAL_MARK='0x4d580000/0xffff0000'


def local_input_policy(v, remove=False):
    """Allow clear bridge-local requests without weakening WAN IPsec checks.

    A full inbound 0/0 selector otherwise treats plain sibling-to-host DNS as
    unauthenticated tunnel input. Only the bridge ingress rule sets this mark.
    """
    if v.get('mode')!='host':return
    from mykos_vpn_host import parse_xfrm
    policies=parse_xfrm(run([IP,'xfrm','policy','list'],timeout=5),'policy')
    owned=[]
    for row in policies:
        mark=row.get('mark') or {}
        if mark.get('value')!=0x4d580000:continue
        direction=row.get('dir')
        expected_src,expected_dst=('0.0.0.0/0',v['local']) if direction=='out' else (v['local'],'0.0.0.0/0')
        if mark.get('mask')!=0xffff0000 or row.get('src')!=expected_src or row.get('dst')!=expected_dst or direction not in ('in','fwd','out') or row.get('priority')!=1 or row.get('tmpl') or row.get('action','allow')!='allow':
            raise ValueError('Local VPN input mark is already in use; existing policy retained')
        owned.append(row['dir'])
    if len(owned)!=len(set(owned)):raise ValueError('Ambiguous local VPN input policies')
    for direction in ('in','fwd','out'):
        source,destination=('0.0.0.0/0',v['local']) if direction=='out' else (v['local'],'0.0.0.0/0')
        args=['src',source,'dst',destination,'dir',direction,'mark','0x4d580000','mask','0xffff0000']
        if remove and direction in owned:run([IP,'xfrm','policy','delete',*args],timeout=5)
        elif not remove and direction not in owned:run([IP,'xfrm','policy','add',*args,'priority','1','action','allow'],timeout=5)
    base=[IPTABLES,'-w','5','-t','mangle']
    args=['-i',v['bridge'],'-s',v['local'],'-d',v['local'],'-m','comment','--comment','mykos-vpn-local-'+key(v['shop'],v['connection']),'-j','MARK','--set-xmark',LOCAL_MARK]
    exists=checked_command([*base,'-C','PREROUTING',*args])
    if remove and exists:run([*base,'-D','PREROUTING',*args],timeout=5)
    elif not remove and not exists:run([*base,'-I','PREROUTING','1',*args],timeout=5)
    # Decrypted replies destined for guests must not be recaptured by the
    # full outbound selector after conntrack reverses DNS translation.
    reply=['-o',v['bridge'],'-d',v['local'],'-m','policy','--dir','in','--pol','ipsec',
           '-m','comment','--comment','mykos-vpn-local-reply-'+key(v['shop'],v['connection']),'-j','MARK','--set-xmark',LOCAL_MARK]
    exists=checked_command([*base,'-C','FORWARD',*reply])
    if remove and exists:run([*base,'-D','FORWARD',*reply],timeout=5)
    elif not remove and not exists:run([*base,'-I','FORWARD','1',*reply],timeout=5)


def firewall(v,pid,remove=False):
    local_input_policy(v,remove=remove)
    tag='mykos-vpn-'+key(v['shop'],v['connection'])
    base6=['/usr/sbin/ip6tables','-w','5']
    args6=['-i',v['bridge'],'-m','comment','--comment',tag,'-j','REJECT']
    exists6=checked_command([*base6,'-C','FORWARD',*args6])
    if remove and exists6:run([*base6,'-D','FORWARD',*args6],timeout=10)
    elif not remove and not exists6:run([*base6,'-I','FORWARD','1',*args6],timeout=10)
    for protocol in ('udp','tcp'):
        # No IPv6 tunnel is configured. Also block bridge-local DNS requests,
        # which take INPUT rather than the already protected FORWARD path.
        dns6=['-i',v['bridge'],'-p',protocol,'--dport','53','-m','comment','--comment',tag+'-dns','-j','REJECT']
        exists=checked_command([*base6,'-C','INPUT',*dns6])
        if remove and exists:run([*base6,'-D','INPUT',*dns6],timeout=10)
        elif not remove and not exists:run([*base6,'-I','INPUT','1',*dns6],timeout=10)
    for prefix,items in rules(v,pid):
        # Insert in reverse order so the policy allow precedes the fail-closed reject.
        for table,chain,args in (items if remove else reversed(items)):
            base=[*prefix,IPTABLES,'-w','5','-t',table]
            exists=checked_command([*base,'-C',chain,*args])
            if remove and exists:run([*base,'-D',chain,*args],timeout=10)
            elif not remove and not exists:run([*base,'-I',chain,'1',*args],timeout=10)


def route_state(v):
    routes=[row for row in json.loads(run([IP,'-N','-j','route','show','table','all'],timeout=10)) if str(row.get('table'))==str(v['table'])]
    for row in routes:
        if row.get('dst')=='default':row['dst']='0.0.0.0/0'
    if routes and (len(routes)!=1 or routes[0].get('dst')!=v['remote'] or str(routes[0].get('protocol'))!='242'):
        raise ValueError('Gateway route table contains non-MYKOS routes; it was left unchanged')
    return routes


def policy(v,remove=False):
    wanted={'priority':v['table']-10000,'src':v['local'],'dst':v['remote'],'iif':v['bridge'],'table':v['table']}
    all_rules=json.loads(run([IP,'-N','-j','rule','show'],timeout=10))
    for row in all_rules:
        for field,length in [('src','srclen'),('dst','dstlen')]:
            if row.get(field) and row[field]!='all' and '/' not in row[field]:
                row[field]+='/'+str(row.get(length,32))
    for row in all_rules:
        for field in ('src','dst'):
            if row.get(field,'all')=='all':row[field]='0.0.0.0/0'
    matches=[r for r in all_rules if r.get('priority')==v['table']-10000]
    if matches and (len(matches)!=1 or any(str(matches[0].get(k))!=str(value) for k,value in wanted.items())):
        raise ValueError('Gateway rule priority is already used; existing policy was left unchanged')
    bypass_priority=v['table']-10001
    bypass=[r for r in all_rules if r.get('priority')==bypass_priority]
    if bypass and (len(bypass)!=1 or bypass[0].get('src')!=v['address']+'/32' or bypass[0].get('iif')!=v['bridge'] or str(bypass[0].get('table')) not in ('254','main')):
        raise ValueError('Gateway control-route priority is in use')
    bypass_args=['priority',str(bypass_priority),'from',v['address']+'/32','iif',v['bridge'],'lookup','main']
    if remove and bypass:run([IP,'rule','del',*bypass_args],timeout=10)
    elif not remove and not bypass:run([IP,'rule','add',*bypass_args],timeout=10)
    args=['priority',str(v['table']-10000),'from',v['local'],'to',v['remote'],'iif',v['bridge'],'table',str(v['table'])]
    if remove and matches:run([IP,'rule','del',*args],timeout=10)
    elif not remove and not matches:run([IP,'rule','add',*args],timeout=10)


def apply(v):
    if v.get('mode')=='host':return apply_host(v)
    # Pin the exact original guest, never silently substitute a reused ID/IP.
    state=inspect_gateway(v['shop'],v['container_id'])
    nic=state.get('NetworkSettings',{}).get('Networks',{}).get(network.name(config()),{})
    if state.get('State',{}).get('Running') and nic.get('IPAddress')!=v['address']:
        raise ValueError('Gateway address changed; reconfigure explicitly')
    running=state.get('State',{}).get('Running') and not state.get('State',{}).get('Paused')
    from mykos_vpn_host import full_tunnel_active,update as update_host
    host_active=False
    routes=route_state(v)
    if running:
        # Block the managed route before any forwarding mutation can fail.
        if not routes:
            run([IP,'route','replace','unreachable',v['remote'],'table',str(v['table']),'proto','242'],timeout=10)
        policy(v)
        with pinned_namespace(state) as namespace:
            run([*namespace_prefix(namespace),'/usr/sbin/sysctl','-q','-w','net.ipv4.ip_forward=1'],timeout=10)
            firewall(v,namespace)
            try:host_active=full_tunnel_active(v,namespace)
            except (ValueError,RuntimeError,OSError):host_active=False
        desired=[IP,'route','replace','table',str(v['table']),v['remote'],'via',v['address'],'dev',v['bridge'],'onlink','proto','242']
        matches=routes and routes[0].get('gateway')==v['address'] and routes[0].get('dev')==v['bridge']
    else:
        firewall(v,None)
        desired=[IP,'route','replace','unreachable',v['remote'],'table',str(v['table']),'proto','242']
        matches=routes and routes[0].get('type')=='unreachable'
    if not matches:run(desired,timeout=10)
    # Activate source/destination-scoped forwarding only after the gateway guard is ready.
    policy(v)
    update_host(v,host_active)
    return {'host_internet_via_vpn':host_active,'gateway_running':bool(running),'shop':v['shop'],'connection':v['connection'],'remote':v['remote']}


def apply_host(v):
    from mykos_vpn_host import full_tunnel_active,update as update_host
    routes=route_state(v)
    firewall(v,None)
    active=full_tunnel_active(v)
    if active:
        peer=HOST_IPSEC/(v['connection']+'.vpn-peer')
        endpoint=str(ipaddress.IPv4Address(peer.read_text().strip()))
        rows=json.loads(run([IP,'-j','-4','route','show','default','table','main'],timeout=5))
        usable=[row for row in rows if row.get('dev') and row.get('type','unicast')=='unicast']
        if not usable:raise ValueError('Cannot identify the host default WAN route')
        row=min(usable,key=lambda row:row.get('metric',0));dev=row['dev']
        if dev==v['bridge'] or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}',dev):raise ValueError('Invalid host VPN WAN interface')
        args=[IP,'route','replace','table',str(v['table']),'default','dev',dev,'proto','242']
        if row.get('gateway'):args.extend(['via',str(ipaddress.IPv4Address(row['gateway'])),'onlink'])
        if not routes or routes[0].get('dev')!=dev or routes[0].get('gateway')!=row.get('gateway'):run(args,timeout=5)
    update_host(v,active)
    return {'shop':'host','connection':v['connection'],'mode':'host','full_tunnel_sa':active,'host_internet_via_vpn':active}


def preflight_mode(shop,connection,mode):
    """Keep shared-default and independent-tunnel scenarios explicit."""
    key(shop,connection)
    if mode not in ('direct','shared'):raise ValueError('Choose direct or shared VPN mode')
    items=profiles()
    if mode=='direct':
        if items:raise ValueError('Remove the shared VPN gateway profile before adding independent VPN tunnels; current routing was left unchanged')
    else:
        if any(key(v['shop'],v['connection'])!=key(shop,connection) for v in items):raise ValueError('Another shared VPN gateway is already selected')
        folders=[('host',HOST_IPSEC)]
        folders.extend((p.name[1:],p) for p in vm.POS.glob('.????????') if re.fullmatch(r'\.[0-9]{8}',p.name) and p.is_dir() and not p.is_symlink())
        for owner,folder in folders:
            for path in folder.glob('*.conn.conf'):
                if path.is_symlink():raise ValueError('Unsafe saved VPN configuration')
                if (owner,path.name[:-10])!=(shop,connection):
                    raise ValueError('Shared-default and independent VPN scenarios cannot be combined yet; saved connections were left unchanged')
    return {'state':'ready','mode':mode}


def managed_connection(directory,connection,local,remote):
    path=directory/(connection+'.conn.conf')
    if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:raise ValueError('Create the managed VPN connection first')
    sections={}
    for section in re.split(r'^conn\s+',path.read_text(),flags=re.M)[1:]:
        rows=section.splitlines();name=rows[0].strip()
        pairs=re.findall(r'^\s*(leftsubnet|rightsubnet|right|leftupdown|auto)\s*=\s*(\S+)', '\n'.join(rows[1:]),re.M)
        if len({key for key,value in pairs})!=len(pairs):raise ValueError('Duplicate VPN settings; recreate the managed connection')
        sections[name]=dict(pairs)
    site=sections.get(connection,{})
    default=sections.get(connection+'-mykos-default',{})
    if site.get('leftsubnet')!=local or site.get('rightsubnet')!=remote:raise ValueError('VPN must protect the selected local and remote networks')
    if default.get('leftsubnet')!=local or default.get('rightsubnet')!='0.0.0.0/0' or default.get('leftupdown')!='/bin/true':
        raise ValueError('This VPN lacks a managed full-tunnel connection; recreate it before selecting shared gateway mode')
    peer=directory/(connection+'.vpn-peer');hook=directory/(connection+'.updown')
    if peer.is_symlink() or not peer.is_file() or peer.stat().st_size>64 or hook.is_symlink() or not hook.is_file() or not os.access(hook,os.X_OK):
        raise ValueError('Missing managed VPN endpoint or reconnect hook')
    address=str(ipaddress.IPv4Address(peer.read_text().strip()))
    if site.get('right')!=address or default.get('right')!=address:raise ValueError('Saved VPN peer does not match its managed endpoint')


def configure_host(connection,remote):
    with vm.locked():
        settings=config();dev=bridge(settings);local=str(network.address_pool(settings))
        site_remote=str(ipaddress.IPv4Network(remote,strict=True))
        if ipaddress.ip_network(local).overlaps(ipaddress.ip_network(site_remote)):raise ValueError('Remote subnet overlaps guest network')
        name=key('host',connection);items=profiles()
        if any(key(v['shop'],v['connection'])!=name for v in items):raise ValueError('Remove the other shared gateway before selecting a host VPN gateway')
        preflight_mode('host',connection,'shared')
        managed_connection(HOST_IPSEC,connection,local,site_remote)
        table=20000+int.from_bytes(hashlib.sha256(name.encode()).digest()[:2],'big')%20000
        from mykos_vpn_host import bridge_address
        value={'shop':'host','connection':connection,'mode':'host','local':local,'remote':'0.0.0.0/0','site_remote':site_remote,'address':str(ipaddress.ip_network(local).network_address+1),'bridge':dev,'table':table,'container_id':'host'}
        value['address']=bridge_address(value)
        existing=next((v for v in items if key(v['shop'],v['connection'])==name),None)
        if existing and existing!=value:raise ValueError('Remove the existing host gateway before changing it')
        route_state(value)
        rows=json.loads(run([IP,'-N','-j','rule','show'],timeout=5))
        if not existing and any(row.get('priority') in (table-10002,table-10001,table-10000) for row in rows):raise ValueError('Host VPN routing priority is in use')
        save(value);result=apply_host(value)
        run(['systemctl','enable','mykos-vpn-gateway-guard.service'],timeout=20)
        run(['systemctl','enable','--now','mykos-vpn-gateway.timer'],timeout=20)
        return result


def configure(shop,connection,remote):
    with vm.locked():
        settings=config();dev=bridge(settings);local=str(network.address_pool(settings))
        site_remote=str(ipaddress.IPv4Network(remote,strict=True))
        remote='0.0.0.0/0'
        if ipaddress.ip_network(local).overlaps(ipaddress.ip_network(site_remote)):raise ValueError('Remote subnet overlaps MYKOS guest network')
        name=key(shop,connection);items=profiles()
        if any(ipaddress.ip_network(v['remote']).overlaps(ipaddress.ip_network(remote)) and key(v['shop'],v['connection'])!=name for v in items):
            raise ValueError('Another shared gateway already owns this remote subnet')
        state=inspect_gateway(shop)
        nic=state.get('NetworkSettings',{}).get('Networks',{}).get(network.name(settings),{})
        address=nic.get('IPAddress')
        if not address or ipaddress.ip_address(address) not in ipaddress.ip_network(local):raise ValueError('Start the gateway on the MYKOS NAT bridge first')
        preflight_mode(shop,connection,'shared')
        managed_connection(vm.POS/('.'+shop),connection,local,site_remote)
        existing=next((v for v in items if key(v['shop'],v['connection'])==name),None)
        table=existing['table'] if existing else 20000+int.from_bytes(hashlib.sha256(name.encode()).digest()[:2],'big')%20000
        if any(abs(v['table']-table)<3 and key(v['shop'],v['connection'])!=name for v in items):raise ValueError('Gateway routing identifier collision; choose another connection name')
        value={'shop':shop,'connection':connection,'remote':remote,'site_remote':site_remote,'local':local,'address':address,'bridge':dev,'table':table,'container_id':state['Id']}
        if existing and existing!=value:raise ValueError('Remove the existing shared gateway profile before changing it')
        route_state(value)
        # Validate rule ownership without mutation before persisting or routing traffic.
        existing_rules=json.loads(run([IP,'-N','-j','rule','show'],timeout=10))
        if not existing and any(x.get('priority') in (table-10000,table-10001,table-10002) for x in existing_rules):raise ValueError('Gateway routing identifier is in use')
        save(value)
        result=apply(value)
        run(['systemctl','enable','mykos-vpn-gateway-guard.service'],timeout=20)
        run(['systemctl','enable','--now','mykos-vpn-gateway.timer'],timeout=20)
        return result


def remove(shop,connection):
    with vm.locked():return remove_profile(shop,connection)


def remove_profile(shop,connection,keep_profile=False):
    name=key(shop,connection);v=next((v for v in profiles() if key(v['shop'],v['connection'])==name),None)
    if not v:return {'state':'not_configured'}
    from mykos_vpn_host import update as update_host
    update_host(v,False)
    route_state(v)
    if v.get('mode')!='host':policy(v,remove=True)
    run([IP,'route','flush','table',str(v['table']),'proto','242'],timeout=10)
    pid=None
    try:
        if v.get('mode')=='host':raise ValueError('Host has no guest namespace')
        state=inspect_gateway(shop,v['container_id'])
        if state.get('State',{}).get('Running'):
            with pinned_namespace(state) as namespace:firewall(v,namespace,remove=True)
    except (ValueError,RuntimeError,OSError):pass
    firewall(v,None,remove=True)
    status=ROOT/'status'/(name+'.json')
    if status.is_symlink():raise ValueError('Unsafe gateway status file')
    status.unlink(missing_ok=True)
    if not keep_profile:
        (ROOT/(name+'.json')).unlink()
        if not profiles():run(['systemctl','disable','--now','mykos-vpn-gateway.timer','mykos-vpn-gateway-guard.service'],timeout=20)
    return {'state':'deactivated' if keep_profile else 'removed'}


def guard():
    """Boot ordering: protect saved guests before Podman's restart service runs."""
    if not ROOT.exists():return []
    with vm.locked():
        result=[]
        for v in profiles():
            route_state(v)
            if v.get('mode')!='host':
                run([IP,'route','replace','unreachable',v['remote'],'table',str(v['table']),'proto','242'],timeout=10)
                policy(v)
            firewall(v,None)
            result.append({'shop':v['shop'],'state':'protected_before_guest_restart'})
        return result


def publish_status(v,result):
    folder=ROOT/'status';vm.safe_dir(folder)
    from mykos_image_build import write
    write(folder/(key(v['shop'],v['connection'])+'.json'),json.dumps({'at':time.time(),**result})+'\n')


def summaries():
    result=[]
    for v in profiles():
        status={}
        p=ROOT/'status'/(key(v['shop'],v['connection'])+'.json')
        if p.is_file() and not p.is_symlink() and p.stat().st_size<65536:
            value=json.loads(p.read_text())
            if 0<=time.time()-value.get('at',0)<=65:status=value
        directory=HOST_IPSEC if v.get('mode')=='host' else vm.POS/('.'+v['shop'])
        peerfile=directory/(v['connection']+'.vpn-peer');peer=None
        if peerfile.is_file() and not peerfile.is_symlink() and peerfile.stat().st_size<64:
            try:peer=str(ipaddress.IPv4Address(peerfile.read_text().strip()))
            except ValueError:pass
        # The public read model never exposes credential/configuration files.
        result.append({**v,'peer':peer,'full_tunnel_sa':bool(status.get('full_tunnel_sa') or status.get('host_internet_via_vpn')),'gateway_status':status})
    return result


def sync():
    if not ROOT.exists():return []
    with vm.locked():
        results=[]
        for v in profiles():
            try:
                value=apply(v);publish_status(v,value);results.append(value)
            except (ValueError,RuntimeError,OSError) as error:
                from mykos_vpn_host import update as update_host
                update_host(v,False)
                # Keep a scoped unreachable route if a gateway identity no longer resolves.
                route_state(v)
                run([IP,'route','replace','unreachable',v['remote'],'table',str(v['table']),'proto','242'],timeout=10)
                value={'shop':v['shop'],'error':str(error),'full_tunnel_sa':False,'host_internet_via_vpn':False}
                publish_status(v,value);results.append(value)
        return results


def require_unreferenced(shop):
    if any(v['shop']==shop for v in profiles()):raise ValueError('Remove this guest shared VPN gateway profiles before deleting its container')


def clear_all():
    # Caller holds the lifecycle lock during an explicitly confirmed purge.
    for v in profiles():remove_profile(v['shop'],v['connection'])


def deactivate_all():
    """Uninstall cleanup: retain operator profiles but release owned networking."""
    with vm.locked():
        return [remove_profile(v['shop'],v['connection'],keep_profile=True) for v in profiles()]
