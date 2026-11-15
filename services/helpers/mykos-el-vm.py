"""Public EL Server and GNOME Desktop VMs with independent guest kernels/disks.

Build from the selected local native image, preserving that image. Only the
new, owned worker/disk is changed. No application adapter is used here.
"""
from pathlib import Path
import hashlib,json,os,re,shlex,shutil,subprocess,tempfile,uuid,signal,time
from mykos_runtime import EL_VM_PROFILES
PROFILES=EL_VM_PROFILES

def run(args,timeout=600,**kwargs):
    data=kwargs.pop('input',None)
    p=subprocess.Popen(list(map(str,args)),stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,**kwargs)
    try:out,err=p.communicate(data,timeout=timeout)
    except BaseException:
        try:os.killpg(p.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:p.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            try:os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            p.communicate()
        raise
    if p.returncode:raise RuntimeError(str(args[:2])+': '+(err or out)[-2500:])
    return out.strip()


def origin():
    from mykos_runtime import config
    value=os.environ.get('MYKOS_REPO_ORIGIN') or config().get('repository_origin') or 'https://repo.myk.sh'
    if not isinstance(value,str) or not re.fullmatch(r'https?://[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]{1,5})?/?',value):
        raise ValueError('Configure the existing LAN repository origin before building an RHEL VM')
    return value.rstrip('/')


def guestfish(disk,commands,work,timeout=1800):
    for name in ('guestfs-cache','guestfs-tmp'):(work/name).mkdir(mode=0o700,exist_ok=True)
    env={**os.environ,'LIBGUESTFS_BACKEND':'direct','LIBGUESTFS_MEMSIZE':'1024','LIBGUESTFS_CACHEDIR':str(work/'guestfs-cache'),'LIBGUESTFS_TMPDIR':str(work/'guestfs-tmp')}
    # guestfish uses line-oriented commands: shell quoting cannot encode newlines.
    if any(not command or not re.fullmatch('[a-z0-9-]+',str(command[0])) or any('\0' in str(x) for x in command) for command in commands):raise ValueError('Invalid guestfish command')
    data='\n'.join(str(command[0])+''.join(' '+json.dumps(str(x),ensure_ascii=False) for x in command[1:]) for command in commands)+'\n'
    return run(['nice','-n','15','ionice','-c','2','-n','7','guestfish','--rw','--format=qcow2','-a',disk],timeout=timeout,input=data,env=env)


def repository_config(selected):
    profile=PROFILES[selected];major=profile['major'];family=profile['family']
    if family=='rh':
        base=origin()
        urls=[base+'/rhel-'+major+'-for-x86_64-'+part+'-rpms' for part in ('baseos','appstream')]
    elif family=='oracle':
        base='https://yum.oracle.com/repo/OracleLinux/OL'+major+'/'
        urls=[base+'baseos/latest/x86_64/',base+'appstream/x86_64/']
    else:
        if family=='rocky':base='https://dl.rockylinux.org/pub/rocky/'+major+'/'
        elif family=='alma':base='https://repo.almalinux.org/almalinux/'+major+'/'
        elif family=='centos':base=('https://vault.centos.org/8-stream/' if major=='8' else 'https://mirror.stream.centos.org/'+major+'-stream/')
        else:raise ValueError('Unsupported EL vendor')
        urls=[base+part+'/x86_64/os/' for part in ('BaseOS','AppStream')]
    return ''.join('[mykos-vm-'+part+']\nname=MYKOS EL VM '+part+'\nbaseurl='+url+'\nenabled=1\ngpgcheck=1\ngpgkey=file:///etc/pki/rpm-gpg/MYKOS-EL-VM-KEYS\n\n' for part,url in zip(('baseos','appstream'),urls))



def appliance_initramfs_commands(selected,kernel):
    """Vendor initramfs tools run on the owned, correctly labeled ext4 disk."""
    if selected not in PROFILES or not re.fullmatch(r'[A-Za-z0-9_.+-]+',kernel):raise ValueError('Invalid EL guest kernel identity')
    image='/boot/initramfs-'+kernel+'.img'
    script='set -eu; dracut --force --no-hostonly --kver '+shlex.quote(kernel)+'; test -s '+shlex.quote(image)+'; '
    script+="if test -x /usr/lib/systemd/systemd-sysroot-fstab-check; then lsinitrd "+shlex.quote(image)+" | grep -F usr/lib/systemd/systemd-sysroot-fstab-check > /dev/null; fi"
    return [['sh',script]]



def parse_selinux_config(text):
    """Read only literal mode/type settings; never source guest shell content."""
    values={}
    for line in text.splitlines():
        if line.lstrip().startswith('#') or '=' not in line:continue
        key,value=line.split('=',1);key=key.strip()
        if key not in ('SELINUX','SELINUXTYPE'):continue
        if key in values:raise ValueError('Duplicate guest SELinux policy setting')
        values[key]=value.split('#',1)[0].strip().strip('\"\'')
    mode=values.get('SELINUX');kind=values.get('SELINUXTYPE','')
    if mode not in ('enforcing','permissive','disabled') or not re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,63}',kind):raise ValueError('Configure a literal guest SELinux mode and policy type')
    return {'mode':mode,'type':kind}


def relabel_commands(policy):
    kind=policy.get('type','')
    if not isinstance(kind,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,63}',kind):raise ValueError('Invalid guest SELinux policy type')
    return [['selinux-relabel','/etc/selinux/'+kind+'/contexts/files/file_contexts','/','force:true']]


def appliance_finalization_commands(selected,kernel,policy,root_uuid):
    # Never copy host/container MCS labels into an independently booted guest.
    # Relabel using its own selected policy; preserve the configuration and mode.
    labels=relabel_commands(policy)
    return [['touch','/.autorelabel'],*labels,*appliance_initramfs_commands(selected,kernel),['sh','/usr/bin/python3 -c '+shlex.quote(boot_setup_script(root_uuid,kernel))],*labels,['checksum','sha256','/etc/selinux/config'],['sync'],['umount-all'],['shutdown']]


def build_disk(selected,output,credentials):
    if os.geteuid()!=0 or selected not in PROFILES:raise ValueError('Choose an EL VM profile as root')
    for tool in ('podman','guestfish','qemu-img'):
        if not shutil.which(tool):raise ValueError('Install the EL VM builder dependency '+tool)
    output=Path(output)
    if output.exists() or output.is_symlink():raise ValueError('Refuse to overwrite an existing VM disk')
    from mykos_vm import safe_dir
    from mykos_runtime import build_priority,config as host_config
    from mykos_network import arguments as network_arguments
    build_priority();safe_dir(output.parent)
    if shutil.disk_usage(output.parent).free<40*1024**3:raise ValueError('Leave at least 40 GiB free before building an EL VM disk')
    reference='localhost/'+PROFILES[selected]['native']+':latest'
    try:
        info=json.loads(run(['podman','image','inspect',reference]))[0];image=info['Id']
        labels=info.get('Labels') or info.get('Config',{}).get('Labels') or {}
        if labels.get('io.mykos.os') not in (None,PROFILES[selected]['native']):raise ValueError('Native source image identity does not match the selected EL VM')
    except RuntimeError as error:raise ValueError('Build the matching native '+PROFILES[selected]['native']+' image before building this VM profile') from error
    major=PROFILES[selected]['major'];assert major in ('8','9','10')
    work=Path(tempfile.mkdtemp(prefix='.el-vm-build-',dir=output.parent));owner=uuid.uuid4().hex;name='mykos-el-vm-'+selected+'-'+owner[:10];cid=None;published=False
    try:
        username=credentials['username'];password=credentials['password']
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,19}',username) or username in ('root','guest','administrator'):raise ValueError('Choose a normal guest login account')
        if not isinstance(password,str) or not 12<=len(password)<=100 or any(ord(c)<32 for c in password):raise ValueError('Invalid guest password')
        hashed=run(['openssl','passwd','-6','-stdin'],input=password+'\n')
        if not re.fullmatch(r'\$6\$[^\s]+',hashed):raise ValueError('Guest password hashing failed')
        login=work/'login.json';login.write_text(json.dumps({'username':username,'password_hash':hashed}));login.chmod(0o600)
        script='''set -euo pipefail
export SYSTEMD_OFFLINE=1
major=MAJOR
. /etc/os-release
test "${VERSION_ID%%.*}" = "$major"
test "$ID" = EXPECTED_VENDOR
mkdir -p /etc/pki/rpm-gpg
: > /etc/pki/rpm-gpg/MYKOS-EL-VM-KEYS
for key in VENDOR_KEY_GLOBS; do
    if test -f "$key"; then cat "$key" >> /etc/pki/rpm-gpg/MYKOS-EL-VM-KEYS; fi
done
test -s /etc/pki/rpm-gpg/MYKOS-EL-VM-KEYS
test ! -e /etc/yum.repos.d/mykos-vm-build.repo; test ! -L /etc/yum.repos.d/mykos-vm-build.repo
cat > /etc/yum.repos.d/mykos-vm-build.repo <<'REPOS'
REPOSITORIES
REPOS
dnf -y --disablerepo='*' --enablerepo=mykos-vm-baseos --enablerepo=mykos-vm-appstream --disableexcludes=all --setopt=install_weak_deps=False --setopt=gpgcheck=1 --setopt=localpkg_gpgcheck=1 install tar gzip iproute util-linux policycoreutils selinux-policy-targeted sudo shadow-utils /usr/bin/passwd procps-ng python3 kernel kernel-modules dracut grub2-tools grubby grub2-efi-x64 shim-x64 qemu-guest-agent NetworkManager openssh-server audit chrony libreswan
DESKTOP_PACKAGES
kernel=$(rpm -q kernel-core --qf '%{VERSION}-%{RELEASE}.%{ARCH}\\n' | sort -V | tail -n1)
test -n "$kernel"
mkdir -p /etc/dracut.conf.d
printf '%s\\n' 'hostonly="no"' 'add_drivers+=" virtio_pci virtio_blk virtio_scsi virtio_net virtio_console ext4 "' > /etc/dracut.conf.d/90-mykos-vm.conf
# Newer RHEL systemd requires this helper in the initramfs as well as root.
if test -x /usr/lib/systemd/systemd-sysroot-fstab-check; then
    printf '%s\\n' 'install_items+=" /usr/lib/systemd/systemd-sysroot-fstab-check "' >> /etc/dracut.conf.d/90-mykos-vm.conf
fi
# Container-aware kernel hooks may omit the boot copies from minimal bases.
mkdir -p /boot
if test ! -s "/boot/vmlinuz-$kernel" && test -s "/lib/modules/$kernel/vmlinuz"; then
    install -m 0644 "/lib/modules/$kernel/vmlinuz" "/boot/vmlinuz-$kernel"
fi
if test ! -s "/boot/.vmlinuz-$kernel.hmac" && test -s "/lib/modules/$kernel/.vmlinuz.hmac"; then
    install -m 0644 "/lib/modules/$kernel/.vmlinuz.hmac" "/boot/.vmlinuz-$kernel.hmac"
fi
test -s "/boot/vmlinuz-$kernel"
# Initramfs generation runs later on the final ext4 disk inside the appliance.
# This avoids overlay attribute limitations and labels the independent guest.
cp /etc/selinux/config /output/selinux-config
# Restore vendor service hardening in this VM; native CRIU profiles stay untouched.
for dropin in /etc/systemd/system/*.service.d/90-mykos-checkpoint.conf; do
    test ! -f "$dropin" || rm -f "$dropin"
done
systemctl unmask systemd-logind.service getty.target console-getty.service systemd-remount-fs.service dev-hugepages.mount sys-kernel-config.mount systemd-udevd.service systemd-udevd-control.socket systemd-udevd-kernel.socket systemd-udev-trigger.service sys-fs-fuse-connections.mount auditd.service chronyd.service tuned.service
mkdir -p /etc/systemd/system/qemu-guest-agent.service.d
cat > /etc/systemd/system/qemu-guest-agent.service.d/mykos-management.conf <<'MYKOS_AGENT'
[Service]
Type=simple
ExecStart=
ExecStart=/usr/bin/qemu-ga --method=virtio-serial --path=/dev/virtio-ports/org.qemu.guest_agent.0 -b guest-file-open,guest-file-close,guest-file-read,guest-file-write,guest-file-seek,guest-file-flush
MYKOS_AGENT
systemctl enable auditd chronyd qemu-guest-agent NetworkManager sshd serial-getty@ttyS0.service
# Normal login on both serial and graphical consoles, never an automatic root shell.
python3 - <<'MYKOS_EL_ACCOUNT'
import json,subprocess,pwd
with open('/output/login.json') as source:login=json.load(source)
name=login['username']
exists=subprocess.run(['id','-u',name],stdout=subprocess.PIPE,stderr=subprocess.PIPE).returncode==0
if not exists:subprocess.check_call(['useradd','--create-home','--shell','/bin/bash','--groups','wheel',name])
if pwd.getpwnam(name).pw_uid<1000:raise RuntimeError('Guest login must be a normal, non-system user')
subprocess.check_call(['usermod','--append','--groups','wheel','--shell','/bin/bash',name])
subprocess.check_call(['usermod','--lock','root'])
result=subprocess.run(['chpasswd','--encrypted'],input=name+':'+login['password_hash']+'\\n',universal_newlines=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
if result.returncode:raise RuntimeError('Guest account provisioning failed')
MYKOS_EL_ACCOUNT
rm -f /etc/systemd/system/serial-getty@ttyS0.service.d/mykos-private-console.conf /etc/NetworkManager/conf.d/90-mykos-podman.conf
# GNOME's server network policy disables automatic connection creation.
# Supply an explicit DHCP connection for this fresh VM, without copying host IPs.
install -d -m 0700 /etc/NetworkManager/system-connections
cat > /etc/NetworkManager/system-connections/mykos-vm.nmconnection <<'MYKOS_DHCP'
[connection]
id=MYKOS VM
uuid=DHCP_CONNECTION_UUID
type=ethernet
autoconnect=true
autoconnect-priority=100
[ethernet]
[ipv4]
method=auto
[ipv6]
method=auto
MYKOS_DHCP
chmod 0600 /etc/NetworkManager/system-connections/mykos-vm.nmconnection
DESKTOP_SERVICES
rm -f /etc/ssh/ssh_host_* /var/lib/systemd/random-seed
: > /etc/machine-id
printf '%s\\n' mykos-el-vm > /etc/hostname
rm -f /etc/dnf/vars/infra /etc/yum.repos.d/mykos-vm-build.repo
printf '%s' "$kernel" > /output/kernel-version
cp /etc/os-release /output/os-release
vendor=
for directory in /boot/efi/EFI/*; do
    if test "${directory##*/}" != BOOT && test -f "$directory/shimx64.efi" && test -f "$directory/grubx64.efi"; then vendor=${directory##*/}; break; fi
done
test -n "$vendor"
printf '%s' "$vendor" > /output/efi-vendor
tar -C /boot/efi -cf /output/efi.tar EFI
dnf clean all
'''
        profile=PROFILES[selected];repos=repository_config(selected)
        keys={'rh':'/etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release','rocky':'/etc/pki/rpm-gpg/*Rocky*','alma':'/etc/pki/rpm-gpg/*AlmaLinux*','oracle':'/etc/pki/rpm-gpg/*oracle*','centos':'/etc/pki/rpm-gpg/*centos* /etc/pki/rpm-gpg/*CentOS*'}
        vendor={'rh':'rhel','rocky':'rocky','alma':'almalinux','oracle':'ol','centos':'centos'}
        desktop_dnf="dnf -y --disablerepo='*' --enablerepo=mykos-vm-baseos --enablerepo=mykos-vm-appstream --setopt=install_weak_deps=False --setopt=gpgcheck=1"
        # Minimal vendor OCI bases can use the conflicting coreutils-single RPM.
        # Replace that exact package in the owned worker before GNOME dependencies.
        desktop_packages=("if rpm -q coreutils-single >/dev/null 2>&1; then "+desktop_dnf+" swap coreutils-single coreutils; fi\n"+desktop_dnf+" group install graphical-server-environment") if profile['desktop'] else ':'
        desktop_services='systemctl enable gdm; systemctl set-default graphical.target' if profile['desktop'] else 'systemctl set-default multi-user.target'
        script=script.replace('DHCP_CONNECTION_UUID',str(uuid.uuid4())).replace('MAJOR',major).replace('REPOSITORIES',repos).replace('EXPECTED_VENDOR',shlex.quote(vendor[profile['family']])).replace('VENDOR_KEY_GLOBS',keys[profile['family']]).replace('DESKTOP_PACKAGES',desktop_packages).replace('DESKTOP_SERVICES',desktop_services)
        # EL8 kernel-install exits without BLS entries when machine-id is empty.
        # Seed only this owned EL8 template; new instances still get fresh IDs.
        if major=='8':script=script.replace(': > /etc/machine-id',"printf '%s\\n' "+uuid.uuid4().hex+" > /etc/machine-id")
        cid=run(['podman','create','--name',name,'--pull=never','--image-volume=ignore',*network_arguments(host_config(),name),'--memory=2g','--cpus=1','--cpu-shares=64','--cgroup-conf=io.weight=default 10','--oom-score-adj=500','--label','io.mykos.el-vm-builder='+owner,'-v',str(work)+':/output:rw,Z','--entrypoint','/bin/bash',image,'-ec',script])
        run(['podman','start','--attach',cid],timeout=1800)
        archive=work/'rootfs.tar'
        state=json.loads(run(['podman','inspect',cid]))[0]
        if state.get('State',{}).get('Running') or (state.get('Config',{}).get('Labels') or {}).get('io.mykos.el-vm-builder')!=owner:
            raise ValueError('Archive only this stopped EL VM build worker')
        mounted=Path(run(['podman','mount',cid]))
        try:
            if not mounted.is_absolute() or not mounted.is_dir() or mounted.is_symlink() or os.path.ismount(mounted/'output'):
                raise ValueError('Cannot verify the owned container root for attribute-preserving copy')
            run(['/usr/bin/tar','--sparse','--numeric-owner','--xattrs','--xattrs-include=*','--acls','--selinux','--one-file-system','--exclude=./output','-C',mounted,'-cf',archive,'.'],timeout=3600)
        finally:run(['podman','unmount',cid])

        policy_bytes=(work/'selinux-config').read_bytes();policy=parse_selinux_config(policy_bytes.decode());policy_hash=hashlib.sha256(policy_bytes).hexdigest()
        kernel=(work/'kernel-version').read_text().strip()
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+',kernel):raise ValueError('Invalid guest kernel identity')
        efi_vendor=(work/'efi-vendor').read_text().strip()
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',efi_vendor):raise ValueError('Invalid vendor EFI directory')
        root_uuid=str(uuid.uuid4());disk=work/'disk.qcow2';run(['qemu-img','create','-f','qcow2',disk,'64G'])
        fstab=work/'fstab';fstab.write_text('UUID='+root_uuid+' / ext4 defaults 0 1\nLABEL=MYKOS_EFI /boot/efi vfat defaults,umask=0077 0 2\n')
        grub=work/'grub.cfg';grub.write_text('set default=0\nset timeout=1\nmenuentry "MYKOS EL'+major+' VM" {\n search --no-floppy --fs-uuid --set=root '+root_uuid+'\n linux /boot/vmlinuz-'+kernel+' root=UUID='+root_uuid+' ro console=tty0 console=ttyS0,115200n8\n initrd /boot/initramfs-'+kernel+'.img\n}\n')
        stub=work/'efi-grub.cfg';stub.write_text('search --no-floppy --fs-uuid --set=root '+root_uuid+'\nset prefix=($root)/boot/grub2\nconfigfile $prefix/grub.cfg\n')
        cmds=[['set-smp','1'],['run'],['part-init','/dev/sda','gpt'],['part-add','/dev/sda','p','2048','1050623'],['part-add','/dev/sda','p','1050624','-2049'],['part-set-gpt-type','/dev/sda','1','c12a7328-f81f-11d2-ba4b-00a0c93ec93b'],['mkfs','vfat','/dev/sda1'],['set-label','/dev/sda1','MYKOS_EFI'],['mkfs','ext4','/dev/sda2'],['set-uuid','/dev/sda2',root_uuid],['mount','/dev/sda2','/'],['tar-in',archive,'/','xattrs:true','selinux:true','acls:true'],['mkdir-p','/boot/efi'],['mount','/dev/sda1','/boot/efi'],['tar-in',work/'efi.tar','/boot/efi'],['mkdir-p','/boot/efi/EFI/BOOT'],['cp','/boot/efi/EFI/'+efi_vendor+'/shimx64.efi','/boot/efi/EFI/BOOT/BOOTX64.EFI'],['cp','/boot/efi/EFI/'+efi_vendor+'/grubx64.efi','/boot/efi/EFI/BOOT/grubx64.efi'],['upload',stub,'/boot/efi/EFI/BOOT/grub.cfg'],['upload',stub,'/boot/efi/EFI/'+efi_vendor+'/grub.cfg'],['mkdir-p','/boot/grub2'],['upload',grub,'/boot/grub2/grub.cfg'],['upload',fstab,'/etc/fstab'],*appliance_finalization_commands(selected,kernel,policy,root_uuid)]
        finalized=guestfish(disk,cmds,work,timeout=3600)
        if not finalized.splitlines() or finalized.splitlines()[-1]!=policy_hash:raise ValueError('Guest SELinux configuration changed during disk preparation')
        check=json.loads(run(['qemu-img','info','--output=json',disk]));assert check['format']=='qcow2' and check['virtual-size']==64*1024**3
        os.replace(disk,output);published=True
        receipt={'profile':selected,'source_native_image':image,'desktop':profile['desktop'],'login_account':username,'native_source':profile['native'],'native_os_release_verified':True,'native_label_present':bool(labels.get('io.mykos.os')),'guest_kernel':kernel,'root_uuid':root_uuid,'initramfs_builder':'isolated ext4 appliance','selinux_policy':{**policy,'config_sha256':policy_hash,'source_mode_and_type_preserved':True},'disk_format':'qcow2','disk_virtual_bytes':check['virtual-size'],'native_image_changed':False,'guest_data_inside_snapshot_disk':True,'host_block_mounts_used':False,'file_attributes':'xattrs/ACLs copied; SELinux labels rebuilt from selected guest policy; owned overlay unmounted'}
        return receipt
    finally:
        if cid is None and subprocess.run(['podman','container','exists',name],capture_output=True).returncode==0:
            candidate=json.loads(run(['podman','inspect',name]))[0]
            if (candidate.get('Config',{}).get('Labels') or {}).get('io.mykos.el-vm-builder')==owner:cid=candidate['Id']
        if cid:
            check=json.loads(run(['podman','inspect',cid]))[0]
            if (check.get('Config',{}).get('Labels') or {}).get('io.mykos.el-vm-builder')!=owner:raise ValueError('Builder ownership changed; retain for review')
            if check['State']['Running']:run(['podman','stop','--time','15',cid],timeout=30)
            run(['podman','rm','--volumes',cid])
        shutil.rmtree(work)


def boot_setup_script(root,kernel):
    """Prepare vendor BLS entries offline on only the newly assembled disk."""
    if not re.fullmatch('[0-9a-fA-F-]{36}',root) or not re.fullmatch('[A-Za-z0-9_.+-]+',kernel):raise ValueError('Invalid guest root or kernel identity')
    return 'root='+repr(root)+';kernel='+repr(kernel)+'\n'+r"""import json,os,pathlib,re,shlex,subprocess
args='root=UUID='+root+' ro console=tty0 console=ttyS0,115200n8'
# The offline package worker can leave entries naming its host root. This is
# a new, unready template: discard those inherited entries before regenerating.
entries=pathlib.Path('/boot/loader/entries')
for directory in (entries,pathlib.Path('/boot/efi/loader/entries')):
 if directory.is_symlink():raise RuntimeError('Unexpected BLS entry directory link')
 if directory.exists():
  for entry in directory.glob('*.conf'):
   if entry.is_symlink() or not entry.is_file():raise RuntimeError('Unexpected BLS entry type')
   entry.unlink()
entries.mkdir(parents=True,exist_ok=True)
pathlib.Path('/etc/kernel/cmdline').write_text(args+'\n')
defaults=pathlib.Path('/etc/default/grub')
text=defaults.read_text() if defaults.exists() else ''
for key,value in (('GRUB_ENABLE_BLSCFG','true'),('GRUB_DEFAULT','saved')):
 text=re.sub(r'(?m)^'+key+r'=.*$',key+'='+value,text) if re.search(r'(?m)^'+key+'=',text) else text+'\n'+key+'='+value+'\n'
defaults.write_text(text)
def call(args):
 result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=1800)
 if result.returncode:raise RuntimeError('Guest bootloader setup failed: '+(result.stderr or result.stdout)[-1000:])
call(['kernel-install','add',kernel,'/boot/vmlinuz-'+kernel])
selected=[]
for entry in sorted(entries.glob('*.conf')):
 if entry.is_symlink() or not entry.is_file() or not re.fullmatch('[A-Za-z0-9_.+-]+',entry.stem):raise RuntimeError('Unexpected generated BLS entry')
 fields={}
 for line in entry.read_text().splitlines():
  parts=line.split(None,1)
  if len(parts)==2:fields[parts[0]]=parts[1]
 options=fields.get('options','').replace('${kernelopts}',args).replace('$kernelopts',args)
 roots=[word for word in shlex.split(options) if word.startswith('root=')]
 if roots!=['root=UUID='+root]:raise RuntimeError('Generated BLS entry does not select guest root')
 if fields.get('linux','').rsplit('/',1)[-1]=='vmlinuz-'+kernel:selected.append(entry.stem)
if not selected:raise RuntimeError('No current guest kernel BLS entry was generated')
call(['grub2-editenv','/boot/grub2/grubenv','set','kernelopts='+args,'saved_entry='+selected[0]])
call(['grub2-editenv','/boot/grub2/grubenv','unset','next_entry'])
call(['grub2-mkconfig','-o','/boot/grub2/grub.cfg'])
config=pathlib.Path('/boot/grub2/grub.cfg').read_text()
if not re.search(r'(?m)^\s*blscfg\s*(?:;\s*)?(?:#.*)?$',config):raise RuntimeError('Vendor BLS boot configuration was not generated')
os.sync()
print('Vendor guest boot configuration installed')
"""


def wait_ready(value, timeout=900):
    """Read-only agent readiness; never execute a shell in its confined domain."""
    import ipaddress
    from mykos_commands import GuestAgent
    import mykos_vm as vm
    if value.get('os') not in PROFILES:raise ValueError('Select a public EL VM')
    profile=PROFILES[value['os']]
    vendor={'rh':'rhel','rocky':'rocky','alma':'almalinux','oracle':'ol','centos':'centos'}[profile['family']]
    deadline=time.monotonic()+timeout;last='guest agent not ready'
    while time.monotonic()<deadline:
        if not vm.inspect(value['name']).get('State',{}).get('Running'):raise RuntimeError('EL VM exited during boot')
        agent=None
        try:
            agent=GuestAgent(value,allow_image_builder=True,timeout=3) if value.get('role')=='template' else GuestAgent(value,timeout=3)
            identity=agent.call('guest-get-osinfo',{})
            if identity.get('id')!=vendor or identity.get('version-id','').split('.')[0]!=profile['major']:raise ValueError('Unexpected guest distribution or major release')
            filesystems=agent.call('guest-get-fsinfo',{})
            if not any(x.get('mountpoint')=='/' and x.get('type')=='ext4' for x in filesystems):raise ValueError('Waiting for the guest ext4 root filesystem')
            interfaces=agent.call('guest-network-get-interfaces',{})
            addresses=[ipaddress.ip_address(x['ip-address']) for item in interfaces if item.get('name')!='lo' for x in item.get('ip-addresses',[]) if x.get('ip-address-type')=='ipv4']
            if not any(not (x.is_loopback or x.is_link_local or x.is_unspecified or x.is_multicast) for x in addresses):raise ValueError('Waiting for the guest IPv4 network')
            return
        except InterruptedError:raise
        except (RuntimeError,ValueError,OSError) as error:last=str(error)
        finally:
            if agent:agent.close()
        time.sleep(2)
    raise RuntimeError('EL VM startup did not pass guest checks: '+last)


def provision_disk(shop,value,disk):
    """Cold-provision only this new instance disk, before its first boot."""
    from mykos_runtime import shopcode
    shopcode(shop)
    if value.get('os') not in PROFILES or value.get('role')!='instance' or value.get('ready'):raise ValueError('Provision only a new EL VM instance')
    disk=Path(disk)
    if disk.is_symlink() or not disk.is_file() or disk.stat().st_uid!=os.geteuid() or disk.stat().st_mode&0o022:raise ValueError('Unsafe new guest disk')
    with tempfile.TemporaryDirectory(prefix='.el-vm-provision-',dir=disk.parent.parent) as temporary:
        work=Path(temporary)
        commands=[['run'],['mount','/dev/sda2','/'],['write','/etc/machine-id',uuid.uuid4().hex+'\n'],['write','/etc/hostname',value['name']+'\n'],['write','/etc/hosts','127.0.0.1 localhost\n::1 localhost\n127.0.1.1 '+value['name']+'\n']]
        for kind in ('rsa','ecdsa','ed25519','dsa'):
            for suffix in ('','.pub'):
                commands.append(['rm-f','/etc/ssh/ssh_host_'+kind+'_key'+suffix])
        commands += [['touch','/.autorelabel'],['checksum','sha256','/etc/selinux/config'],['cat','/etc/selinux/config'],['sync'],['umount-all'],['shutdown']]
        configuration=guestfish(disk,commands,work);digest,separator,text=configuration.partition('\n')
        if not separator or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Cannot verify the copied guest SELinux configuration')
        policy=parse_selinux_config(text)
        checked=guestfish(disk,[['run'],['mount','/dev/sda2','/'],*relabel_commands(policy),['checksum','sha256','/etc/selinux/config'],['sync'],['umount-all'],['shutdown']],work)
        if checked!=digest:raise ValueError('Guest SELinux mode/type changed during provisioning')


def build_template(selected,ram,credentials,previous=None,replace=False):
    import mykos_vm as vm
    from mykos_image_build import report,archive_existing
    if previous and not previous.get('installation_complete'):raise ValueError('Inspect incomplete EL VM disk before rebuilding')
    def interrupted(*_):raise KeyboardInterrupt('EL VM build interrupted; cleaning temporary disk assembly resources')
    handlers={}
    if __import__('threading').current_thread() is __import__('threading').main_thread():
        for signum in (signal.SIGTERM,signal.SIGHUP):handlers[signum]=signal.signal(signum,interrupted)
    try:
        vm.preflight(selected,ram)
        report(selected,'building','Building an independent EL VM disk with signed vendor packages')
        if replace:archive_existing(selected)
        if previous and previous.get('pending_commit'):
            result=vm.prepare(selected,keep_running=True,embed_disk=True)
            report(selected,'ready','EL VM image commit resumed successfully');return result
        if previous:vm.resume(selected)
        else:
            vm.safe_dir(vm.storage_root())
            with tempfile.TemporaryDirectory(prefix='.el-vm-builder-',dir=vm.storage_root()) as temporary:
                disk=Path(temporary)/'data.qcow2';receipt=build_disk(selected,disk,credentials)
                vm.create(selected,ram_mib=ram,prepared_disk={'path':str(disk),'receipt':receipt})
        value=vm.read(selected);wait_ready(value)
        vm.stop_container(value['name']);vm.run([vm.PODMAN,'rm',value['name']]);vm.launch(selected,value);wait_ready(value)
        report(selected,'capturing','Capturing EL guest RAM, devices and matching virtual disk')
        result=vm.prepare(selected,keep_running=True,embed_disk=True)
        report(selected,'ready','EL VM image ready; new instances use independent disks');return result
    except BaseException:
        report(selected,'failed','EL VM build incomplete; inspect the owned disk and diagnostics before retrying')
        raise
    finally:
        for signum,handler in handlers.items():signal.signal(signum,handler)
