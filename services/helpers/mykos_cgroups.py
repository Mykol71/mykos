"""Native guest init/checkpoint compatibility without changing host settings."""
import json
import os
from pathlib import Path
import stat
import sys

from mykos_runtime import PODMAN, run, NATIVE_OS_NAMES

ROOT = Path('/run/mykos-legacy-cgroups')
UNIFIED = Path('/sys/fs/cgroup/cgroup.controllers')
UNIT = 'mykos-legacy-cgroups.service'
EL7_NAMES = ('rh7','oracle7','centos7','scientific7','springdale7')


def legacy_image(image):
    if not UNIFIED.is_file():return False
    if image in tuple(x for name in EL7_NAMES for x in (name,name+':latest','localhost/'+name+':latest')):
        legacy=True
    elif image in NATIVE_OS_NAMES:
        legacy=False
    else:
        value=json.loads(run([PODMAN,'image','inspect',image],timeout=20))[0]
        legacy=(value.get('Config',{}).get('Labels') or {}).get('io.mykos.os') in EL7_NAMES
    return legacy


def host_mount_arguments(image):
    # An external root bind would replace the saved private EL7 hierarchy
    # with the host cgroup2 root when CRIU reopens its external mounts.
    return [] if legacy_image(image) else ['--volume','/sys/fs/cgroup:/sys/fs/cgroup:ro']


def init_command(image):
    if image in ('alpine','alpine:latest','localhost/alpine:latest'):return ['/sbin/openrc-init']
    if image not in NATIVE_OS_NAMES:
        value=json.loads(run([PODMAN,'image','inspect',image],timeout=20))[0]
        if (value.get('Config',{}).get('Labels') or value.get('Labels') or {}).get('io.mykos.init')=='openrc':return ['/sbin/openrc-init']
    return ['/usr/bin/python','/usr/local/libexec/mykos-rh7-init'] if legacy_image(image) else ['/sbin/init']


def arguments(image):
    legacy=legacy_image(image)
    if not legacy:return []
    if not Path('/usr/bin/crun').is_file():raise ValueError('RH7 on cgroup v2 requires crun')
    # The host service owns the mount, including when a caller has PrivateMounts.
    # Enable it before launching containers so Podman's boot restart sees it too.
    run(['/usr/bin/systemctl','enable','--now',UNIT],timeout=40)
    wrapper=Path(__file__).with_name('mykos-rh7-init')
    if wrapper.is_symlink() or not wrapper.is_file():raise ValueError('Missing RH7 cgroup initialization helper')
    # Bind the named hierarchy explicitly: newer crun no longer materializes
    # it from force_cgroup_v1. PID 1 isolates its own branch before systemd.
    return ['--runtime=/usr/bin/crun','--systemd=always',
            '--mount','type=bind,source='+str(ROOT/'systemd')+',destination=/run/mykos-legacy-systemd,ro=true',
            '--mount','type=bind,source='+str(wrapper)+',destination=/usr/local/libexec/mykos-rh7-init,ro=true']


def prepare():
    if os.geteuid()!=0:raise ValueError('Root is required')
    if not UNIFIED.is_file():return
    for path in (ROOT,ROOT/'systemd'):
        if path.is_symlink():raise ValueError('Unsafe legacy cgroup mountpoint')
        path.mkdir(mode=0o755,exist_ok=True)
        info=path.stat()
        if info.st_uid!=0 or info.st_mode & 0o022 or not stat.S_ISDIR(info.st_mode):
            raise ValueError('Unsafe legacy cgroup mountpoint')
        if path==ROOT and not os.path.ismount(path):
            run(['/usr/bin/mount','-t','tmpfs','-o','mode=0755,size=4m','mykos-legacy-cgroups',str(path)],timeout=20)
    target=ROOT/'systemd'
    if not os.path.ismount(target):
        run(['/usr/bin/mount','-t','cgroup','-o','none,name=systemd','cgroup',str(target)],timeout=20)
    value=json.loads(run(['/usr/bin/findmnt','--json','--mountpoint',str(target),'--output','FSTYPE,OPTIONS'],timeout=10))['filesystems']
    if len(value)!=1 or value[0]['fstype']!='cgroup' or 'name=systemd' not in value[0]['options'].split(','):
        raise ValueError('Unexpected legacy cgroup hierarchy')


if __name__=='__main__':
    if sys.argv[1:]!=['--prepare']:raise SystemExit('Usage: mykos_cgroups.py --prepare')
    prepare()
