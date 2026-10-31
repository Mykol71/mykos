"""Select a root-managed checkpoint tool without replacing the vendor binary."""
import os
from pathlib import Path
import re
import shutil
import stat

VENDOR=('/usr/sbin/criu','/usr/bin/criu')
ROOT=Path('/opt/mykos-criu')


def checked(value,stat_reader=None):
    if not isinstance(value,str):raise ValueError('Invalid CRIU executable')
    if value in VENDOR:
        real=str(Path(value).resolve(strict=True))
        if real not in VENDOR:raise ValueError('Unexpected vendor CRIU executable')
        return real
    pattern=re.escape(str(ROOT))+r'/[0-9]+\.[0-9]+(?:\.[0-9]+)?/sbin/criu'
    if not re.fullmatch(pattern,value):raise ValueError('Select the vendor or a versioned MYKOS CRIU executable')
    reader=stat_reader or os.lstat;path=Path(value)
    for entry in (path,*path.parents):
        info=reader(entry)
        if info.st_uid!=0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError('CRIU executable and parent directories must be root-owned and not writable by other users')
        if entry==path:
            if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:raise ValueError('CRIU must be a regular executable')
        elif not stat.S_ISDIR(info.st_mode):raise ValueError('Invalid CRIU executable parent directory')
    return value


def binary(settings=None):
    if settings is None:
        import mykos_runtime as runtime
        settings=runtime.config() if runtime.CONFIG.exists() or runtime.CONFIG.is_symlink() else {}
    value=settings.get('criu_binary')
    if not value:
        packaged=ROOT/'4.2.1/sbin/criu'
        if packaged.exists() or packaged.is_symlink():return checked(str(packaged))
        discovered=shutil.which('criu')
        if not discovered:raise ValueError('Install CRIU to enable native Linux memory snapshots')
        value=str(Path(discovered).resolve(strict=True))
    return checked(value)
