# Rocky Linux 10: derived from the operator-maintained rh10.ks.
# RHEL 10 container rootfs for livemedia-creator --make-tar.
# Build with a RHEL 10 Anaconda/Lorax environment, not the host's RHEL 8 installer.
# Basic setup information
# Accounts are locked at build time; provision host/container access explicitly.
rootpw --lock
keyboard us
timezone UTC --utc
timesource --ntp-disable
selinux --permissive
firewall --disabled
# Build networking is selected automatically; Podman supplies runtime eth0 and LAN DHCP.
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
url --url=https://dl.rockylinux.org/pub/rocky/10/BaseOS/x86_64/os/
repo --name="AppStream" --install --baseurl=https://dl.rockylinux.org/pub/rocky/10/AppStream/x86_64/os/
repo --name="BaseOS" --install --baseurl=https://dl.rockylinux.org/pub/rocky/10/BaseOS/x86_64/os/

# Package setup
%packages --nocore --exclude-weakdeps
@^minimal-environment
bash
net-tools
wget
bind-utils
telnet
rsyslog
sed
rsync
curl
-tuned
# Containers share the host kernel; do not install then erase kernel RPMs.
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

# RHEL 10 removed the installer OpenSCAP add-on and %anaconda section.
# Apply any required RHEL 10 security profile after building the image.
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
rm -f /var/lib/systemd/random-seed
# Give every instance its own machine ID on first boot.
: > /etc/machine-id
# /bin/sh is supplied by the distribution. Access accounts are provisioned later.

%end
