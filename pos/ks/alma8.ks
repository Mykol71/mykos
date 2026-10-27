# AlmaLinux 8: derived from the operator-maintained rh8.ks.
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
url --url=https://repo.almalinux.org/almalinux/8/BaseOS/x86_64/kickstart/
repo --name="AppStream" --baseurl=https://repo.almalinux.org/almalinux/8/AppStream/x86_64/kickstart/
repo --name="BaseOS" --baseurl=https://repo.almalinux.org/almalinux/8/BaseOS/x86_64/kickstart/
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
yum
rsync
kernel-modules
mlocate
iptables-services
-firewalld
-tuned
-kernel
-kernel-core
-kernel-modules
-kernel-modules-core
-kernel-modules-extra
# MYKOS native VPN, SSH and download tools from this vendor.
libreswan
openssh-server
curl
%end
%post
if [ -f /etc/yum/pluginconf.d/subscription-manager.conf ]; then
  sed -i "s/enabled=1/enabled=0/g" /etc/yum/pluginconf.d/subscription-manager.conf
fi
echo 'container' > /etc/dnf/vars/infra
/bin/date +%Y%m%d_%H%M > /etc/BUILDTIME
LANG="en_US:UTF-8"
echo "%_install_langs $LANG" > /etc/rpm/macros.image-language-conf
systemd-tmpfiles --create --boot
systemctl mask systemd-logind.service getty.target console-getty.service sys-fs-fuse-connections.mount systemd-remount-fs.service dev-hugepages.mount
rm -f /etc/udev/hwdb.bin
rm -rf /usr/lib/udev/hwdb.d/
rm -rf /boot
rf /var/lib/dnf/history.*
ln -s /usr/bin/sh /bin/sh
updatedb
/dev/loop b 7 0
usermod -G wheel -a tfsupport
# Keep this vendor's installed repository definitions and signing keys.
%end
