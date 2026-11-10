"""Persistent native Linux containers; preserve root layers and customer mounts."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

import mykos_vm as vm
import mykos_host_monitoring as host
import mykos_network as network
from mykos_runtime import PODMAN, config, network_device, run, shopcode, target


def inspect(shop):
    name=target(shop)
    state=json.loads(run([PODMAN,'container','inspect',name],timeout=30))[0]
    if (state.get('Config',{}).get('Labels') or {}).get('io.mykos.vm.os'):
        raise ValueError('Use the VM lifecycle for this container')
    return name,state


def directory():
    folder=vm.POS/'.linux';vm.safe_dir(folder);return folder


def save(shop,value):
    folder=directory();path=folder/(shop+'.json')
    if path.is_symlink():raise ValueError('Symlink Linux metadata rejected')
    descriptor,temp=tempfile.mkstemp(prefix='.record-',dir=str(folder))
    try:
        with os.fdopen(descriptor,'w') as handle:json.dump(value,handle,indent=2)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def saved(shop):
    path=directory()/(shop+'.json')
    if path.is_symlink():raise ValueError('Symlink Linux metadata rejected')
    return json.loads(path.read_text()) if path.exists() else {}


def require_persistent(state):
    if state.get('HostConfig',{}).get('AutoRemove'):
        raise ValueError('Legacy container uses auto-remove. Convert it to persistent storage before stopping; its current data has been left intact.')


def remember(shop,name,state):
    value=saved(shop)
    value.update(name=name,image=state.get('Image'),network_device=network_device(config()))
    if state.get('State',{}).get('Running'):
        try:
            mac=host.network(state).get('mac') or ''
            if re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',mac):value['mac']=mac.lower()
        except RuntimeError:pass
    save(shop,value)
    return value


def vpn_preflight(shop):
    """Inspect dependencies in a host Python child; never execute guest code."""
    shopcode(shop)
    name,state=inspect(shop)
    if (state.get('Config',{}).get('Labels') or {}).get('io.mykos.network.isolated') == 'true':
        raise ValueError('Copied instance networking is isolated; VPN activation is disabled')
    if not state.get('State',{}).get('Running'):raise ValueError('Start this container first')
    if (state.get('Config',{}).get('Labels') or {}).get('io.mykos.os') in ('debian13','archlinux','sles15sp7','amazon2023','alpine'):
        raise ValueError('VPN creation on this native image is not yet qualified; managed NAT networking is available')
    expected=(vm.POS/('.'+shop)).resolve()
    mounts=[m for m in state.get('Mounts',[]) if m.get('Destination')=='/etc/ipsec.d']
    if len(mounts)!=1 or Path(mounts[0].get('Source','')).resolve()!=expected or not mounts[0].get('RW'):
        raise ValueError('VPN requires the customer folder mounted read-write at /etc/ipsec.d')
    pid=int(state['State']['Pid']);root=Path('/proc')/str(pid)/'root'
    mounted=(root/'etc/ipsec.d').stat();source=expected.stat()
    if (mounted.st_dev,mounted.st_ino)!=(source.st_dev,source.st_ino):raise ValueError('Customer VPN mount identity does not match the guest')
    # chroot resolves absolute guest symlinks correctly, including alternatives.
    # Only this fixed host-interpreter code runs; it starts no guest processes.
    script="""import os,json,stat,sys
os.chroot(sys.argv[1]);os.chdir('/')
paths={'ipsec':('/usr/sbin/ipsec','/sbin/ipsec'),'iptables':('/usr/sbin/iptables','/sbin/iptables'),
       'ip6tables':('/usr/sbin/ip6tables','/sbin/ip6tables'),'flock':('/usr/bin/flock','/bin/flock'),
       'setsid':('/usr/bin/setsid','/bin/setsid'),'timeout':('/usr/bin/timeout','/bin/timeout'),'systemctl':('/usr/bin/systemctl','/bin/systemctl')}
for path in ('/etc/ipsec.conf','/etc/ipsec.secrets'):
 if os.path.lexists(path) and (os.path.islink(path) or not os.path.isfile(path)):raise SystemExit('VPN include must be a regular file: '+path)
missing=[name for name,choices in paths.items() if not any(os.path.isfile(p) and os.access(p,os.X_OK) for p in choices)]
print(json.dumps(missing))
"""
    missing=json.loads(run([sys.executable,'-c',script,str(root)],timeout=10))
    current=inspect(shop)[1]
    if current['Id']!=state['Id'] or current.get('State',{}).get('Pid')!=pid:raise ValueError('Container changed during VPN preflight')
    if missing:raise ValueError('Install VPN dependencies in this Linux image first: '+', '.join(missing))
    return {'shop':shop,'state':'vpn_ready'}


def prepare_vpn(shop):
    with vm.locked():
        return _prepare_vpn(shop)


def _prepare_vpn(shop):
    """Install boot reconnect support only for the existing customer bind mount."""
    shopcode(shop)
    name,state=inspect(shop)
    if (state.get('Config',{}).get('Labels') or {}).get('io.mykos.network.isolated') == 'true':
        raise ValueError('Copied instance networking is isolated; VPN activation is disabled')
    if not state.get('State',{}).get('Running'):raise ValueError('Start this container first')
    vpn_preflight(shop)
    expected=vm.POS/('.'+shop)
    mounts=[m for m in state.get('Mounts',[]) if m.get('Destination')=='/etc/ipsec.d']
    if len(mounts)!=1 or Path(mounts[0].get('Source',''))!=expected or not mounts[0].get('RW'):
        raise ValueError('VPN requires this customer folder mounted read-write at /etc/ipsec.d; existing container left unchanged')
    # Installation occurs only after optional Meraki upload succeeds. Files contain
    # no secrets; the customer directory remains the sole configuration source.
    restore=vm.POS/'vpn-restore'
    if restore.is_symlink() or not restore.is_file():raise ValueError('Missing VPN boot restore helper')
    unit='[Service]\nExecStartPre=/usr/local/sbin/mykos-vpn-restore\n'
    setup="""import os,json,pathlib,stat,sys,tempfile,fnmatch,re
root,helper,unit,device,inode=sys.argv[1:]
os.chroot(root);os.chdir('/')
mounted=os.stat('/etc/ipsec.d')
if (mounted.st_dev,mounted.st_ino)!=(int(device),int(inode)):raise ValueError('VPN mount changed; no files written')
for filename,line in [('ipsec.conf','include /etc/ipsec.d/*.conn.conf'),('ipsec.secrets','include /etc/ipsec.d/*.secrets')]:
 p=pathlib.Path('/etc')/filename
 if p.is_symlink() or (p.exists() and not p.is_file()):raise ValueError('Unsafe IPsec include destination')
 old=p.read_text() if p.exists() else ''
 probe='/etc/ipsec.d/mykos-probe.'+('conn.conf' if filename=='ipsec.conf' else 'secrets')
 patterns=[parts[1] for text in old.splitlines() for parts in [text.split()] if len(parts)>=2 and parts[0]=='include']
 if not any(fnmatch.fnmatch(probe,pattern) for pattern in patterns):
  if p.exists():
   backup=p.with_name(filename+'.mykos-before-include')
   if backup.is_symlink():raise ValueError('Unsafe IPsec include backup')
   if not backup.exists():
    fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as h:h.write(old)
  fd,tmp=tempfile.mkstemp(prefix='.mykos-include-',dir='/etc')
  with os.fdopen(fd,'w') as h:h.write(old+('' if not old or old.endswith('\\n') else '\\n')+line+'\\n')
  os.chmod(tmp,0o600);os.replace(tmp,p)
for rel,text,mode in [('usr/local/sbin/mykos-vpn-restore',helper,0o755),('etc/systemd/system/ipsec.service.d/mykos-reconnect.conf',unit,0o644)]:
 p=pathlib.Path('/')/rel
 if p.is_symlink():raise ValueError('Unsafe VPN boot destination')
 p.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(prefix='.mykos-vpn-',dir=str(p.parent))
 with os.fdopen(fd,'w') as h:h.write(text)
 os.chmod(tmp,mode);os.replace(tmp,p)
"""
    run([sys.executable,'-c',setup,str(Path('/proc')/str(int(state['State']['Pid']))/'root'),restore.read_text(),unit,str(expected.stat().st_dev),str(expected.stat().st_ino)],timeout=15)
    run([PODMAN,'exec',name,'systemctl','daemon-reload'],timeout=15)
    run([PODMAN,'exec',name,'systemctl','enable','ipsec'],timeout=15)
    return {'shop':shop,'state':'vpn_boot_ready'}


def wait_boot(name,runner=run,timeout=240):
    deadline=time.monotonic()+timeout
    while True:
        state=runner([PODMAN,'exec',name,'/bin/sh','-c','systemctl is-system-running 2>/dev/null || true'],timeout=10).strip()
        if state in ('running','degraded'):return state
        if time.monotonic()>=deadline:raise TimeoutError('Container boot did not finish before VPN startup')
        time.sleep(.5)


def start(shop):
    shopcode(shop)
    with vm.locked():
        name,state=inspect(shop);require_persistent(state)
        if state.get('State',{}).get('Running'):return {'shop':shop,'state':'running','output':name}
        from mykos_runtime import require_container_capacity
        require_container_capacity(name)
        labels=state.get('Config',{}).get('Labels') or {}
        if labels.get('io.mykos.network.isolated') == 'true':
            if state.get('HostConfig',{}).get('NetworkMode') != 'none':
                raise ValueError('Copied instance network isolation changed')
            run([PODMAN,'start',name],timeout=120)
            if not inspect(shop)[1].get('State',{}).get('Running'):
                raise RuntimeError('Copied instance exited during startup')
            return {'shop':shop,'state':'running','network_isolated':True}
        if labels.get('io.mykos.network.interface') != 'eth0':
            raise ValueError('Legacy pipework container: recreate on mykos-lan before startup; saved data was preserved')
        selected,_=network.ensure()
        if selected not in state.get('NetworkSettings',{}).get('Networks',{}):
            raise ValueError('Container is not attached to the configured native LAN network')
        value=remember(shop,name,state)
        if (state.get('Config',{}).get('Annotations') or {}).get('run.oci.systemd.force_cgroup_v1')=='/sys/fs/cgroup':
            from mykos_cgroups import arguments
            arguments('rh7')
        run([PODMAN,'start',name],timeout=120)
        state=json.loads(run([PODMAN,'container','inspect',name],timeout=30))[0]
        # Restart a VPN service only when this ID has explicitly configured tunnels.
        if any((vm.POS / ('.'+shop)).glob('*.conn.conf')):
            wait_boot(name,run)
            _prepare_vpn(shop)
            run([PODMAN,'exec',name,'systemctl','restart','ipsec'],timeout=45)
        state=json.loads(run([PODMAN,'container','inspect',name],timeout=30))[0]
        if not state.get('State',{}).get('Running'):raise RuntimeError('Container exited during startup')
        remember(shop,name,state)
        return {'shop':shop,'state':'running','output':name}


def stop(shop):
    shopcode(shop)
    with vm.locked():
        name,state=inspect(shop);require_persistent(state)
        remember(shop,name,state)
        run([PODMAN,'stop','--time','120',name],timeout=150)
        return {'shop':shop,'state':'stopped','output':name}



def vpn_change(shop,operation):
    if operation not in ('start','stop'):raise ValueError('Invalid VPN control action')
    with vm.locked():
        name,state=inspect(shop)
        vpn_preflight(shop)
        folder=vm.POS/('.'+shop)
        if folder.is_symlink() or not any(p.is_file() and not p.is_symlink() for p in folder.glob('*.conn.conf')):raise ValueError('No configured VPN for this instance')
        if operation=='start':_prepare_vpn(shop)
        run([PODMAN,'exec',name,'systemctl',operation,'ipsec'],timeout=60)
        if operation=='start':
            run([PODMAN,'exec',name,'ipsec','auto','--rereadsecrets'],timeout=15)
            connections=[]
            for path in folder.glob('*.conn.conf'):
                if path.is_symlink() or not path.is_file():raise ValueError('Unsafe VPN configuration')
                for connection in re.findall(r'^\s*conn\s+(\S+)',path.read_text(),re.M):
                    if connection=='%default' or connection.endswith('-mykos-default'):continue
                    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,69}',connection):raise ValueError('Invalid saved VPN connection name')
                    connections.append(connection)
            if not connections:raise ValueError('No saved VPN connections')
            for connection in sorted(set(connections)):run([PODMAN,'exec',name,'ipsec','auto','--asynchronous','--up',connection],timeout=20)
        if inspect(shop)[1]['Id']!=state['Id']:raise ValueError('Container changed during VPN action')
        return {'shop':shop,'state':'vpn_started' if operation=='start' else 'vpn_stopped','configuration_retained':True}
