# RHEL 7 container rootfs for livemedia-creator --make-tar.
# Use a matching RHEL 7 build environment for a full image-build validation.
# Basic setup information
# Accounts are locked at build time; provision host/container access explicitly.
rootpw --lock
url --url="https://mykol.com/rhel-7-server-rpms"
repo --name="Optional" --install --baseurl=https://mykol.com/rhel-7-server-optional-rpms
repo --name="Extras" --install --baseurl=https://mykol.com/rhel-7-server-extras-rpms
repo --name="Server" --install --baseurl=https://mykol.com/rhel-7-server-rpms
keyboard us
selinux --permissive
firewall --disabled
skipx
timezone  America/Chicago
timesource --ntp-disable
network --bootproto=dhcp --device=link --activate --onboot=on
shutdown
bootloader --disabled
lang en_US.UTF-8
firstboot --disable
#users

# Disk setup
zerombr
clearpart --all --initlabel
part / --asprimary --fstype="ext4" --size=10000


# Explicit packages: the site's RHEL 7 mirrors do not publish comps group metadata.
# --nocore prevents Anaconda from implicitly requesting the missing Core group.
%packages --nocore --excludedocs --inst-langs=en
# Explicit runtime requirements; the solver adds only hard dependencies.
redhat-release-server
bash
coreutils
grep
sed
systemd
systemd-sysv
openssh-server
shadow-utils
util-linux
ca-certificates
curl
openssl
nmap-ncat
yum
libreswan
iptables
# Host-managed networking, time, and performance need no guest daemons.
-firewalld
-tuned
-chrony
-NetworkManager
# Hardware and kernel support come from the container host.
-kernel
-kernel-core
-kernel-modules
-kernel-modules-core
-kernel-modules-extra
-linux-firmware
-*-firmware
-microcode_ctl
%end
%post --erroronfail --log=/root/mykos-image-post.log
set -eu
mkdir -p /etc/rpm /etc/yum/vars /etc/NetworkManager/conf.d

if [ -f /etc/yum/pluginconf.d/subscription-manager.conf ]; then
    sed -i 's/enabled=1/enabled=0/g' /etc/yum/pluginconf.d/subscription-manager.conf
fi

# container customizations inside the chroot
mkdir -p /etc/yum/vars /etc/rpm
echo 'container' > /etc/yum/vars/infra

#Generate installtime file record
/bin/date +%Y%m%d_%H%M > /etc/BUILDTIME

# Limit languages to help reduce size.
LANG="en_US.UTF-8"
echo "%_install_langs $LANG" > /etc/rpm/macros.image-language-conf

# systemd fixes
systemd-tmpfiles --create --boot
# mask mounts and login bits
systemctl mask systemd-logind.service getty.target console-getty.service sys-fs-fuse-connections.mount systemd-remount-fs.service dev-hugepages.mount sys-kernel-config.mount systemd-udevd.service systemd-udevd-control.socket systemd-udevd-kernel.socket systemd-udev-trigger.service

# Remove things we don't need
# Keep Yum's package history intact; this image does not use DNF.

# /bin/sh is supplied by the distribution; access accounts are provisioned later.
# A failed download or an invalid shell script must fail the image build.
updateos_tmp=$(mktemp /usr/bin/.updateos.XXXXXX)
trap 'rm -f "$updateos_tmp"' EXIT
curl --fail --show-error --location --retry 2 --connect-timeout 15 --max-time 120 \
    --output "$updateos_tmp" https://mykol.com/updateos/updateos
[ -s "$updateos_tmp" ]
bash -n "$updateos_tmp"
chmod 0755 "$updateos_tmp"
mv -f "$updateos_tmp" /usr/bin/updateos
trap - EXIT

systemctl enable sshd
rm -rf /var/cache/yum /var/cache/dnf
: > /etc/machine-id
%end
