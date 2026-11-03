# RHEL 8 RTI native image; keeps the standard rh8 recipe unchanged.
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
%packages --excludedocs --inst-langs=en --exclude-weakdeps
# RTI package profile requested by the operator; java is intentionally listed once.
@^minimal-environment
@system-tools
aide
audispd-plugins
libreswan
opensc
openscap
openscap-scanner
pcsc-lite
scap-security-guide
net-tools
java
perl
wget
mailx
procmail
bind-utils
ksh
expect
-firewalld
iptables
iptables-services
samba
cups
chrony
ntpstat
telnet
fetchmail
rsyslog
python2
rear
sed
lrzsz
ncurses-term
rsync
grub2-efi-x64-modules
grub2-tools-efi
sendmail
crypto-policies-scripts
mutt
NetworkManager
dos2unix
nodejs
usbutils
audit
# MYKOS native-container runtime and installed updateos prerequisites.
redhat-release
bash
coreutils
grep
systemd
openssh-server
shadow-utils
util-linux
ca-certificates
curl
openssl
nmap-ncat
dnf
# The host provides the kernel and hardware firmware, as for rh8.
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

# Keep the requested chrony tools, while the native container shares host time.
systemctl mask chronyd.service tuned.service
systemctl enable sshd
rm -rf /var/cache/yum /var/cache/dnf
: > /etc/machine-id
%end
