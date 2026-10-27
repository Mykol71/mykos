keyboard us
# Accounts are locked at build time; provision host/container access explicitly.
rootpw --lock
timezone --utc CDT
timesource --ntp-disable
selinux --permissive
firewall --disabled
network  --bootproto=dhcp --device=link --ipv6=no --activate --onboot=on
bootloader --disable
lang en_US
eula --agreed
reboot
skipx
zerombr
clearpart --all --initlabel
part / --asprimary --fstype="ext4" --size=15000
url --url=https://mykol.com/rhel-8-for-x86_64-baseos-rpms
repo --name="rtiAppStream" --baseurl=https://mykol.com/rhel-8-for-x86_64-appstream-rpms
repo --name="rtiBaseOS" --baseurl=https://mykol.com/rhel-8-for-x86_64-baseos-rpms
%packages
@^minimal-environment
@system-tools
bash
ksh
net-tools
wget
mailx
procmail
bind-utils
telnet
rsyslog
sed
rsync
kernel-modules
mlocate
iptables-services
-firewalld
-tuned
%end
%post
sed -i "s/enabled=1/enabled=0/g" /etc/yum/pluginconf.d/subscription-manager.conf
echo 'container' > /etc/dnf/vars/infra
/bin/date +%Y%m%d_%H%M > /etc/BUILDTIME
LANG="en_US:UTF-8"
echo "%_install_langs $LANG" > /etc/rpm/macros.image-language-conf
systemd-tmpfiles --create --boot
systemctl mask systemd-logind.service getty.target console-getty.service sys-fs-fuse-connections.mount systemd-remount-fs.service dev-hugepages.mount
rm -f /etc/udev/hwdb.bin
rm -rf /usr/lib/udev/hwdb.d/
rm -rf /boot
rm -f /var/run/nologin
rf /var/lib/dnf/history.*
ln -s /usr/bin/sh /bin/sh
updatedb
/dev/loop b 7 0
usermod -G wheel -a tfsupport
rm -f /etc/yum.repos.d/*
%end
