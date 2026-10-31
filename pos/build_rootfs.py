"""Run native rootfs builds in a private installer mount and PID namespace."""
import argparse
import fcntl
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time

SUPPORTED=re.compile(r'(?:rh(?:7|8|9|10)|(?:rocky|alma|oracle|centos)(?:8|9|10)|fedora(?:43|44)|mssql(?:2022|2025))\Z')
EL8=re.compile(r'(?:rh|rocky|alma|oracle|centos)8\Z')
ISOLATION='''# Minimal guest roots omit SELinux policy. Prevent chroot account tools from
# probing the host policy view; kernel enforcement on the host stays enabled.
if [ -d /sys/fs/selinux ]; then
    mount -t tmpfs -o mode=0555,nosuid,nodev,noexec tmpfs /sys/fs/selinux
fi
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs /run
case "$2" in /run/*) install -D -m 0644 "$1/resolv.conf" "$2";; esac
mount --bind "$1/mnt" /mnt
mount --bind "$1/tmp" /tmp
if [ "$3" = el8 ]; then mount --bind "$1/os-release" "$4"; fi
shift 4
exec "$@"
'''


def rh8_platform(text):
    """Override only the installer environment's platform, retaining its identity."""
    if re.search(r'^PLATFORM_ID=',text,flags=re.M):
        return re.sub(r'^PLATFORM_ID=[^\n]*','PLATFORM_ID="platform:el8"',text,count=1,flags=re.M)
    return text.rstrip()+'\nPLATFORM_ID="platform:el8"\n'


def terminate(process):
    if process.poll() is not None:return
    os.killpg(process.pid,signal.SIGTERM)
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=10)


def watch(process,log,timeout):
    deadline=time.monotonic()+timeout
    try:
        while process.poll() is None:
            text=log.read_text(errors='replace') if log.exists() else ''
            fatal=next((line for line in text.splitlines() if 'Non interactive installation failed' in line or 'There was an error running the kickstart script' in line),None)
            if fatal:raise RuntimeError(fatal)
            if time.monotonic()>deadline:raise RuntimeError('Image installation timed out')
            time.sleep(1)
        return process.returncode
    finally:terminate(process)


def build(os_name,kickstart,check_platform=False,release_file=Path('/etc/os-release'),temporary_root='/var/tmp',timeout=7200,lock_root='/var/tmp'):
    if not SUPPORTED.fullmatch(os_name):raise ValueError('Unsupported native image OS')
    if os.geteuid()!=0:raise ValueError('Native image builds require root')
    kickstart=Path(kickstart)
    if kickstart.is_symlink() or not kickstart.is_file():raise ValueError('Missing or unsafe build kickstart')
    kickstart=kickstart.resolve(strict=True)
    executable=shutil.which('livemedia-creator')
    if not executable:raise ValueError('Install the image-builder dependencies first')
    unshare=shutil.which('unshare')
    if not unshare:raise ValueError('Native image builds require util-linux unshare')
    if check_platform and not EL8.fullmatch(os_name):raise ValueError('Platform diagnostic is only for EL8')
    release_file=Path(release_file).resolve(strict=True);original=release_file.read_text()
    # The lock is on the directory, avoiding symlinkable lock files. Builds use
    # unique roots; serializing also protects the shared final archive name.
    descriptor=os.open(lock_root,os.O_RDONLY|os.O_DIRECTORY)
    try:
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another MYKOS native image build is running')
        directory=Path(tempfile.mkdtemp(prefix='mykos-build-'+os_name+'-',dir=temporary_root));directory.chmod(0o700)
        for name in ('mnt','tmp'):(directory/name).mkdir(mode=0o700)
        (directory/'tmp').chmod(0o1777)
        (directory/'os-release').write_text(rh8_platform(original))
        resolver=Path('/etc/resolv.conf').resolve(strict=True);(directory/'resolv.conf').write_bytes(resolver.read_bytes())
        archive=os_name+'-docker.tar.xz';log=directory/'build.log'
        command=[executable,'--no-virt','--make-tar','--ks='+str(kickstart),'--image-name='+archive,
                 '--resultdir='+str(directory/'result'),'--logfile='+str(log)]
        if check_platform:
            interpreter=shlex.split(Path(executable).open().readline().strip()[2:])
            command=interpreter+['-c','from pyanaconda.core.util import get_os_release_value; '
                'value=get_os_release_value("PLATFORM_ID"); print("Anaconda build platform:",value); '
                'assert value=="platform:el8"']
        print('Installer diagnostics: '+str(directory),flush=True)
        process=subprocess.Popen([unshare,'--mount','--pid','--fork','--kill-child','--mount-proc','--propagation','private',
            '/bin/bash','-ceu',ISOLATION,'mykos-rootfs',str(directory),str(resolver),
            'el8' if EL8.fullmatch(os_name) else 'native',str(release_file),*command],start_new_session=True)
        code=watch(process,log,timeout)
        if release_file.read_text()!=original:raise RuntimeError('Host OS metadata changed during build')
        if code or check_platform:return code
        candidates=list((directory/'result').rglob(archive))
        if len(candidates)!=1 or candidates[0].is_symlink() or not candidates[0].is_file() or not candidates[0].stat().st_size:
            raise RuntimeError('Image creator did not produce exactly one rootfs archive; see '+str(directory))
        destination=Path(temporary_root)/archive
        if destination.is_symlink():raise ValueError('Unsafe image archive destination')
        os.replace(candidates[0],destination)
        # Successful roots contain a second unpacked image and private sockets.
        # Failed builds remain intact for diagnosis; namespace exit drops mounts.
        shutil.rmtree(directory/'mnt');shutil.rmtree(directory/'tmp')
        print('Rootfs archive: '+str(destination),flush=True)
        return 0
    finally:os.close(descriptor)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('os_name');parser.add_argument('kickstart')
    parser.add_argument('--check-platform',action='store_true')
    args=parser.parse_args()
    try:raise SystemExit(build(args.os_name,args.kickstart,args.check_platform))
    except (ValueError,OSError,RuntimeError,subprocess.SubprocessError) as error:parser.exit(1,str(error)+'\n')
