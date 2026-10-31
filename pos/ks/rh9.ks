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
url --url=https://mykol.com/rhel-9-for-x86_64-baseos-rpms
repo --name="AppStream" --install --baseurl=https://mykol.com/rhel-9-for-x86_64-appstream-rpms
repo --name="BaseOS" --install --baseurl=https://mykol.com/rhel-9-for-x86_64-baseos-rpms

# Package setup
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
iptables-nft
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

echo 'container' > /etc/dnf/vars/infra

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

# MYKOS checkpoint service profile: CRIU cannot save BPF program FDs.
# Preserve vendor syscall/capability limits; avoid BPF and nested UTS in these services.
checkpoint_services="systemd-journald"
if [ -f /usr/lib/systemd/system/dbus-broker.service ]; then
  checkpoint_services="$checkpoint_services dbus-broker"
elif [ -f /usr/lib/systemd/system/dbus.service ]; then
  checkpoint_services="$checkpoint_services dbus"
fi
for checkpoint_optional in systemd-userdbd systemd-hostnamed systemd-localed systemd-timedated logrotate; do
  if [ -f "/usr/lib/systemd/system/$checkpoint_optional.service" ]; then
    checkpoint_services="$checkpoint_services $checkpoint_optional"
  fi
done
for checkpoint_service in $checkpoint_services; do
  mkdir -p "/etc/systemd/system/$checkpoint_service.service.d"
  cat > "/etc/systemd/system/$checkpoint_service.service.d/90-mykos-checkpoint.conf" <<'MYKOS_CHECKPOINT'
[Service]
PrivateDevices=no
DevicePolicy=auto
DeviceAllow=
IPAccounting=no
IPAddressAllow=
IPAddressDeny=
ProtectHostname=no
MYKOS_CHECKPOINT
done
# End MYKOS checkpoint service profile.

systemctl enable sshd
rm -rf /var/cache/yum /var/cache/dnf
: > /etc/machine-id
%end
