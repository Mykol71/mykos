"""Owned host-output diversion shared by host and native-guest VPN gateways.

Only activate after a matching full-tunnel policy AND ESP state are confirmed.
No main-route changes, broad connection tracking flushes or firewall chain flushes.
"""
import ipaddress,json,re,shlex
MARK='0x4d590000/0xffff0000'
IP='/usr/sbin/ip'


def parse_xfrm(text,kind):
    # EL8/EL9 iproute versions do not consistently implement xfrm JSON output.
    # State is ALWAYS requested with nokeys before reaching this parser.
    if len(text)>512*1024:raise ValueError('Oversized IPsec kernel metadata')
    if text.lstrip().startswith('['):return json.loads(text)
    result=[]
    for block in re.split(r'(?=^src )',text,flags=re.M):
        header=re.match(r'src (\S+) dst (\S+)',block)
        if not header:continue
        row={'src':header[1],'dst':header[2]}
        for field in ('dir','proto','mode','action','level'):
            m=re.search(r'\b'+field+r' (\S+)',block)
            if m:row[field]=m[1]
        m=re.search(r'\bpriority (\d+)',block)
        if m:row['priority']=int(m[1])
        m=re.search(r'\bmark (0x[0-9a-fA-F]+)/(0x[0-9a-fA-F]+)',block)
        if m:row['mark']={'value':int(m[1],16),'mask':int(m[2],16)}
        m=re.search(r'\breqid (\d+)',block)
        if m:row['reqid']=int(m[1])
        if kind=='policy':
            row['tmpl']=[]
            for part in re.split(r'\btmpl ',block)[1:]:
                m=re.match(r'src (\S+) dst (\S+)',part)
                if not m:continue
                template={'src':m[1],'dst':m[2]}
                for field in ('proto','mode','level'):
                    found=re.search(r'\b'+field+r' (\S+)',part)
                    if found:template[field]=found[1]
                found=re.search(r'\breqid (\d+)',part)
                if found:template['reqid']=int(found[1])
                row['tmpl'].append(template)
        result.append(row)
    return result


def full_tunnel_active(v, namespace=None):
    import mykos_vpn_gateway as gateway
    prefix=gateway.namespace_prefix(namespace) if namespace else []
    directory=gateway.vm.POS/('.'+v['shop']) if v.get('mode','guest')!='host' else gateway.HOST_IPSEC
    peer=directory/(v['connection']+'.vpn-peer')
    if peer.is_symlink() or not peer.is_file() or peer.stat().st_size>64:return False
    remote=str(ipaddress.IPv4Address(peer.read_text().strip()))
    policies=parse_xfrm(gateway.run([*prefix,IP,'xfrm','policy','list'],timeout=5),'policy')
    states=parse_xfrm(gateway.run([*prefix,IP,'xfrm','state','list','nokeys'],timeout=5),'state')
    for policy in policies:
        if policy.get('dir')!='out' or policy.get('action','allow')!='allow':continue
        if policy.get('src')!=v['local'] or policy.get('dst') not in ('0.0.0.0/0','0/0'):continue
        for template in policy.get('tmpl',[]):
            if template.get('proto')!='esp' or template.get('dst')!=remote or template.get('mode')!='tunnel' or template.get('level','required') not in ('required','require'):continue
            if any(s.get('proto')=='esp' and s.get('mode')=='tunnel' and s.get('dst')==remote and s.get('reqid')==template.get('reqid') for s in states):return True
    return False


def bridge_address(v):
    import mykos_vpn_gateway as gateway
    rows=json.loads(gateway.run([IP,'-j','-4','addr','show','dev',v['bridge']],timeout=5))
    addresses=[a['local'] for row in rows for a in row.get('addr_info',[]) if a.get('family')=='inet' and ipaddress.ip_address(a['local']) in ipaddress.ip_network(v['local'])]
    if len(addresses)!=1:raise ValueError('Cannot uniquely identify the host bridge address')
    return addresses[0]


def bypass_networks():
    import mykos_vpn_gateway as gateway
    rows=json.loads(gateway.run([IP,'-j','-4','route','show','table','main'],timeout=5))
    values={'127.0.0.0/8','169.254.0.0/16','224.0.0.0/4','255.255.255.255/32'}
    for row in rows:
        if row.get('scope')=='link' and row.get('dst') not in (None,'default'):
            values.add(str(ipaddress.IPv4Network(row['dst'],strict=False)))
    return sorted(values)


def rule(v, remove=False):
    import mykos_vpn_gateway as gateway
    priority=v['table']-10002
    rows=json.loads(gateway.run([IP,'-N','-j','rule','show'],timeout=5))
    owned=[r for r in rows if r.get('priority')==priority]
    for row in owned:
        mark=int(str(row.get('fwmark','0')),0);mask=int(str(row.get('fwmask','0xffffffff')),0)
        if str(row.get('table'))!=str(v['table']) or mark!=0x4d590000 or mask!=0xffff0000 or row.get('src','all') not in ('all','0.0.0.0/0'):
            raise ValueError('Host VPN route priority is in use; existing rule retained')
    if len(owned)>1:raise ValueError('Ambiguous host VPN policy')
    if bool(owned)!=bool(remove):return
    gateway.run([IP,'rule','del' if remove else 'add','priority',str(priority),'fwmark',MARK,'lookup',str(v['table'])],timeout=5)


def desired_rules(v, source, networks):
    import mykos_vpn_gateway as gateway
    chain='MKH_'+gateway.hashlib.sha256(gateway.key(v['shop'],v['connection']).encode()).hexdigest()[:10]
    # Never divert replies to incoming management sessions or the VPN's own transport.
    output=[['-m','conntrack','--ctdir','REPLY','-j','RETURN'],
            ['-p','udp','-m','multiport','--dports','500,4500','-j','RETURN'],
            ['-p','esp','-j','RETURN']]
    output += [['!','-d','127.0.0.0/8','-p',protocol,'-m',protocol,'--dport','53','-j','MARK','--set-xmark',MARK] for protocol in ('udp','tcp')]
    output += [['-d',subnet,'-j','RETURN'] for subnet in networks]
    output += [['-j','MARK','--set-xmark',MARK]]
    tag='mykos-host-vpn-'+gateway.key(v['shop'],v['connection'])
    hook=['-m','comment','--comment',tag,'-j',chain]
    nat=['-m','mark','--mark',MARK,'!','-s',v['local'],'-m','comment','--comment',tag,'-j','SNAT','--to-source',source]
    return chain,output,hook,nat


def firewall(v, source, networks, remove=False):
    import mykos_vpn_gateway as gateway
    chain,items,hook,nat=desired_rules(v,source,networks)
    base=['/usr/sbin/iptables','-w','5']
    exists=gateway.checked_command([*base,'-t','mangle','-nL',chain])
    if exists:
        # Refuse to repurpose a preexisting or altered chain.
        lines=gateway.run([*base,'-t','mangle','-S',chain],timeout=5).splitlines()
        actual=[shlex.split(line)[2:] for line in lines if line.startswith('-A '+chain+' ')]
        if actual!=items and not (remove and actual==items[:len(actual)]):raise ValueError('Host VPN chain differs from its owned rules; left unchanged')
        if remove:items=actual
    elif not remove:
        gateway.run([*base,'-t','mangle','-N',chain],timeout=5)
        for args in items:gateway.run([*base,'-t','mangle','-A',chain,*args],timeout=5)
    dns=[['!','-d','127.0.0.0/8','-p',protocol,'-m',protocol,'--dport','53','-m','mark','--mark',MARK,
          '-m','comment','--comment','mykos-host-vpn-dns-'+gateway.key(v['shop'],v['connection']),
          '-j','DNAT','--to-destination','8.8.8.8'] for protocol in ('udp','tcp')]
    operations=[('nat','POSTROUTING',nat),*[('nat','OUTPUT',item) for item in dns],('mangle','OUTPUT',hook)]
    for table,target,args in (reversed(operations) if remove else operations):
        present=gateway.checked_command([*base,'-t',table,'-C',target,*args])
        if remove and present:gateway.run([*base,'-t',table,'-D',target,*args],timeout=5)
        elif not remove and not present:gateway.run([*base,'-t',table,'-I',target,'1',*args],timeout=5)
    if remove and exists:
        for args in reversed(items):gateway.run([*base,'-t','mangle','-D',chain,*args],timeout=5)
        gateway.run([*base,'-t','mangle','-X',chain],timeout=5)


def reverse_path(v, enable=True):
    """Loose RPF only on the managed bridge; restore its exact previous setting."""
    import mykos_vpn_gateway as gateway
    folder=gateway.ROOT/'host-output';path=folder/(gateway.key(v['shop'],v['connection'])+'.json')
    field='net.ipv4.conf.'+v['bridge']+'.rp_filter'
    current=int(gateway.run(['/usr/sbin/sysctl','-n',field],timeout=5))
    if current not in (0,1,2):raise ValueError('Unexpected reverse-path filter state')
    if path.is_symlink():raise ValueError('Unsafe host VPN reverse-path state')
    if enable:
        gateway.vm.safe_dir(folder)
        if not path.exists():
            from mykos_image_build import write
            write(path,json.dumps({'bridge':v['bridge'],'previous':current})+'\n')
        else:
            saved=json.loads(path.read_text())
            if saved.get('bridge')!=v['bridge']:raise ValueError('Host VPN bridge identity changed')
        if current!=2:gateway.run(['/usr/sbin/sysctl','-q','-w',field+'=2'],timeout=5)
    elif path.exists():
        saved=json.loads(path.read_text())
        if saved.get('bridge')!=v['bridge'] or saved.get('previous') not in (0,1,2):raise ValueError('Invalid host reverse-path recovery state')
        if current==2 and saved['previous']!=2:gateway.run(['/usr/sbin/sysctl','-q','-w',field+'='+str(saved['previous'])],timeout=5)
        path.unlink()


def ipv6_guard(v, networks=None, remove=False):
    import mykos_vpn_gateway as gateway
    chain='MKH6_'+gateway.hashlib.sha256(gateway.key(v['shop'],v['connection']).encode()).hexdigest()[:9]
    base=['/usr/sbin/ip6tables','-w','5']
    items=[['-o','lo','-j','RETURN'],['-m','conntrack','--ctdir','REPLY','-j','RETURN'],
           *[['-p',protocol,'-m',protocol,'--dport','53','-j','REJECT'] for protocol in ('udp','tcp')],
           *[['-d',subnet,'-j','RETURN'] for subnet in (networks or ['::1/128','fe80::/10','ff00::/8'])],['-j','REJECT']]
    hook=['-m','comment','--comment','mykos-host-vpn-'+gateway.key(v['shop'],v['connection']),'-j',chain]
    exists=gateway.checked_command([*base,'-nL',chain])
    if exists:
        actual=[shlex.split(line)[2:] for line in gateway.run([*base,'-S',chain],timeout=5).splitlines() if line.startswith('-A '+chain+' ')]
        # REJECT's default reject-with may be made explicit by the backend.
        actual=[a[:-2] if a[-2:]==['--reject-with','icmp6-port-unreachable'] else a for a in actual]
        if actual!=items and not (remove and actual==items[:len(actual)]):raise ValueError('Host IPv6 VPN chain differs; left unchanged')
        if remove:items=actual
    elif not remove:
        gateway.run([*base,'-N',chain],timeout=5)
        for args in items:gateway.run([*base,'-A',chain,*args],timeout=5)
    present=gateway.checked_command([*base,'-C','OUTPUT',*hook])
    if remove and present:gateway.run([*base,'-D','OUTPUT',*hook],timeout=5)
    elif not remove and not present:gateway.run([*base,'-I','OUTPUT','1',*hook],timeout=5)
    if remove and exists:
        for args in reversed(items):gateway.run([*base,'-D',chain,*args],timeout=5)
        gateway.run([*base,'-X',chain],timeout=5)


def update(v, active):
    """Activate after full-SA verification; otherwise restore ordinary host WAN."""
    import mykos_vpn_gateway as gateway
    path=gateway.ROOT/'host-output'/(gateway.key(v['shop'],v['connection'])+'.json')
    if not active and not path.exists():return
    if path.is_symlink():raise ValueError('Unsafe host output recovery state')
    if active:
        source=bridge_address(v);networks=bypass_networks()
        reverse_path(v)
        saved=json.loads(path.read_text())
        if 'source' in saved and (saved['source']!=source or saved['networks']!=networks):
            raise ValueError('Host network changed; remove the shared VPN profile before changing its routing')
        rows=json.loads(gateway.run([IP,'-j','-6','route','show','table','main'],timeout=5))
        networks6=sorted({'::1/128','fe80::/10','ff00::/8'} | {str(ipaddress.IPv6Network(row['dst'],strict=False)) for row in rows if row.get('protocol')=='kernel' and not row.get('gateway') and row.get('dst') not in (None,'default')})
        saved.update(source=source,networks=networks,networks6=networks6)
        from mykos_image_build import write
        write(path,json.dumps(saved)+'\n')
        try:
            rule(v);ipv6_guard(v,networks6);firewall(v,source,networks)
        except Exception:
            # Recover all known owned changes if activation fails partway through.
            # Keep the saved state if cleanup itself needs a later retry.
            update(v,False)
            raise
    else:
        saved=json.loads(path.read_text())
        if 'source' in saved:firewall(v,saved['source'],saved['networks'],remove=True)
        ipv6_guard(v,saved.get('networks6'),remove=True);rule(v,remove=True)
        reverse_path(v,enable=False)
