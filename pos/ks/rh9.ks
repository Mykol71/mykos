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
%packages
@^minimal-environment
@system-tools
bash
ksh
expect
perl
net-tools
wget
procmail
bind-utils
telnet
rsyslog
sed
rsync
sendmail
kernel-modules
cups
samba
-tuned
%end

%addon org_fedora_oscap      
    content-type = scap-security-guide
    datastream-id = scap_org.open-scap_datastream_from_xccdf_ssg-rhel8-xccdf-1.2.xml
    xccdf-id = scap_org.open-scap_cref_ssg-rhel8-xccdf-1.2.xml
    profile = xccdf_org.ssgproject.content_profile_pci-dss
%end


%post

# container customizations inside the chroot
rpm -e kernel

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
rm /var/run/nologin
ln -s /usr/bin/sh /bin/sh
usermod -G wheel -a tfsupport
%end
