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
repo --install --name="rtiAppStream" --baseurl=https://mykol.com/rhel-8-for-x86_64-appstream-rpms
repo --install --name="rtiBaseOS" --baseurl=https://mykol.com/rhel-8-for-x86_64-baseos-rpms
%packages --nocore --excludedocs --inst-langs=en --exclude-weakdeps
# Explicit runtime requirements; the solver adds only hard dependencies.
redhat-release
bash
coreutils
grep
sed
systemd
openssh-server
shadow-utils
util-linux
ca-certificates
curl
openssl
nmap-ncat
dnf
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
mkdir -p /etc/rpm /etc/dnf/vars /etc/NetworkManager/conf.d
if [ -f /etc/yum/pluginconf.d/subscription-manager.conf ]; then
  sed -i "s/enabled=1/enabled=0/g" /etc/yum/pluginconf.d/subscription-manager.conf
fi
echo 'container' > /etc/dnf/vars/infra
/bin/date +%Y%m%d_%H%M > /etc/BUILDTIME
LANG="en_US.UTF-8"
echo "%_install_langs $LANG" > /etc/rpm/macros.image-language-conf
systemd-tmpfiles --create --boot
systemctl mask systemd-logind.service getty.target console-getty.service sys-fs-fuse-connections.mount systemd-remount-fs.service dev-hugepages.mount sys-kernel-config.mount systemd-udevd.service systemd-udevd-control.socket systemd-udevd-kernel.socket systemd-udev-trigger.service

systemctl enable sshd
rm -rf /var/cache/yum /var/cache/dnf
: > /etc/machine-id
%end
