"""Topology from saved host-side metadata only; never read PSKs or probe guests."""
import ipaddress,re
from pathlib import Path
import mykos_vm as vm
import mykos_vpn_gateway as gateway


def topology(shop,shared_profiles=None):
    if not re.fullmatch(r'[0-9]{8}',shop):raise ValueError('Invalid ID')
    directory=vm.POS/('.'+shop)
    direct=[]
    if directory.is_dir() and not directory.is_symlink():
        for path in sorted(directory.glob('*.conn.conf')):
            if path.is_symlink() or path.stat().st_size>65536:continue
            text=path.read_text()
            sections=re.split(r'^conn\s+',text,flags=re.M)
            if len(sections)>1:text=sections[1]
            values=dict(re.findall(r'^\s*(right|rightsubnet|leftsubnet)\s*=\s*([^\s#]+)',text,re.M))
            # Older Ubuntu markers contain only this sanitized summary.
            marker=re.search(r'# peer (\S+); local (\S+); remote (\S+)',text)
            if marker:values.update(right=marker[1],leftsubnet=marker[2],rightsubnet=marker[3])
            if not all(k in values for k in ('right','rightsubnet','leftsubnet')):continue
            direct.append({'name':path.name[:-10],'peer':values['right'].strip('"'),'remote':values['rightsubnet'].strip('"'),'local':values['leftsubnet'].strip('"')})
    shared_profiles=gateway.summaries() if shared_profiles is None else shared_profiles
    shared=[{'name':p['connection'],'gateway':p['shop'],'mode':p.get('mode','guest'),'remote':p['remote'],'site_remote':p.get('site_remote'),'peer':p.get('peer'),'full_tunnel_sa':bool(p.get('full_tunnel_sa'))} for p in shared_profiles if p['shop']!=shop]
    owned=[p['connection'] for p in shared_profiles if p['shop']==shop]
    for link in direct:link['mode']='gateway' if link['name'] in owned else 'direct'
    primary=None
    path=directory/'.mykos-first-tunnel'
    if path.is_file() and not path.is_symlink() and path.stat().st_size<80:
        value=path.read_text().strip();candidate=value.removeprefix('mykos-')
        if any(link['name'] in (value,candidate) for link in direct):primary=value
    return {'guest':shop,'links':direct,'via_gateways':shared,'primary':primary}
