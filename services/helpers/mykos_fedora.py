"""Fedora GNOME VMs: official cloud media and private first-boot configuration."""
import json
import re
import subprocess
import urllib.request


def cloud_image(version):
    if version not in ('43','44'):raise ValueError('Unsupported Fedora release')
    base='https://dl.fedoraproject.org/pub/fedora/linux/releases/'+version+'/Cloud/x86_64/images/'
    with urllib.request.urlopen(base,timeout=30) as response:
        index=response.read(2*1024*1024).decode('utf-8')
    names=set(re.findall(r'href="(Fedora-Cloud-Base-Generic-'+version+r'-[0-9.]+[.]x86_64[.]qcow2)"',index))
    if len(names)!=1:raise ValueError('Expected one official Fedora Generic cloud disk')
    return base+names.pop()


def userdata(values):
    username=values['username'];password=values['password']
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,19}',username) or username in ('root','guest','administrator'):
        raise ValueError('Invalid desktop account')
    if not isinstance(password,str) or not 12<=len(password)<=100 or any(ord(c)<32 for c in password):
        raise ValueError('Invalid desktop password')
    hashed=subprocess.run(['/usr/bin/openssl','passwd','-6','-stdin'],input=password+'\n',text=True,capture_output=True,check=True,timeout=10).stdout.strip()
    if not re.fullmatch(r'\$6\$[^\s]+',hashed):raise ValueError('Desktop password hashing failed')
    setup=r'''#!/bin/bash
set -euo pipefail
mkdir -p /var/lib/mykos
trap 'touch /var/lib/mykos/image-install-failed' EXIT
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
 if [[ ! -f /swapfile ]]; then
  if [[ $(findmnt -n -o FSTYPE /) == btrfs ]]; then
   btrfs filesystem mkswapfile --size 12g /swapfile
  else
   fallocate -l 12G /swapfile;chmod 600 /swapfile;mkswap /swapfile
  fi
 fi
 swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
# Both desktop and serial consoles retain the normal guest login prompt.
systemctl enable --now qemu-guest-agent
systemctl enable --now serial-getty@ttyS0.service
systemctl enable gdm.service
systemctl set-default graphical.target
systemctl start --no-block graphical.target
systemctl daemon-reload
systemctl enable --now mykos-fedora-ready.service
touch /var/lib/mykos/image-install-complete
trap - EXIT
'''
    ready=r"""#!/bin/bash
set -euo pipefail
for attempt in $(seq 1 120); do
 if systemctl is-active --quiet gdm.service && pgrep -x gnome-shell >/dev/null; then
  # Expose only this non-secret readiness marker to Fedora's confined QGA.
  # Keep SELinux enforcing and retain all vendor service/process confinement.
  touch /run/mykos-fedora-ready
  chcon -t virt_qemu_ga_var_run_t /run/mykos-fedora-ready
  chmod 0644 /run/mykos-fedora-ready
  exit 0
 fi
 sleep 1
done
exit 1
"""
    unit="""[Unit]
Description=MYKOS Fedora graphical readiness
After=gdm.service
Wants=gdm.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/mykos-fedora-ready
TimeoutStartSec=130
RemainAfterExit=yes
[Install]
WantedBy=graphical.target
"""
    data={'users':[{'name':username,'groups':['wheel'],'shell':'/bin/bash','lock_passwd':False,'passwd':hashed}],
          'disable_root':True,'ssh_pwauth':False,'package_update':False,
          'packages':['gnome-shell','gnome-session','gnome-settings-daemon','gdm','gnome-console','nautilus',
                      'xorg-x11-server-Xwayland','xorg-x11-xauth','mesa-dri-drivers','qemu-guest-agent','btrfs-progs'],
          'write_files':[{'path':'/etc/dnf/libdnf5.conf.d/90-mykos.conf','permissions':'0644','content':'[main]\ninstall_weak_deps=False\n'},
                         {'path':'/usr/local/sbin/mykos-fedora-setup','permissions':'0700','content':setup},
                         {'path':'/usr/local/sbin/mykos-fedora-ready','permissions':'0700','content':ready},
                         {'path':'/etc/systemd/system/mykos-fedora-ready.service','permissions':'0644','content':unit}],
          'runcmd':[['bash','/usr/local/sbin/mykos-fedora-setup']]}
    return '#cloud-config\n'+json.dumps(data,indent=2)+'\n'
