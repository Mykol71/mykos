#!/usr/bin/env python
"""Isolated EL7 yum worker for signed legacy guest root filesystems.

Invoked only inside the bounded build container. Host credentials are read-only,
never copied into the guest. All install repositories are explicitly selected.
"""
from __future__ import print_function
import glob,hashlib,json,os,re,shutil,stat,subprocess,sys
try:import ConfigParser as configparser
except ImportError:import configparser

def run(args):
 code=subprocess.call(args)
 if code:raise RuntimeError('Legacy command failed: '+str(args[0])+' (exit '+str(code)+')')
def configuration(name,root):
 catalog=json.load(open('/input/legacy_el.json'));spec=catalog[name];major=spec['major']
 if os.path.exists(root):raise ValueError('Guest root already exists')
 os.mkdir(root,0o755);os.makedirs(root+'/var/lib/rpm');os.makedirs(root+'/etc/yum.repos.d')
 conf=configparser.RawConfigParser();conf.add_section('main')
 for key,value in [('reposdir','/tmp/empty-repos'),('plugins','0'),('gpgcheck','1'),('sslverify','1'),('sslcacert','/input/build-ca.crt'),('keepcache','1' if major==6 else '0'),('cachedir','/tmp/legacy-yum-cache'),('persistdir','/tmp/legacy-yum-state'),('exactarch','1'),('multilib_policy','best'),('throttle','20M'),('max_connections','1'),('exclude','kernel* grub* '+('dracut* ' if major==6 else '')+'linux-firmware NetworkManager*')]:conf.set('main',key,value)
 os.mkdir('/tmp/empty-repos');run(['rpm','--root',root,'--initdb'])
 keys=[]
 for key in spec['keys']:
  path='/input/legacy-keys/'+key['sha256']+'.asc'
  if hashlib.sha256(open(path,'rb').read()).hexdigest()!=key['sha256']:raise ValueError('Pinned signing key mismatch')
  run(['rpm','--root',root,'--import',path]);keys.append(path)
 credentials={}
 if spec['distribution']=='rhel':
  pairs=[(p,p[:-4]+'-key.pem') for p in sorted(glob.glob('/run/mykos-entitlement/*.pem')) if not p.endswith('-key.pem') and os.path.isfile(p[:-4]+'-key.pem')]
  for cert,key in pairs:
   probe=subprocess.call(['curl','--silent','--fail','--location','--proto','=https','--proto-redir','=https','--max-time','30','--cert',cert,'--key',key,'--cacert','/run/mykos-rhsm-ca/redhat-uep.pem','--output','/dev/null',spec['repositories'][0]['url']+'/repodata/repomd.xml'],stdout=open(os.devnull,'w'),stderr=open(os.devnull,'w'))
   if probe==0:
    credentials={'sslclientcert':cert,'sslclientkey':key,'sslcacert':'/run/mykos-rhsm-ca/redhat-uep.pem'};break
  if not credentials:raise ValueError('RHEL guest requires a valid local repository entitlement')
 for repo in spec['repositories']:
  section=repo['name'];conf.add_section(section)
  for key,value in credentials.items():conf.set(section,key,value)
  for key,value in [('name',section),('baseurl',repo['url']),('gpgcheck','1'),('enabled','1'),('sslverify','1'),('gpgkey',' '.join('file://'+p for p in keys))]:conf.set(section,key,value)
 with open('/tmp/legacy-yum.conf','w') as f:conf.write(f)
 return spec,keys

def build(name):
 root='/stage/rootfs';spec,keys=configuration(name,root);major=spec['major']
 packages=[spec['release_package'],'bash','coreutils','grep','sed','shadow-utils','passwd','util-linux-ng' if major==6 else 'util-linux','openssh-server','ca-certificates','curl','tar','gzip','python','yum','iproute','procps' if major==6 else 'procps-ng','upstart' if major==6 else 'systemd','initscripts' if major==6 else 'systemd-sysv']
 run(['yum','-y','--config=/tmp/legacy-yum.conf','--installroot='+root,'--releasever='+spec['version'],'install']+packages)
 run(['rpm','--root',root,'-q','--whatprovides']+packages)
 identity=subprocess.check_output(['chroot',root,'/bin/sh','-c','cat /etc/redhat-release /etc/system-release /etc/os-release 2>/dev/null || :'])
 if not re.search(r'(?<![0-9])'+re.escape(spec['version'])+r'(?![0-9])',identity.decode('utf-8','replace')):raise ValueError('Installed guest version does not match selected final release')
 # Installer cache/config stays outside the target and is never an image layer.
 with open('/stage/packages.txt','w') as f:subprocess.check_call(['rpm','--root',root,'-qa','--qf','%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n'],stdout=f)
 for folder in ('proc','sys','dev','run','tmp','etc/ssh','var/lib/mykos','var/lib/dbus','etc/NetworkManager/conf.d'): 
  if not os.path.isdir(root+'/'+folder):os.makedirs(root+'/'+folder)
 # Lock root rather than shipping a password. Per-instance credentials remain an operator action.
 run(['chroot',root,'passwd','-l','root'])
 for p in glob.glob(root+'/etc/ssh/ssh_host_*'):os.unlink(p)
 for p in ('etc/machine-id','var/lib/dbus/machine-id'):
  path=root+'/'+p
  if os.path.lexists(path):os.unlink(path)
  with open(path,'w'):pass
 # Keep signature checks enabled in the guest, with only vendor repositories.
 for p in glob.glob(root+'/etc/yum.repos.d/*.repo'):os.unlink(p)
 guest=configparser.RawConfigParser()
 for repo in spec['repositories']:
  section=repo['name'];guest.add_section(section)
  for key,value in [('name',section+' (legacy)'),('baseurl',repo.get('guest_url',repo['url'])),('gpgcheck','1'),('enabled','1'),('sslverify','1'),('gpgkey',' '.join('file:///etc/pki/rpm-gpg/mykos-'+str(i)+'.asc' for i in range(len(keys))))]:guest.set(section,key,value)
 dest=root+'/etc/pki/rpm-gpg'
 if not os.path.isdir(dest):os.makedirs(dest)
 for i,p in enumerate(keys):shutil.copyfile(p,dest+'/mykos-'+str(i)+'.asc')
 with open(root+'/etc/yum.repos.d/mykos-legacy.repo','w') as f:guest.write(f)
 if major==6:
  # Podman owns devices/networking. Retain vendor Upstart as PID1, removing only
  # hardware/host boot jobs from its active directory. Operators can add jobs.
  inactive=root+'/usr/share/mykos/legacy-init.disabled';os.makedirs(inactive)
  for p in glob.glob(root+'/etc/init/*.conf'):shutil.move(p,inactive+'/'+os.path.basename(p))
  with open(root+'/etc/init/mykos-setup.conf','w') as f:f.write('start on startup\ntask\nscript\n mkdir -p /var/run/sshd /var/lock/subsys\n test -s /etc/ssh/ssh_host_rsa_key || ssh-keygen -q -t rsa -b 2048 -f /etc/ssh/ssh_host_rsa_key -N ""\n test ! -x /etc/rc.d/rc.local || /etc/rc.d/rc.local\nend script\n')
  with open(root+'/etc/init/mykos-sshd.conf','w') as f:f.write('start on stopped mykos-setup\nrespawn\nexec /usr/sbin/sshd -D\n')
  with open(root+'/etc/init/mykos-stop.conf','w') as f:f.write('start on power-status-changed\ntask\nscript\n test -f /run/.containerenv || exit 1\n /sbin/halt -f\nend script\n')
 else:
  run(['chroot',root,'systemctl','mask','systemd-logind.service','getty.target','console-getty.service','systemd-udevd.service','systemd-udevd-control.socket','systemd-udevd-kernel.socket','systemd-udev-trigger.service','dev-hugepages.mount','sys-kernel-config.mount','sys-fs-fuse-connections.mount','systemd-remount-fs.service','systemd-modules-load.service','systemd-sysctl.service','sys-kernel-debug.mount'])
  run(['chroot',root,'systemctl','enable','sshd.service'])
  with open(root+'/etc/machine-id','w'):pass
 # Build an EL6-native rpmdb from the same vendor-verified cached RPMs.
 # EL7's stored header/index format is incompatible with the EL6 RPM reader.
 if major==6:
  rpms=sorted(glob.glob(root+'/tmp/legacy-yum-cache/*/*/packages/*.rpm')+glob.glob(root+'/tmp/legacy-yum-cache/*/packages/*.rpm'))
  rpms=sorted(set(rpms))
  if not rpms:raise ValueError('Verified package cache is missing for native EL6 rpmdb')
  shutil.rmtree(root+'/var/lib/rpm');os.mkdir(root+'/var/lib/rpm')
  # NSS needs entropy devices while validating signatures in this chroot.
  # These worker-local nodes are removed before exporting the rootfs.
  devices=[]
  for name,minor in [('null',3),('zero',5),('random',8),('urandom',9)]:
   path=root+'/dev/'+name
   if os.path.exists(path) and os.path.isfile(path) and os.path.getsize(path)==0:os.unlink(path)
   if not os.path.exists(path):os.mknod(path,stat.S_IFCHR|0o666,os.makedev(1,minor));devices.append(path)
  run(['chroot',root,'rpm','--initdb'])
  for i in range(len(keys)):run(['chroot',root,'rpm','--import','/etc/pki/rpm-gpg/mykos-'+str(i)+'.asc'])
  run(['chroot',root,'rpm','--justdb','--noscripts','--notriggers','--nodeps','-i']+[p[len(root):] for p in rpms])
  run(['chroot',root,'rpm','-q']+packages)
  native=subprocess.check_output(['chroot',root,'rpm','-qa','--qf','%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n']).decode().splitlines()
  if sorted(native)!=sorted(open('/stage/packages.txt').read().splitlines()):raise ValueError('Native EL6 rpmdb package inventory mismatch')
  for path in devices:os.unlink(path)
 for path in (root+'/var/cache/yum',root+'/tmp/legacy-yum-cache',root+'/tmp/legacy-yum-state'):
  if os.path.isdir(path):shutil.rmtree(path)
 with open(root+'/var/lib/mykos/legacy-build.json','w') as f:json.dump({'os':name,'version':spec['version'],'legacy':True,'init':'upstart' if major==6 else 'systemd','kernel':'host shared; not the historical vendor kernel','package_signatures':'required; pinned vendor keys','repository_urls':[r['url'] for r in spec['repositories']],'signing_key_sha256':[k['sha256'] for k in spec['keys']]},f,sort_keys=True)
 run(['tar','--numeric-owner','--xattrs','--acls','-C',root,'-cf','/stage/rootfs.tar','.'])
 print('LEGACY_ROOTFS_COMPLETE '+name)
def mirror(name):
 spec,keys=configuration(name,'/tmp/legacy-keyroot')
 config=configparser.RawConfigParser();config.read('/tmp/legacy-yum.conf');config.remove_option('main','exclude')
 with open('/tmp/legacy-yum.conf','w') as handle:config.write(handle)
 # yum-utils is obtained from the signed RHEL7 worker repositories; guest
 # packages still come exclusively from the explicitly selected vendor trees.
 run(['yum','-y','--disablerepo=*','--enablerepo=ubi-7*','install','yum-utils'])
 for key in keys:run(['rpm','--import',key])
 import yum
 import xml.etree.ElementTree as ET
 for selected in spec['repositories']:
  repo_name=selected['name']
  run(['reposync','--config=/tmp/legacy-yum.conf','--gpgcheck','--download-metadata','--repoid='+repo_name,'--download_path=/stage'])
  base=yum.YumBase();base.doConfigSetup(fn='/tmp/legacy-yum.conf',init_plugins=False);base.repos.disableRepo('*');base.repos.enableRepo(repo_name);base.doRepoSetup()
  repo=base.repos.getRepo(repo_name);manifest=repo.repoXML;source=os.path.join(repo.cachedir,'repomd.xml');target='/stage/'+repo_name
  for item in ET.parse(source).getroot().findall('{http://linux.duke.edu/metadata/repo}data'):
   href=item.find('{http://linux.duke.edu/metadata/repo}location').get('href');dest=os.path.normpath(target+'/'+href)
   if not dest.startswith(target+'/repodata/'):raise ValueError('Unsafe upstream metadata location')
   cached=repo.retrieveMD(item.get('type'))
   if not os.path.isdir(os.path.dirname(dest)):os.makedirs(os.path.dirname(dest))
   shutil.copyfile(cached,dest)
  shutil.copyfile(source,target+'/repodata/repomd.xml')
if __name__=='__main__':
 if len(sys.argv)==3 and sys.argv[2]=='sync':mirror(sys.argv[1])
 elif len(sys.argv)==2:build(sys.argv[1])
 else:raise SystemExit('Usage: legacy_worker.py OS [sync]')
