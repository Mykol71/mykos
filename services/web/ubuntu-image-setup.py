#!/usr/bin/env python3
"""Run inside an installed Ubuntu VM before capturing a MYKOS image."""
import fcntl
import os
from pathlib import Path
import stat
import subprocess

SIZE = 12 * 1024**3
TARGET = Path('/var/lib/mykos-image/swapfile')
KNOWN = {'/swap.img', '/swapfile', str(TARGET)}


def command(args):
    return subprocess.check_output(args, text=True).strip()


def swap_rows(text):
    result = []
    for line in text.splitlines():
        name, size = line.rsplit(None, 1)
        if name not in KNOWN:
            raise ValueError('Unrecognized active swap; review it manually before setup: ' + name)
        result.append((name, int(size)))
    return result


def fstab_text(text, target):
    lines = []
    for line in text.splitlines():
        fields = line.split()
        if fields and not line.lstrip().startswith('#') and fields[0] in KNOWN and len(fields) > 2 and fields[2] == 'swap':
            continue
        lines.append(line)
    lines.append(target + ' none swap sw 0 0')
    return '\n'.join(lines) + '\n'


def regular_root_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
        raise ValueError('Expected a single-link regular root-owned file: ' + str(path))


def setup(ram_mib=2048):
    if type(ram_mib) is not int or not 2048 <= ram_mib <= 4096:
        raise ValueError("Supply the assigned VM RAM in MiB (2048–4096)")
    size_bytes = SIZE
    if os.geteuid() != 0:
        raise ValueError('Run with sudo inside Ubuntu')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'ubuntu' or command(['systemd-detect-virt', '--vm']) not in ('kvm', 'qemu'):
        raise ValueError('This setup is for the installed Ubuntu VM, not the MYKOS host or wrapper')
    if command(['findmnt', '-n', '-o', 'FSTYPE', '/']) not in ('ext4', 'xfs'):
        raise ValueError('Boot the installed Ubuntu system on ext4/XFS first; live installer and RAM-backed filesystems are not supported')
    folder = TARGET.parent
    for parent in (Path('/var'), Path('/var/lib'), folder):
        if parent.is_symlink():
            raise ValueError('Symlink setup directory rejected')
    folder.mkdir(mode=0o700, exist_ok=True)
    info = folder.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Setup directory must be root-owned and not writable by other users')
    lock = folder/'setup.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        current = swap_rows(command(['swapon', '--show', '--bytes', '--noheadings', '--raw', '--output', 'NAME,SIZE']))
        # Reuse an existing suitable swap file without allocating another one.
        target = next((Path(name) for name, size in current if size_bytes - 4096 <= size <= size_bytes), TARGET)
        fstab = Path('/etc/fstab')
        regular_root_file(fstab)
        old = fstab.read_text()
        if target == TARGET and not TARGET.exists():
            space = os.statvfs(folder)
            if space.f_bavail * space.f_frsize < SIZE + 1024**3:
                raise ValueError('Need 12 GiB free disk space plus 1 GiB headroom before creating swap')
            command(['dd', 'if=/dev/zero', 'of=' + str(TARGET), 'bs=1M', 'count=' + str(SIZE // 1024**2), 'status=none', 'conv=excl'])
            TARGET.chmod(0o600)
            command(['mkswap', str(TARGET)])
        regular_root_file(target)
        if target.stat().st_size != size_bytes:
            raise ValueError('Existing MYKOS swap file has unexpected size; left intact')
        target.chmod(0o600)
        if str(target) not in dict(current):
            command(['swapon', str(target)])
        # The new swap is active before replacing an older installer swap file.
        # Keep old files on disk for recovery; only their swap use is disabled.
        for name, _ in current:
            if name != str(target):
                regular_root_file(Path(name))
                command(['swapoff', name])
        updated = fstab_text(old, str(target))
        if updated != old:
            backup = folder/'fstab.before-mykos-swap'
            if not backup.exists():
                fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'w') as out:
                    out.write(old)
            # Preserve fstab's inode, owner and permissions.
            fstab.write_text(updated)
        print(command(['swapon', '--show']))
        print('Ubuntu image swap configured: 12 GiB. Reboot once to verify, then capture the image.')


def setup_console():
    if os.geteuid() != 0:
        raise ValueError('Run with sudo inside the installed Ubuntu VM')
    release = Path('/etc/os-release').read_text()
    if 'ID=ubuntu' not in release and 'ID="ubuntu"' not in release:
        raise ValueError('Ubuntu guest required')
    if command(['systemd-detect-virt', '--vm']) not in ('kvm', 'qemu') or command(['findmnt','-n','-o','FSTYPE','/']) not in ('ext4','xfs'):
        raise ValueError('Boot the installed Ubuntu VM before configuring its serial console')
    if not Path('/dev/ttyS0').exists(): raise ValueError('Ubuntu serial device ttyS0 is missing')
    path = Path('/etc/systemd/system/mykos-root-console.service')
    if path.is_symlink(): raise ValueError('Unsafe console service path')
    text = """[Unit]
Description=MYKOS direct guest root console
After=systemd-user-sessions.service
Conflicts=serial-getty@ttyS0.service
[Service]
ExecStart=/bin/su - root
Restart=always
RestartSec=1
StandardInput=tty
StandardOutput=tty
StandardError=tty
TTYPath=/dev/ttyS0
TTYReset=yes
TTYVHangup=yes
[Install]
WantedBy=multi-user.target
"""
    if path.exists() and path.read_text() != text:
        raise ValueError('Existing custom console unit was left unchanged')
    path.write_text(text); path.chmod(0o644)
    command(['systemctl','disable','--now','serial-getty@ttyS0.service'])
    command(['systemctl','daemon-reload'])
    command(['systemctl','enable','--now','mykos-root-console.service'])
    print('Direct serial root console enabled; access is through the MYKOS admin terminal.')


def setup_vpn():
    if os.geteuid()!=0: raise ValueError('Run with sudo inside Ubuntu')
    release=Path('/etc/os-release').read_text()
    if 'ID=ubuntu' not in release and 'ID="ubuntu"' not in release: raise ValueError('Ubuntu guest required')
    if command(['systemd-detect-virt','--vm']) not in ('kvm','qemu') or command(['findmnt','-n','-o','FSTYPE','/']) not in ('ext4','xfs'):
        raise ValueError('Boot the installed Ubuntu VM before configuring VPN support')
    subprocess.run(['apt-get','update'],check=True)
    subprocess.run(['apt-get','install','-y','libreswan','qemu-guest-agent'],check=True)
    command(['systemctl','enable','ipsec'])
    subprocess.run(['systemctl','start','qemu-guest-agent'],check=False)
    print('Ubuntu Libreswan installed. The MYKOS guest-agent channel must be enabled before using VPN actions.')


if __name__ == '__main__':
    try:
        import argparse
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--ram-mib', type=int, default=2048, help='Assigned VM RAM; Current image RAM policy is 2048 MiB; swap policy is 12 GiB')
        parser.add_argument('--console-only',action='store_true',help='Configure direct serial su - root without changing swap')
        parser.add_argument('--vpn-only',action='store_true',help='Install Libreswan and QEMU Guest Agent without changing swap or console')
        args=parser.parse_args()
        if args.vpn_only: setup_vpn()
        else:
            if not args.console_only: setup(args.ram_mib)
            setup_console()
            if not args.console_only: setup_vpn()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
