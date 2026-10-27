# Oracle Linux 9: derived from the operator-maintained rh9.ks.
# Basic setup information
# Accounts are locked at build time; provision host/container access explicitly.
rootpw --lock
keyboard us
timezone --utc UTC
timesource --ntp-disable
selinux --permissive
firewall --disabled
network  --bootproto=dhcp --device=link --ipv6=no --activate --onboot=on
network  --hostname=SHOPCODE.HOSTNAME
bootloader --disable
lang en_US
shutdown
eula --agreed
skipx

#users

# Disk setup
zerombr
clearpart --all --initlabel
part / --asprimary --fstype="ext4" --size=10000

# Repos
url --url=https://yum.oracle.com/repo/OracleLinux/OL9/baseos/latest/x86_64/
repo --name="AppStream" --install --baseurl=https://yum.oracle.com/repo/OracleLinux/OL9/appstream/x86_64/
repo --name="BaseOS" --install --baseurl=https://yum.oracle.com/repo/OracleLinux/OL9/baseos/latest/x86_64/

# Package setup
%packages
@^minimal-environment
@system-tools
bash
ksh
net-tools
wget
procmail
bind-utils
rsyslog
sed
rsync
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

# The source RedHat-specific OpenSCAP datastream is not applied to another vendor.
%post

echo 'container' > /etc/dnf/vars/infra

#Generate installtime file record
/bin/date +%Y%m%d_%H%M > /etc/BUILDTIME

# Limit languages to help reduce size.
LANG="en_US:UTF-8"
echo "%_install_langs $LANG" > /etc/rpm/macros.image-language-conf

# systemd fixes
systemd-tmpfiles --create --boot
# mask mounts and login bits
systemctl mask systemd-logind.service getty.target console-getty.service sys-fs-fuse-connections.mount systemd-remount-fs.service dev-hugepages.mount

# Remove things we don't need
rm -f /etc/udev/hwdb.bin
rm -rf /usr/lib/udev/hwdb.d/
rm -rf /boot
rm -rf /var/lib/dnf/history.*
ln -s /usr/bin/sh /bin/sh
usermod -G wheel -a tfsupport
%end
