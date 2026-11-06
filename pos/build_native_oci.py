#!/usr/bin/env python3
"""Build minimal native Linux guests from official OCI images.

Vendor package signatures remain enabled. No guest kernel, default password,
host identity, application backup or private repository content is embedded.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

BASES={'debian13':'docker.io/library/debian:13','archlinux':'docker.io/library/archlinux:base','sles15sp7':'registry.suse.com/bci/bci-init:15.7','amazon2023':'public.ecr.aws/amazonlinux/amazonlinux:2023','alpine':'docker.io/library/alpine:3.24'}
VERSIONS={'debian13':'13','archlinux':'rolling','sles15sp7':'15.7','amazon2023':'2023','alpine':'3.24'}
VENDORS={'debian13':'Debian','archlinux':'Arch Linux','sles15sp7':'SUSE Linux Enterprise','amazon2023':'Amazon Linux','alpine':'Alpine Linux'}

def dockerfile(os_name,base):
    repository=BASES.get(os_name,'').rsplit(':',1)[0]
    if os_name not in BASES or not re.fullmatch(re.escape(repository)+r'@sha256:[0-9a-f]{64}',base):
        raise ValueError('Select an official supported native image')
    debian=os_name=='debian13'
    install=("export DEBIAN_FRONTEND=noninteractive; apt-get update; apt-get install -y --no-install-recommends systemd systemd-sysv dbus openssh-server bash coreutils grep sed util-linux iproute2 iputils-ping procps python3 ca-certificates tar gzip passwd libreswan iptables; apt-get clean; rm -rf /var/lib/apt/lists/*" if debian else
             "pacman -Syu --noconfirm --needed systemd systemd-sysvcompat dbus openssh bash coreutils grep sed util-linux iproute2 iputils procps-ng python ca-certificates tar gzip shadow; pacman -Scc --noconfirm")
    if os_name=='sles15sp7':
        install='zypper --non-interactive refresh; zypper --non-interactive install --no-recommends systemd dbus-1 openssh bash coreutils grep sed util-linux iproute2 iputils procps python3 ca-certificates tar gzip shadow; zypper clean --all'
    if os_name=='amazon2023':
        install='dnf -y --setopt=install_weak_deps=False install systemd dbus openssh-server bash coreutils-single grep sed util-linux iproute iputils procps-ng python3 ca-certificates tar gzip passwd shadow-utils; dnf clean all'
    if os_name=='alpine':
        install='apk add --no-cache openrc openrc-init openssh openssh-server-common-openrc bash coreutils grep sed util-linux iproute2 iputils procps python3 ca-certificates tar gzip shadow'
    # This checkpoint profile mirrors the existing native images. Keep vendor
    # syscall/capability policy while avoiding unsupported BPF descriptors.
    setup=r'''#!/bin/bash
set -euo pipefail
passwd -l root
mkdir -p /etc/NetworkManager/conf.d /etc/ssh /etc/ipsec.d /var/lib/mykos /var/lib/dbus
rm -f /etc/ssh/ssh_host_* /var/lib/systemd/random-seed
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id
systemctl mask systemd-logind.service getty.target console-getty.service sys-fs-fuse-connections.mount systemd-remount-fs.service dev-hugepages.mount sys-kernel-config.mount systemd-udevd.service systemd-udevd-control.socket systemd-udevd-kernel.socket systemd-udev-trigger.service
systemctl disable systemd-networkd.service systemd-networkd.socket systemd-resolved.service 2>/dev/null || true
for service in systemd-journald dbus dbus-broker systemd-userdbd systemd-hostnamed systemd-localed systemd-timedated logrotate; do
 if [ -f "/usr/lib/systemd/system/$service.service" ] || [ -f "/lib/systemd/system/$service.service" ]; then
  mkdir -p "/etc/systemd/system/$service.service.d"
  printf '%s\n' '[Service]' 'PrivateDevices=no' 'DevicePolicy=auto' 'DeviceAllow=' 'IPAccounting=no' 'IPAddressAllow=' 'IPAddressDeny=' 'ProtectHostname=no' 'LoadCredential=' 'LoadCredentialEncrypted=' 'ImportCredential=' > "/etc/systemd/system/$service.service.d/90-mykos-checkpoint.conf"
 fi
done
cat > /etc/systemd/system/mykos-ssh-hostkeys.service <<'UNIT'
[Unit]
Description=Generate unique SSH host keys for this MYKOS guest
Before=ssh.service sshd.service
[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
UNIT
systemctl enable mykos-ssh-hostkeys.service
if [ -f /usr/lib/systemd/system/ssh.service ] || [ -f /lib/systemd/system/ssh.service ]; then
 systemctl enable ssh.service
else
 systemctl enable sshd.service
fi
date -u +%Y%m%d_%H%M > /etc/BUILDTIME
'''
    if os_name=='alpine':
        setup='''#!/bin/sh
set -eu
passwd -l root
mkdir -p /etc/NetworkManager/conf.d /etc/ipsec.d /var/lib/mykos
rm -f /etc/ssh/ssh_host_*
printf '%s\\n' 'rc_sys="docker"' 'rc_tty_number=0' 'rc_interactive="NO"' >> /etc/rc.conf
rc-update add sshd default
date -u +%Y%m%d_%H%M > /etc/BUILDTIME
'''
    if os_name in ('debian13','sles15sp7','amazon2023'):
        # UTC-native profile: systemd watches /etc rather than an overlay
        # localtime inode whose device identity is inconsistent on this kernel.
        # Keep all vendor zoneinfo data; no host file or extra mount is needed.
        setup+='\nrm -f /etc/localtime\n'
    return ('FROM '+base+'\nENV container=podman TZ=UTC\nRUN set -eu; '+install+'\n'
            'COPY setup.sh /tmp/mykos-native-setup.sh\nRUN bash /tmp/mykos-native-setup.sh && rm /tmp/mykos-native-setup.sh\n'
            'LABEL io.mykos.os="'+os_name+'" io.mykos.os.version="'+VERSIONS[os_name]+'" '
            'org.opencontainers.image.vendor="'+VENDORS[os_name]+'" '
            'io.mykos.init="'+('openrc' if os_name=='alpine' else 'systemd')+'" '
            'io.mykos.base.reference="'+base+'"\nSTOPSIGNAL '+('SIGTERM' if os_name=='alpine' else 'SIGRTMIN+3')+'\nCMD ["'+('/sbin/openrc-init' if os_name=='alpine' else '/sbin/init')+'"]\n'),setup

def run(args,timeout=1800):
    result=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError((result.stderr or result.stdout)[-5000:])
    return result.stdout.strip()

def build(os_name,tag=None):
    if os.geteuid()!=0:raise ValueError('Root is required')
    if os_name not in BASES:raise ValueError('Select a supported native OS')
    tag=tag or 'localhost/'+os_name+':latest'
    if not re.fullmatch(r'localhost/[a-z0-9][a-z0-9._/-]*:[a-z0-9._-]+',tag):raise ValueError('Use a local image tag')
    if subprocess.run(['podman','image','exists',tag],capture_output=True).returncode==0:
        raise ValueError('Image already exists; remove an unreferenced image explicitly before rebuilding')
    reference=BASES[os_name]
    existed=subprocess.run(['podman','image','exists',reference],capture_output=True).returncode==0
    try:
        # Preserve an existing operator cache/tag exactly. A fresh tag is pulled
        # only when absent; every build pins its selected digest in provenance.
        if not existed:run(['podman','pull','--arch','amd64','--tls-verify=true',reference])
        image=json.loads(run(['podman','image','inspect',reference],timeout=30))[0]
        digest=image['Digest']
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):raise ValueError('Official image digest unavailable')
        base=reference.rsplit(':',1)[0]+'@'+digest
        recipe,setup=dockerfile(os_name,base)
        with tempfile.TemporaryDirectory(prefix='mykos-native-oci-',dir='/var/tmp') as temp:
            folder=Path(temp);(folder/'Dockerfile').write_text(recipe);(folder/'setup.sh').write_text(setup)
            run(['podman','build','--pull=never','--layers=false','--rm=true','--force-rm=true','--memory=2g','--cpu-shares=64','--tag',tag,str(folder)])
        result=json.loads(run(['podman','image','inspect',tag],timeout=30))[0]
        labels=result.get('Labels') or result.get('Config',{}).get('Labels') or {}
        if labels.get('io.mykos.os')!=os_name or labels.get('io.mykos.base.reference')!=base:raise RuntimeError('Built image identity mismatch')
        return {'os':os_name,'image':tag,'image_id':result['Id'],'base_reference':base,'built_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'package_signature_policy':'vendor defaults retained','root_password':'locked','host_networking':'Podman owned','VPN':'Debian includes Libreswan; Arch requires separately qualified VPN dependencies'}
    finally:
        # Drop only the new upstream tag, never a pre-existing cache or a shared
        # image by ID. Finished image layers remain referenced by the result.
        if not existed and subprocess.run(['podman','image','exists',reference],capture_output=True).returncode==0:
            run(['podman','untag',reference],timeout=30)

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Usage: build_native_oci.py '+'|'.join(BASES))
    try:print(json.dumps(build(sys.argv[1])))
    except (ValueError,RuntimeError,OSError) as error:raise SystemExit(str(error))
