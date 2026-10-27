# Oracle Linux 10 native container rootfs for livemedia-creator --make-tar.
# Use an EL10 Anaconda/Lorax builder. Runtime networking comes from Podman.
# Basic setup information
# Accounts are locked at build time; provision host/container access explicitly.
rootpw --lock
keyboard us
timezone UTC --utc
timesource --ntp-disable
selinux --permissive
firewall --disabled
# Build networking is selected automatically; Podman supplies runtime eth0 and the configured private NAT network.
network  --bootproto=dhcp --device=link --noipv6 --activate --onboot=on
network  --hostname=SHOPCODE.HOSTNAME
bootloader --disabled
lang en_US.UTF-8
shutdown
eula --agreed
skipx

#users

# Disk setup
zerombr
clearpart --all --initlabel
part / --asprimary --fstype="ext4" --size=20000

# Repos
url --url=https://yum.oracle.com/repo/OracleLinux/OL10/baseos/latest/x86_64/
repo --name="AppStream" --install --baseurl=https://yum.oracle.com/repo/OracleLinux/OL10/appstream/x86_64/
repo --name="BaseOS" --install --baseurl=https://yum.oracle.com/repo/OracleLinux/OL10/baseos/latest/x86_64/

# Package setup
%packages --excludedocs
@^minimal-environment
bash
curl
openssh-server
iproute
procps-ng
util-linux
shadow-utils
findutils
tar
gzip
rsync
sed
net-tools
bind-utils
wget
ksh
expect
python3
libreswan
-tuned
-kernel
-kernel-uek
-kernel-uek-core
-kernel-uek-modules
-kernel-core
-kernel-modules
-kernel-modules-core
-kernel-modules-extra
%end

# Keep vendor release packages, signing keys and repository definitions.
%post --erroronfail --log=/root/mykos-image-post.log
set -eu

# container customizations inside the chroot
mkdir -p /etc/dnf/vars /etc/rpm
echo 'container' > /etc/dnf/vars/infra

#Generate installtime file record
/bin/date +%Y%m%d_%H%M > /etc/BUILDTIME

# Limit languages to help reduce size.
LANG="en_US.UTF-8"
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

# These paths may already be absent in a minimal image.
rm -f /var/run/nologin /var/lib/systemd/random-seed
# Give every instance its own machine ID on first boot.
: > /etc/machine-id
# /bin/sh is supplied by the distribution. Access accounts are provisioned later.

%end
