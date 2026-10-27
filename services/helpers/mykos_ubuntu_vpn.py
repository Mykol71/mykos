"""Ubuntu guest IPsec over the private QEMU agent; never SSH or guest-IP RPC."""
import base64
import getpass
import fcntl
import importlib.machinery
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import time
import mykos_vm as vm
from mykos_commands import GuestAgent


def selection(shop):
    if not re.fullmatch(r'[0-9]{8}', shop): raise ValueError('Select an eight-digit ID')
    value = vm.read(shop)
    if value.get('role') != 'instance' or not value.get('os', '').startswith('ubuntu'):
        raise ValueError('Select an Ubuntu instance')
    if not value.get('guest_agent'):
        raise ValueError('Ubuntu guest-agent channel needs enabling first: ./vm guest-agent-enable ' + shop + ' --confirm ' + shop)
    return value


def execute(value, path, args=(), payload=None, timeout=30):
    key=value['os'] if value.get('role')=='template' else value['name'][:8]
    lock=vm.storage(key)/'.vpn-agent.lock'
    with os.fdopen(os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600),'w') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Ubuntu VPN agent is busy; retry shortly')
        return _execute(value,path,args,payload,timeout)


def _execute(value, path, args=(), payload=None, timeout=30):
    agent = GuestAgent(value, allow_image_builder=True, timeout=min(3, timeout)) if value.get('role')=='template' else GuestAgent(value, timeout=min(3, timeout))
    try:
        request = {'path':path, 'arg':list(args), 'capture-output':True}
        if payload is not None: request['input-data'] = base64.b64encode(payload).decode()
        pid = agent.call('guest-exec', request)['pid']
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = agent.call('guest-exec-status', {'pid':pid})
            if data.get('exited'):
                output = b'\n'.join(base64.b64decode(data.get(k, '')) for k in ('out-data','err-data'))
                if len(output) > 65536 or data.get('out-truncated') or data.get('err-truncated'):
                    raise ValueError('Ubuntu VPN response was too large')
                if data.get('exitcode') != 0: raise ValueError('Ubuntu VPN command failed: ' + output.decode(errors='replace')[:1000])
                return output.decode('utf-8',errors='replace').strip()
            time.sleep(.1)
        # Commands have a guest-side timeout too; do not leave a stuck ipsec process.
        raise ValueError('Ubuntu VPN command timed out')
    finally: agent.close()


def status(info):
    from mykos_host_monitoring import unknown_vpn, parse_vpn, identity
    result = unknown_vpn()
    if not info.get('State', {}).get('Running'): return result
    shop = info.get('Name', '').lstrip('/')[:8]
    try:
        value = vm.read(shop)
        if value.get('role') != 'instance' or not value.get('os','').startswith('ubuntu'): return result
        if not value.get('guest_agent'):
            directory = vm.POS/('.'+shop)
            if directory.is_symlink(): return result
            if not list(directory.glob('*.conn.conf')): result.update(status='not_configured',loaded=0,established=0)
            return result
        cache = vm.storage(shop)/'vpn-status.json'
        if cache.is_symlink(): return result
        token = identity(info)
        if cache.exists():
            data = json.loads(cache.read_text())
            if data.get('identity') == token and 0 <= time.time()-data.get('at',0) < 30: return data['vpn']
        # One bounded guest command per 30s, shared by all API/UI readers.
        script = """import glob,subprocess
files=glob.glob('/etc/ipsec.d/mykos-*.conn.conf')
if not files:
 print('000 Total IPsec connections: loaded 0, active 0\\n000 IPsec SAs: total(0)')
else:
 p=subprocess.run(['/usr/sbin/ipsec','status'],capture_output=True,text=True,timeout=2)
 print(p.stdout if p.returncode==0 else 'MYKOS_IPSEC_DOWN')
"""
        try:
            text = execute(value,'/usr/bin/python3',('-c',script),timeout=4)
            result = parse_vpn(text)
            if 'MYKOS_IPSEC_DOWN' in text: result.update(status='down',established=0)
        except (ValueError,OSError): pass
        fd = os.open(cache,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as out: json.dump({'identity':token,'at':time.time(),'vpn':result},out)
    except (ValueError,OSError,KeyError): pass
    return result


def config_module():
    path = Path(__file__).resolve().parents[2]/'pos/host-vpn'
    if not path.is_file(): path = vm.POS/'host-vpn'
    loader = importlib.machinery.SourceFileLoader('mykos_vpn_config',str(path))
    spec = importlib.util.spec_from_loader(loader.name,loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    return module,path


def add(shop, name, peer, remote, secret, local=None):
    value = selection(shop)
    common,path = config_module()
    remote = str(ipaddress.IPv4Network(remote, strict=True))
    if local is None:
        address = execute(value,'/usr/bin/python3',('-c',"import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',53));print(s.getsockname()[0]);s.close()"),timeout=5)
        local = str(ipaddress.IPv4Network(address+'/32', strict=True))
    local,remote = common.validate(name,peer,local,remote,secret)
    directory = vm.POS/('.'+shop)
    vm.safe_dir(directory)
    marker = directory/(name+'.conn.conf')
    if marker.exists() or marker.is_symlink(): raise ValueError('Connection already exists: '+name)
    # Reuse the host workflow's validated config writer inside Ubuntu. Secrets
    # travel only as QGA stdin, never argv, public status, or the host marker.
    source = path.read_text()
    prefix = source[:source.index('\ndef main():')]
    script = prefix + """
name,peer,local,remote,secret=json.loads(sys.stdin.read())
connection=save_connection(Path('/etc'),name,peer,local,remote,secret)
import subprocess
for args in (['systemctl','start','ipsec'],['/usr/sbin/ipsec','auto','--rereadsecrets'],['/usr/sbin/ipsec','auto','--add',connection],['/usr/sbin/ipsec','auto','--asynchronous','--up',connection]):
 p=subprocess.run(args,capture_output=True,text=True,timeout=15)
 if p.returncode: raise RuntimeError('VPN configured but activation failed; inspect ipsec service')
print(connection)
"""
    # The shared writer imports stdlib but not json.
    execute(value,'/usr/bin/python3',('-c','import json\n'+script),json.dumps([name,peer,local,remote,secret]).encode(),timeout=75)
    fd = os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as out: out.write('conn mykos-'+name+'\n  # peer '+peer+'; local '+local+'; remote '+remote+'\n')
    cache = vm.storage(shop)/'vpn-status.json'
    if not cache.is_symlink(): cache.unlink(missing_ok=True)
    return {'shop':shop,'connection':'mykos-'+name,'local_subnet':local,'remote_subnet':remote,'state':'configured'}


def interactive(shop):
    selection(shop)
    name=input('Enter REMOTE_NAME: ').strip()
    peer=input('Enter REMOTE_PUBLIC: ').strip()
    remote=input('Enter REMOTE_NET (CIDR, or network without /24): ').strip()
    if '/' not in remote: remote += '/24'
    secret=getpass.getpass('Enter VPN shared secret (PSK): ')
    if input('Create this Ubuntu VPN connection? [y/N] ').strip().lower()!='y': return
    result=add(shop,name,peer,remote,secret)
    print('VPN configured. Set the peer private subnet to '+result['local_subnet']+'. Status will refresh automatically.',flush=True)
