"""One-command unattended desktop image builds; no auxiliary containers/packages."""
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import struct
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
import zlib

import mykos_vm as vm

STATE = Path('/var/lib/mykos/image-builds')
BUILD_CA = Path('/etc/mykos/image-build-ca.pem')
_source = Path(__file__).resolve().parents[2]/'pos/install'
RECIPES = _source if _source.is_dir() else Path('/usr/local/share/mykos/pos/install')
MODERN_WINDOWS = ('win10','win11','win8e','wns2016','wns2019','wns2022','wns2025')


def selection(os_name):
    if os_name not in vm.VERSIONS: raise ValueError('Unsupported image OS')
    if vm.VERSIONS[os_name][0] == 'windows' and os_name not in MODERN_WINDOWS:
        raise ValueError('This Windows release uses legacy setup, not autounattend.xml; use the legacy create command')
    return os_name


def folder(os_name):
    selection(os_name)
    vm.safe_dir(STATE)
    path = STATE/os_name
    vm.safe_dir(path)
    return path


def write(path, data, mode=0o600):
    if path.is_symlink(): raise ValueError('Symlink build file rejected')
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp=Path(f.name)
        try:
            f.write(data.encode() if isinstance(data,str) else data);f.flush();os.fchmod(f.fileno(),mode)
            os.replace(tmp,path)
        finally:
            if tmp.exists():tmp.unlink()


def discard_failed(os_name, confirmation):
    selection(os_name)
    if confirmation!=os_name: raise ValueError('Confirm the exact image OS')
    with vm.locked():
        if status(os_name).get('state')!='failed': raise ValueError('Only a failed image build can be removed')
        service='mykos-image-build@'+os_name+'.service'
        check=subprocess.run(['systemctl','is-active',service],capture_output=True,text=True,timeout=10)
        if check.stdout.strip() in ('active','activating','deactivating','reloading'):
            raise ValueError('Build worker is still active; wait for it to exit')
        value=vm.read(os_name)
        if value.get('role')!='template' or value.get('ready') or value.get('name')!=os_name:
            raise ValueError('Only an unfinished MYKOS image builder can be removed')
        state=vm.inspect(os_name)
        labels=state.get('Config',{}).get('Labels') or {}
        if labels.get('io.mykos.vm.role')!='template' or labels.get('io.mykos.vm.os')!=os_name:
            raise ValueError('Builder identity changed')
        from mykos_containers import all_containers, unshared
        private=vm.storage(os_name)
        vm.safe_dir(private)
        unshared([private],all_containers(),state['Id'])
        vm.stop_container(os_name)
        vm.run([vm.PODMAN,'rm',state['Id']],timeout=60)
        unshared([private],all_containers(),state['Id'])
        shutil.rmtree(private)
        vm.record_path(os_name).unlink()
        shutil.rmtree(folder(os_name))
        return {'os':os_name,'state':'deleted','images_retained':True}


def status(os_name):
    path=folder(os_name)/'status.json'
    return json.loads(path.read_text()) if path.exists() else {'os':os_name,'state':'not_started'}


def report(os_name, state, message):
    value={'os':os_name,'state':state,'message':message,'updated_at':int(time.time())}
    write(folder(os_name)/'status.json',json.dumps(value))
    print(message,flush=True)
    return value


def credentials(os_name, username=None, password=None):
    path=folder(os_name)/'credentials.json'
    if not username and not password and path.exists():return json.loads(path.read_text())
    username=username or 'mykos'
    password=password or ('Mykos9!'+secrets.token_urlsafe(24))
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,19}',username) or username in ('root','administrator','guest'):
        raise ValueError('Build username must be a local account name, 1–20 lowercase ASCII letters/digits/underscore/hyphen')
    if len(password)<12 or len(password)>100 or any(ord(c)<32 for c in password):
        raise ValueError('Image password must be 12–100 characters with no control characters')
    if vm.VERSIONS[os_name][0]=='windows' and sum(bool(re.search(p,password)) for p in ('[a-z]','[A-Z]','[0-9]','[^A-Za-z0-9]'))<3:
        raise ValueError('Windows image password must satisfy three password complexity categories')
    result={'username':username,'password':password}
    write(path,json.dumps(result));return result


def origin(env_path=None):
    env=Path(env_path) if env_path is not None else Path('/etc/mykos/web.env')
    values={}
    for line in env.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1);values[key.strip()]=value.strip().strip('"\'').rstrip('/')
    # Browser access may use a LAN IP while the installed certificate names DNS.
    # Keep TLS verification enabled and use an explicit certificate-matching URL.
    value=values.get('MYKOS_IMAGE_BUILD_ORIGIN',values.get('MYKOS_PUBLIC_ORIGIN',''))
    if re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?',value):return value
    raise ValueError('Configure an HTTPS MYKOS_IMAGE_BUILD_ORIGIN or MYKOS_PUBLIC_ORIGIN before unattended image builds')


def callback(os_name, token):
    import fcntl
    path=folder(os_name)/'callback.json'
    with (path.parent/'callback.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if not path.exists():raise ValueError('No pending image installation')
        record=json.loads(path.read_text())
        if record.get('count',0)>=2 or time.time()>record['expires'] or not hmac.compare_digest(record['digest'],hashlib.sha256(token.encode()).hexdigest()):
            raise ValueError('Invalid image completion token')
        # HTTP retries during the first boot cannot masquerade as a clean boot.
        record['count']=record.get('expected_boot',1)
        write(path,json.dumps(record));write(folder(os_name)/'complete',str(time.time()))
        return {'completed':True,'os':os_name,'remove':record['count']>=2}


def renew_completion_window(os_name):
    """Re-arm only an explicitly resumed, unfinished build's scoped callback."""
    import fcntl
    path=folder(os_name)/'callback.json'
    with (path.parent/'callback.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        value=json.loads(path.read_text())
        if value.get('count',0)>=2:
            if (path.parent/'complete').exists():return
            raise ValueError('Image completion token is already consumed')
        value['expires']=time.time()+14400
        write(path,json.dumps(value))


def arm_second_boot(os_name):
    import fcntl
    path=folder(os_name)/'callback.json'
    with (path.parent/'callback.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        value=json.loads(path.read_text());value['expected_boot']=2
        write(path,json.dumps(value))
        complete=path.parent/'complete'
        if complete.exists():complete.unlink()


def callback_script(os_name, token):
    # Curl reads the private token from a file, never its visible argv.
    config='url = "'+origin()+'/mykos/api/image-build/'+os_name+'/complete"\nrequest = "POST"\nheader = "Authorization: Bearer '+token+'"\n'
    import ipaddress
    from urllib.parse import urlsplit
    try:
        address=str(ipaddress.IPv4Address(vm.settings()[0]));url=urlsplit(origin())
        config+='resolve = "'+url.hostname+':'+str(url.port or 443)+':'+address+'"\n'
    except (OSError,ValueError):pass
    certificate=''
    if BUILD_CA.exists():
        import ssl,stat
        info=BUILD_CA.lstat()
        if BUILD_CA.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o022:
            raise ValueError('Image build CA must be an operator-owned regular file without group/world write access')
        certificate=BUILD_CA.read_text();ssl.PEM_cert_to_DER_cert(certificate)
    script="umask 077\nconfig=$(mktemp)\nca=''\ntrap 'rm -f \"$config\" \"$ca\"' EXIT\ncat > \"$config\" <<'MYKOS_CURL'\n"+config+"MYKOS_CURL\n"
    if certificate:
        script+="ca=$(mktemp)\ncat > \"$ca\" <<'MYKOS_CA'\n"+certificate.rstrip()+"\nMYKOS_CA\nprintf 'cacert = \"%s\"\\n' \"$ca\" >> \"$config\"\n"
    return script+"/usr/bin/curl --fail --silent --show-error --connect-timeout 10 --max-time 30 --retry 30 --retry-delay 10 --config \"$config\"\n"



def cpio(files):
    result=io.BytesIO()
    for index,(name,data,mode) in enumerate([*files,('TRAILER!!!',b'',0)],1):
        raw=name.encode()+b'\0'
        header='070707'+''.join(format(v,'06o') for v in (0,index,mode,0,80,1,0))+format(0,'011o')+format(len(raw),'06o')+format(len(data),'011o')
        result.write(header.encode());result.write(raw);result.write(data)
    import gzip
    return gzip.compress(result.getvalue(),mtime=0)


def package(identifier, files, destination):
    """Payload-free macOS product package, using standard XAR/ODC formats."""
    assets=RECIPES/'macos'
    package_info=(assets/'PackageInfo').read_text().replace('@IDENTIFIER@',identifier).encode()
    distribution=(assets/'Distribution').read_text().replace('@IDENTIFIER@',identifier).replace('@COMPONENT@','component.pkg').encode()
    content=[('Distribution',distribution),('component.pkg/PackageInfo',package_info),('component.pkg/Scripts',cpio(files))]
    xar=ET.Element('xar');toc=ET.SubElement(xar,'toc')
    checksum=ET.SubElement(toc,'checksum',style='sha1');ET.SubElement(checksum,'offset').text='0';ET.SubElement(checksum,'size').text='20'
    directory=ET.SubElement(toc,'file',id='1');ET.SubElement(directory,'name').text='component.pkg';ET.SubElement(directory,'type').text='directory';ET.SubElement(directory,'mode').text='0755'
    heap=bytearray();offset=20
    for i,(name,data) in enumerate(content,2):
        parent=directory if '/' in name else toc
        f=ET.SubElement(parent,'file',id=str(i));ET.SubElement(f,'name').text=name.split('/')[-1];ET.SubElement(f,'type').text='file';ET.SubElement(f,'mode').text='0644'
        d=ET.SubElement(f,'data')
        for key,value in [('offset',offset),('length',len(data)),('size',len(data))]:ET.SubElement(d,key).text=str(value)
        ET.SubElement(d,'encoding',style='application/octet-stream')
        for key in ('archived-checksum','extracted-checksum'):ET.SubElement(d,key,style='sha1').text=hashlib.sha1(data).hexdigest()
        heap.extend(data);offset+=len(data)
    xml=ET.tostring(xar,encoding='utf-8',xml_declaration=True);table=zlib.compress(xml)
    write(destination,struct.pack('>4sHHQQI',b'xar!',28,1,len(table),len(xml),1)+table+hashlib.sha1(table).digest()+heap)


def macos_assets(os_name, values, token, target):
    import runpy,sys
    assets=RECIPES/'macos'
    with tempfile.TemporaryDirectory(prefix='mykos-mac-account-') as tmp:
        argv=sys.argv
        try:
            sys.argv=[str(assets/'admin-create.py'),tmp,values['username'],values['password'],'N']
            runpy.run_path(str(assets/'admin-create.py'),run_name='__main__')
        finally:sys.argv=argv
        files=[('./'+p.name,p.read_bytes(),0o100600 if p.name in ('user.plist','account-password') else 0o100755) for p in Path(tmp).iterdir()]
        files.append(('./postinstall',(assets/'admin-postinstall').read_bytes(),0o100755))
        files.append(('./account-create.exp',(assets/'account-create.exp').read_bytes(),0o100700))
        package('com.mykos.install.admin',files,target/'admin.pkg')
    package('com.mykos.install.skipsetup',[
        ('./postinstall',(assets/'setup-postinstall').read_bytes(),0o100755),
        ('./com.apple.SetupAssistant.plist',(assets/'setup.plist').read_bytes(),0o100644)],target/'skipsetup.pkg')
    first=(assets/'firstboot.sh').read_text()
    # Report only after the installed OS has created the requested account.
    marker='log "prebuilt account package installed successfully"'
    assert marker in first
    first=first.replace(marker,marker+'\n'+macos_ready_service(os_name,token)+'\n')
    write(target/'firstboot.sh',first,0o700)
    write(target/'com.dockur.macos.firstboot.plist',(assets/'firstboot.plist').read_bytes())
    package('com.mykos.install.bootstrap',[
        ('./postinstall',(assets/'bootstrap-postinstall').read_bytes(),0o100755),
        ('./setup-postinstall',(assets/'setup-postinstall').read_bytes(),0o100755),
        ('./com.apple.SetupAssistant.plist',(assets/'setup.plist').read_bytes(),0o100644),
        ('./admin.pkg',(target/'admin.pkg').read_bytes(),0o100600),
        ('./firstboot.sh',(target/'firstboot.sh').read_bytes(),0o100700),
        ('./firstboot.plist',(assets/'firstboot.plist').read_bytes(),0o100644)],target/'bootstrap.pkg')
    write(target/'launch.sh',(assets/'recovery.sh').read_bytes(),0o700)


def windows_answer(source, values):
    """Retain the vendor's storage/driver/setup commands; replace account data."""
    ns='urn:schemas-microsoft-com:unattend';q=lambda s:'{'+ns+'}'+s
    ET.register_namespace('',ns);ET.register_namespace('wcm','http://schemas.microsoft.com/WMIConfig/2002/State')
    tree=ET.fromstring(source)
    for node in tree.iter():
        tag=node.tag.split('}')[-1]
        if tag=='LocalAccount':
            name=node.find(q('Name'))
            if name is not None:name.text=values['username']
        if tag in ('Password','AdministratorPassword'):
            val=node.find(q('Value'));plain=node.find(q('PlainText'))
            if val is not None and plain is not None:
                suffix='AdministratorPassword' if tag=='AdministratorPassword' else 'Password'
                val.text=base64.b64encode((values['password']+suffix).encode('utf-16le')).decode();plain.text='false'
        if tag=='AutoLogon':
            node.find(q('Username')).text=values['username'];node.find(q('Enabled')).text='true';node.find(q('LogonCount')).text='3'
    return ET.tostring(tree,encoding='utf-8',xml_declaration=True)


def ubuntu_userdata(values, setup):
    try:
        import crypt
        hashed=crypt.crypt(values['password'],crypt.mksalt(crypt.METHOD_SHA512))
        if not hashed.startswith('$6$'):raise ValueError('System crypt lacks SHA512')
    except (ImportError,AttributeError,ValueError):
        hashed=subprocess.run(['openssl','passwd','-6','-stdin'],input=values['password']+'\n',text=True,capture_output=True,check=True).stdout.strip()
    if not hashed.startswith('$6$'):raise ValueError('SHA512 password hashing is unavailable')
    script=base64.b64encode(setup).decode()
    late=["curtin in-target -- mkdir -p /usr/local/sbin /var/lib/mykos",
          "printf %s "+shlex.quote(script)+" | base64 -d > /target/usr/local/sbin/mykos-ubuntu-setup.py",
          "chmod 700 /target/usr/local/sbin/mykos-ubuntu-setup.py",
          "touch /run/mykos-install/disk-installed"]
    firstboot="set -eu; trap 'touch /var/lib/mykos/image-install-failed' EXIT; python3 /usr/local/sbin/mykos-ubuntu-setup.py; export DEBIAN_FRONTEND=noninteractive; apt-get install -y ubuntu-desktop-minimal; systemctl set-default graphical.target; systemctl start --no-block graphical.target; touch /var/lib/mykos/image-install-complete; trap - EXIT"
    data={'autoinstall':{'version':1,'refresh-installer':{'update':False},'locale':'en_US.UTF-8',
        'keyboard':{'layout':'us'},'identity':{'hostname':'mykos-base','username':values['username'],'password':hashed},
        'storage':{'layout':{'name':'direct'},'swap':{'size':0}},
        'packages':['qemu-guest-agent','libreswan','iproute2','net-tools','curl','python3'],
        'early-commands':['modprobe 9pnet_virtio','mkdir -p /run/mykos-install','mount -t 9p -o trans=virtio,version=9p2000.L mykosinstall /run/mykos-install'],
        'late-commands':late,'shutdown':'poweroff',
        'error-commands':['cp /var/log/installer/subiquity-server-debug.log /run/mykos-install/installer-error.log || true','journalctl --no-pager > /run/mykos-install/installer-journal.log','touch /run/mykos-install/installer-failed'],
        'user-data':{'runcmd':[['sh','-c',firstboot]]}}}
    return '#cloud-config\n'+json.dumps(data,indent=2)+'\n'


def stage(os_name, values, image):
    """Called once before launch, preparing private media using the same builder."""
    kind,version=vm.VERSIONS[os_name]
    target=vm.storage(os_name)/'storage/.mykos-installstate';vm.safe_dir(target)
    token=secrets.token_urlsafe(32)
    write(folder(os_name)/'callback.json',json.dumps({'digest':hashlib.sha256(token.encode()).hexdigest(),'expires':time.time()+14400,'used':False}))
    complete=folder(os_name)/'complete'
    if complete.exists():complete.unlink()
    if kind=='macos':macos_assets(os_name,values,token,target)
    elif kind=='ubuntu':
        setup=(Path(__file__).parents[1]/'web/ubuntu-image-setup.py')
        if not setup.exists():setup=Path('/opt/mykos-web/ubuntu-image-setup.py')
        write(target/'user-data',ubuntu_userdata(values,setup.read_bytes()))
        write(target/'meta-data',json.dumps({'instance-id':'mykos-build-'+secrets.token_hex(12),'local-hostname':'mykos-base'}))
    else:
        # Read the already-pulled image, without creating a helper container.
        mount=Path(vm.run([vm.PODMAN,'image','mount',image],timeout=30))
        try:
            asset={'win10':'win10x64','win11':'win11x64','win8e':'win81x64','win7u':'win7x64-ultimate'}.get(os_name,'win'+version)
            source=mount/'run/assets'/(asset+'.xml')
            if not source.is_file():raise ValueError('Windows image is missing its official '+asset+' answer file')
            write(target/'autounattend.xml',windows_answer(source.read_bytes(),values))
        finally:vm.run([vm.PODMAN,'image','unmount',image],timeout=30)
        windows_agent_package(os_name)
    return target


def windows_agent_package(os_name):
    """Use the guest agent from a signature-verified RHEL data RPM; never install it on the host."""
    import fcntl,stat
    cache=STATE/'.tools';vm.safe_dir(cache)
    with (cache/'virtio-win.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        packages=list(cache.glob('virtio-win-*.rpm'))
        if not packages:
            options=['--downloadonly','--downloaddir='+str(cache),'--setopt=install_weak_deps=False']
            for action in ('install','reinstall'):
                result=subprocess.run(['dnf','-q','-y',action,*options,'virtio-win'],capture_output=True,text=True,timeout=900)
                packages=list(cache.glob('virtio-win-*.rpm'))
                if result.returncode==0 and packages:break
            if not packages:raise ValueError('A signed virtio-win RPM must be available from the configured RHEL repositories')
        package=max(packages,key=lambda p:p.stat().st_mtime)
        info=package.lstat()
        if package.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o022:
            raise ValueError('Unsafe guest-agent RPM cache file')
        verification=subprocess.run(['rpmkeys','--checksig',str(package)],capture_output=True,text=True,timeout=60)
        if verification.returncode or 'signatures OK' not in verification.stdout:
            raise ValueError('Guest-agent RPM must have a valid signature trusted by the host RPM key database')
        identity=subprocess.check_output(['rpm','-qp','--qf','%{NAME}\n%{PACKAGER}\n',str(package)],text=True,timeout=30).splitlines()
        if not identity or identity[0]!='virtio-win' or not any('Red Hat' in x for x in identity[1:]):
            raise ValueError('Expected the Red Hat virtio-win RPM')
        data=agent_from_rpm(package)
        path=vm.storage(os_name)/'oem/qemu-ga-x86_64.msi'
        write(path,data)
        digest=hashlib.sha256()
        with package.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        write(path.with_suffix('.json'),json.dumps({'authentication':'trusted-rhel-rpm-signature','rpm':package.name,'rpm_sha256':digest.hexdigest(),'sha256':hashlib.sha256(data).hexdigest()}))


def agent_from_rpm(package):
    """Extract exactly one bounded regular file from the authenticated RPM's CPIO payload."""
    import stat
    process=subprocess.Popen(['rpm2cpio',str(package)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    def read(size):
        data=bytearray()
        while len(data)<size:
            chunk=process.stdout.read(size-len(data))
            if not chunk:raise ValueError('Incomplete virtio-win RPM payload')
            data.extend(chunk)
        return bytes(data)
    try:
        while True:
            header=read(110)
            if header[:6] not in (b'070701',b'070702'):raise ValueError('Unsupported RPM payload format')
            fields=[int(header[i:i+8],16) for i in range(6,110,8)]
            if not 0<fields[11]<16384:raise ValueError('Invalid RPM payload name')
            name=read(fields[11]).rstrip(b'\0').decode();read((-(110+fields[11]))%4);size=fields[6]
            if name.lstrip('./')=='usr/share/virtio-win/guest-agent/qemu-ga-x86_64.msi':
                if not stat.S_ISREG(fields[1]) or not 0<size<=32*1024*1024:raise ValueError('Invalid guest-agent RPM entry')
                data=read(size)
                if not data.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):raise ValueError('Guest-agent payload is not an MSI')
                return data
            if name=='TRAILER!!!':raise ValueError('The signed RPM does not contain the x86-64 guest agent')
            while size:
                part=read(min(1024*1024,size));size-=len(part)
            read((-fields[6])%4)
    finally:
        process.stdout.close();process.terminate();process.wait(timeout=10)

def launch_arguments(value, key):
    if not value.get('unattended') or value['role']!='template' or value.get('installation_complete') or value.get('ready'):return []
    kind=vm.VERSIONS[value['os']][0]
    args=['-e','MYKOS_UNATTENDED='+kind]
    if kind=='windows':
        asset={'win10':'win10x64','win11':'win11x64','win8e':'win81x64'}.get(value['os'],'win'+vm.VERSIONS[value['os']][1])
        args+=['-v',str(vm.storage(key)/'storage/.mykos-installstate/autounattend.xml')+':/run/mykos-autounattend.xml:ro','-e','MYKOS_WINDOWS_ASSET='+asset,'-e','MANUAL=N']
    else:
        tools=Path(__file__).with_name('mykos-unattended-entry')
        args+=['-v',str(tools)+':/run/mykos-unattended-entry:ro']
        if kind=='ubuntu':args+=['-v',str(Path(__file__).with_name('mykos-ubuntu-iso'))+':/run/mykos-ubuntu-iso:ro','-e','MYKOS_UBUNTU_ISO_URL='+value['boot']]
        if kind=='macos':args+=['-v',str(RECIPES/'macos/patch.py')+':/run/mykos-recovery-patch.py:ro']
    return args


def archive_existing(os_name):
    with vm.locked():
        if not vm.record_path(os_name).exists():return
        value=vm.read(os_name)
        try:state=vm.inspect(value['name'])
        except RuntimeError:state=None
        if state and state['State'].get('Running'):raise ValueError('Stop the existing base image builder before replacing it')
        if state:
            labels=state['Config'].get('Labels') or {}
            if labels.get('io.mykos.vm.os')!=os_name or labels.get('io.mykos.vm.role')!='template':raise ValueError('Base image identity mismatch')
            vm.run([vm.PODMAN,'rm',value['name']],timeout=30)
        saved=vm.storage(os_name).with_name('.previous-'+os_name+'-'+str(int(time.time()))+'-'+secrets.token_hex(4))
        saved.mkdir(mode=0o700)
        if vm.storage(os_name).exists():os.replace(vm.storage(os_name),saved/'storage')
        shutil.move(str(vm.record_path(os_name)),str(saved/'record.json'))


def installed(value):
    kind=vm.VERSIONS[value['os']][0]
    if kind=='macos':return (folder(value['os'])/'complete').exists()
    if kind=='windows':
        from mykos_commands import powershell
        result=powershell(value,"$ErrorActionPreference='Stop'; if (-not (Test-Path 'C:\\ProgramData\\MYKOS\\image-install-complete')) {throw 'Provisioning incomplete'}; if ((Get-ItemProperty 'HKLM:\\SYSTEM\\Setup').SystemSetupInProgress -ne 0) {throw 'Setup is active'}; Write-Output 'installed'",12,allow_image_builder=True)
        return result.get('exit_code')==0 and not result.get('timed_out')
    from mykos_ubuntu_vpn import execute
    execute(value,'/bin/sh',('-c','test -f /var/lib/mykos/image-install-complete && systemctl is-active --quiet display-manager.service && pgrep -x gnome-shell >/dev/null'),timeout=8)
    return True


def wait_installed(os_name, seconds=10800):
    started=time.monotonic();deadline=started+seconds;next_notice=started+60;last='Waiting for unattended installation'
    while time.monotonic()<deadline:
        if time.monotonic()>=next_notice:
            report(os_name,status(os_name).get('state','building'),'Waiting for '+os_name+' installed-boot checks ('+str(int((time.monotonic()-started)//60))+' min); console: /mykos/console/'+os_name+'/')
            next_notice=time.monotonic()+60
        value=vm.read(os_name)
        if (vm.storage(os_name)/'storage/.mykos-installstate/installer-failed').exists():
            raise ValueError('Unattended installer failed; see .mykos-installstate/installer-journal.log in its storage')
        try:
            if installed(value):
                if vm.VERSIONS[os_name][0]=='macos':time.sleep(3)
                return
        except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as error:last=str(error)
        try:state=vm.inspect(value['name']).get('State',{})
        except subprocess.TimeoutExpired:
            # Another OS may be committing a large disk layer under Podman's
            # storage lock. Keep this bounded build alive without overlapping calls.
            time.sleep(10);continue
        if not state.get('Running'):raise ValueError('Image builder exited before installation completed; inspect its console/logs')
        time.sleep(10)
    raise TimeoutError('Image installation timed out: '+last)


def boot_installed(os_name):
    """Detach installation-only devices before the startup memory capture."""
    with vm.locked():
        value=vm.read(os_name);kind=vm.VERSIONS[os_name][0]
        vm.stop_container(value['name'])
        vm.run([vm.PODMAN,'rm',value['name']],timeout=30)
        value['installation_complete']=True;value['ram_mib']=2048
        value['cpu_env']=vm.checkpoint_cpu_profile(os_name)
        if kind=='windows':value.update(runtime_memory_prepared=True,runtime_pagefile='fixed-12288')
        complete=folder(os_name)/'complete'
        if kind=='macos':arm_second_boot(os_name)
        # Drop recovery/installer media only in the newly built image's own storage.
        storage=vm.storage(os_name)/'storage'
        for name in ('autoinstall.iso','custom.iso','installer.iso'):
            p=storage/name
            if p.is_file() and not p.is_symlink():p.unlink()
        if kind=='windows':
            for p in [*storage.glob('*.iso'),storage/'setup.img',storage/'.mykos-installstate/autounattend.xml']:
                if p.is_file() and not p.is_symlink():p.unlink()
        if kind=='macos':
            for p in storage.rglob('*.dmg'):
                if p.name in ('base.dmg','setup.dmg') and not p.is_symlink():p.unlink()
        vm.save(os_name,value);vm.launch(os_name,value)


def build(os_name, username=None, password=None, replace=False):
    selection(os_name)
    # One build per OS; keep long installation work outside the VM registry lock.
    import fcntl
    with (folder(os_name)/'build.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('This OS image is already building')
        previous=vm.read(os_name) if vm.record_path(os_name).exists() and not replace else None
        if previous:
            if previous.get('role')!='template' or not previous.get('unattended') or previous.get('ready'):
                raise ValueError('An existing image is not an unfinished unattended build; use --replace to rebuild')
            if username is not None or password is not None:
                raise ValueError('Resume uses the existing build credentials; use --replace to change them')
        values=credentials(os_name,username,password)
        try:
            report(os_name,'building','Starting unattended '+os_name+' installation')
            if replace:archive_existing(os_name)
            if previous:
                report(os_name,'building','Resuming unattended '+os_name+' installation')
                if vm.VERSIONS[os_name][0]=='macos': renew_completion_window(os_name)
                vm.resume(os_name)
            else:
                vm.create(os_name,values['username'],values['password'],2048,unattended=True)
            wait_installed(os_name)
            report(os_name,'configuring','Booting the installed system without installation-only devices')
            if not vm.read(os_name).get('installation_complete'):boot_installed(os_name)
            wait_installed(os_name,900)
            report(os_name,'capturing','Capturing installed state and committing '+vm.template_image(os_name))
            install_state=vm.storage(os_name)/'storage/.mykos-installstate'
            if install_state.exists():
                if install_state.is_symlink():raise ValueError('Unsafe installation-state directory')
                shutil.rmtree(install_state)
            result=vm.prepare(os_name,keep_running=True,embed_disk=True)
            try:
                upstream='docker.io/qemux/qemu:latest' if os_name.startswith('ubuntu') else 'docker.io/dockurr/'+vm.VERSIONS[os_name][0]+':latest'
                image_id=json.loads(vm.run([vm.PODMAN,'image','inspect',upstream],timeout=30))[0]['Id']
                vm.run([vm.PODMAN,'untag',image_id,upstream],timeout=30)
            except (OSError,ValueError,RuntimeError,KeyError,IndexError):pass
            callback_file=folder(os_name)/'callback.json'
            if callback_file.exists():callback_file.unlink()
            report(os_name,'ready','Image committed; the generic base container is running')
            return result
        except BaseException:
            report(os_name,'failed','Build did not finish; retained its disks and diagnostics')
            raise


def embed_capture(value, source):
    """Commit the installed OS/state, not just a thin VM wrapper filesystem."""
    mount=Path(vm.run([vm.PODMAN,'mount',value['name']],timeout=30))
    try:
        if not mount.is_absolute() or not mount.is_dir():raise ValueError('Cannot mount stopped builder root filesystem')
        if vm.VERSIONS[value['os']][0]=='windows':
            original=mount/'run/.mykos-original-answer.xml'
            asset={'win10':'win10x64','win11':'win11x64','win8e':'win81x64'}.get(value['os'],'win'+vm.VERSIONS[value['os']][1])
            if original.is_file() and not original.is_symlink():
                shutil.copyfile(original,mount/'run/assets'/(asset+'.xml'));original.unlink()
        parent=mount/'opt/mykos-image'
        if parent.is_symlink() or (mount/'opt').is_symlink():raise ValueError('Unsafe image payload directory')
        parent.mkdir(parents=True,exist_ok=True)
        target=parent/'golden'
        if target.exists():shutil.rmtree(target)
        # OCI tar layers otherwise materialize every byte of a sparse 64 GiB
        # disk, including its holes. Keep a compressed sparse archive in the
        # committed image; external immutable state remains reflinkable.
        payload=parent/'golden.tar.gz'
        vm.run(['/usr/bin/tar','--sparse','--numeric-owner','-C',str(source),'-czf',str(payload),'.'])
        payload.chmod(0o600)
        write(parent/'image.json',json.dumps({'format':2,'os':value['os'],'capture_mode':'memory','ram_mib':2048,'payload':'golden.tar.gz'}))
    finally:vm.run([vm.PODMAN,'unmount',value['name']],timeout=30)


def queue(os_name, username=None, password=None, replace=False):
    import fcntl
    path=folder(os_name)
    with (path/'queue.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        unit='mykos-image-build@'+selection(os_name)+'.service'
        active=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=10).stdout.strip()
        if active in ('active','activating','deactivating'):raise ValueError('Image build is already active')
        write(path/'request.json',json.dumps({'username':username,'password':password,'replace':bool(replace)}))
        report(os_name,'queued','Unattended image build queued')
        subprocess.run(['systemctl','start','--no-block',unit],check=True,timeout=10)
    return status(os_name)


def worker(os_name):
    request=folder(os_name)/'request.json'
    data=json.loads(request.read_text())
    try:return build(os_name,**data)
    finally:request.unlink()


def macos_ready_service(os_name, token):
    callback_text=callback_script(os_name,token)
    # An installed-system launch daemon reports each of the two verified boots.
    # Its token is private, short-lived, scoped to this one build and removed
    # automatically after the second boot. It is not an administrative API key.
    ready="""#!/bin/bash
set -eu
for (( n=0; n<300; n++ )); do
    /usr/bin/pgrep -x WindowServer >/dev/null && break
    sleep 2
done
/usr/bin/pgrep -x WindowServer >/dev/null
"""+callback_text.replace('/usr/bin/curl --fail', 'response=$(/usr/bin/curl --fail').replace('--config "$config"\n','--config "$config")\n')+"""
if printf '%s' "$response" | /usr/bin/grep -Eq '\"remove\"[[:space:]]*:[[:space:]]*true'; then
    rm -f /Library/LaunchDaemons/com.mykos.image-ready.plist /usr/local/libexec/mykos-image-ready
fi
"""
    import plistlib
    plist=plistlib.dumps({'Label':'com.mykos.image-ready','ProgramArguments':['/usr/local/libexec/mykos-image-ready'],'RunAtLoad':True,'StandardErrorPath':'/var/log/mykos-image-ready.log'}).decode()
    return """/bin/mkdir -p /usr/local/libexec
/bin/cat > /usr/local/libexec/mykos-image-ready <<'MYKOS_READY'
"""+ready+"""MYKOS_READY
/bin/chmod 700 /usr/local/libexec/mykos-image-ready
/bin/cat > /Library/LaunchDaemons/com.mykos.image-ready.plist <<'MYKOS_PLIST'
"""+plist+"""MYKOS_PLIST
/bin/chmod 644 /Library/LaunchDaemons/com.mykos.image-ready.plist
/bin/launchctl enable system/com.openssh.sshd || exit 1
/bin/launchctl bootstrap system /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || :
/bin/launchctl bootstrap system /Library/LaunchDaemons/com.mykos.image-ready.plist || exit 1
"""


def instance_setup(shop, value):
    """Run optional operator-owned ID setup only after cloning the base state."""
    import stat
    from mykos_runtime import shopcode
    shopcode(shop)
    path=Path('/etc/mykos/instance-hooks')/value['os']
    if not path.exists():return
    for parent in (path.parent,*path.parent.parents):
        if parent.is_symlink():raise ValueError('Unsafe instance hook directory')
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022 or not info.st_mode & 0o100:
        raise ValueError('Instance hook must be an executable, root-owned regular file without group/world write access')
    subprocess.run([str(path),shop,value['name']],check=True,timeout=300,
                   env={**os.environ,'MYKOS_SHOPCODE':shop,'MYKOS_CONTAINER_NAME':value['name'],'MYKOS_OS':value['os']})
