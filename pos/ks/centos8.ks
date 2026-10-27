# CentOS Stream 8: derived from the operator-maintained rh8.ks.
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
url --url=https://vault.centos.org/8-stream/BaseOS/x86_64/os/
repo --name="AppStream" --baseurl=https://vault.centos.org/8-stream/AppStream/x86_64/os/
repo --name="BaseOS" --baseurl=https://vault.centos.org/8-stream/BaseOS/x86_64/os/
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
rm -f /var/run/nologin
rf /var/lib/dnf/history.*
ln -s /usr/bin/sh /bin/sh
updatedb
/dev/loop b 7 0
usermod -G wheel -a tfsupport
# Keep this vendor's installed repository definitions and signing keys.

# Stream 8 is archived. Keep runtime package access on the official Vault too.
for repo_file in /etc/yum.repos.d/CentOS-*.repo; do
  [ ! -f "$repo_file" ] || sed -i 's/^enabled=1/enabled=0/' "$repo_file"
done
cat > /etc/yum.repos.d/MYKOS-CentOS-Stream-8.repo <<'REPOSITORIES'
[mykos-centos8-baseos]
name=CentOS Stream 8 archived BaseOS
baseurl=https://vault.centos.org/8-stream/BaseOS/x86_64/os/
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial

[mykos-centos8-appstream]
name=CentOS Stream 8 archived AppStream
baseurl=https://vault.centos.org/8-stream/AppStream/x86_64/os/
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-centosofficial
REPOSITORIES
%end
