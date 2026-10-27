# RHEL 7 container rootfs for livemedia-creator --make-tar.
# Use a matching RHEL 7 build environment for a full image-build validation.
# Basic setup information
# Accounts are locked at build time; provision host/container access explicitly.
rootpw --lock
url --url="https://mykol.com/rhel-7-server-rpms"
repo --name="Optional" --install --baseurl=https://mykol.com/rhel-7-optional-rpms
repo --name="Extras" --install --baseurl=https://mykol.com/rhel-7-extras-rpms
repo --name="Server" --install --baseurl=https://mykol.com/rhel-7-server-rpms
keyboard us
selinux --permissive
firewall --disabled
skipx
timezone  America/Chicago
network --bootproto=dhcp --device=link --activate --onboot=on
shutdown
bootloader --disabled
lang en_US.UTF-8
firstboot --disable
logging --level=info
auth  --useshadow --passalgo=sha512

#users

# Disk setup
zerombr
clearpart --all --initlabel
part / --asprimary --fstype="ext4" --size=10000

%addon org_fedora_oscap
content-type = scap-security-guide
profile = xccdf_org.ssgproject.content_profile_pci-dss
%end

# Explicit packages: the site's RHEL 7 mirrors do not publish comps group metadata.
# --nocore prevents Anaconda from implicitly requesting the missing Core group.
%packages --nocore --excludedocs --instLangs=en
filesystem
setup
basesystem
redhat-release-server
coreutils
util-linux
shadow-utils
sed
grep
gawk
findutils
diffutils
gzip
bzip2
xz
cpio
which
hostname
ca-certificates
glibc-common
dbus
bind-utils
bash
yum
vim-minimal
less
iputils
iproute
net-tools
nmap-ncat
rsync
systemd
systemd-sysv
initscripts
rootfiles
tar
passwd
yum-utils
yum-plugin-ovl
firewalld
telnet
mc
glibc
slang
curl
mtools
net-snmp
kbd
sysstat
procps-ng
e2fsprogs
uuid
libuuid
biosdevname
iptables-services
deltarpm
openssh-server
openssl
authconfig
-tuned
%end
%post --erroronfail --log=/anaconda-post.log
set -eu

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
systemctl mask systemd-logind.service getty.target console-getty.service sys-fs-fuse-connections.mount systemd-remount-fs.service dev-hugepages.mount

# Remove things we don't need
rm -f /etc/udev/hwdb.bin
rm -rf /usr/lib/udev/hwdb.d/
rm -rf /boot
# Keep Yum's package history intact; this image does not use DNF.
rm -f /var/run/nologin

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
%end
