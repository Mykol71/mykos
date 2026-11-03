"""Explicit application adapters. Generic virtualization selects none by default."""
from pathlib import Path
import mykos_runtime as runtime


def adapter(settings=None):
    value=(runtime.config() if settings is None else settings).get('application_adapter','none')
    if value!='none':raise ValueError('A business adapter requires its separately installed extension')
    return value


def has_backup(shop, settings=None):
    from mykos_backup_restore import available
    return available(shop,runtime.config() if settings is None else settings)


def mounts(shop, settings=None):
    from mykos_backup_restore import mount_arguments
    return mount_arguments(shop,runtime.config() if settings is None else settings)


def services():
    return ()


def backup_type(path):
    return 'rsync'
