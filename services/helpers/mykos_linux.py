"""Persistent native Linux containers; preserve root layers and customer mounts."""
import json
import os
from pathlib import Path
import re
import tempfile

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


def start(shop):
    shopcode(shop)
    with vm.locked():
        name,state=inspect(shop);require_persistent(state)
        if state.get('State',{}).get('Running'):return {'shop':shop,'state':'running','output':name}
        labels=state.get('Config',{}).get('Labels') or {}
        if labels.get('io.mykos.network.interface') != 'eth0':
            raise ValueError('Legacy pipework container: recreate on mykos-lan before startup; saved data was preserved')
        selected,_=network.ensure()
        if selected not in state.get('NetworkSettings',{}).get('Networks',{}):
            raise ValueError('Container is not attached to the configured native LAN network')
        value=remember(shop,name,state)
        run([PODMAN,'start',name],timeout=120)
        state=json.loads(run([PODMAN,'container','inspect',name],timeout=30))[0]
        # Restart a VPN service only when this ID has explicitly configured tunnels.
        if any((vm.POS / ('.'+shop)).glob('*.conn.conf')):
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
