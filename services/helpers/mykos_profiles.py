"""Explicit application adapters. Generic virtualization selects none by default."""
from pathlib import Path
import mykos_runtime as runtime


def adapter(settings=None):
    value = (runtime.config() if settings is None else settings).get('application_adapter', 'none')
    if value not in ('none', 'rti'):
        raise ValueError('Unsupported application_adapter')
    return value


def has_backup(shop, settings=None):
    from mykos_backup_restore import available
    return available(shop,runtime.config() if settings is None else settings)


def mounts(shop, settings=None):
    from mykos_backup_restore import mount_arguments
    return mount_arguments(shop,runtime.config() if settings is None else settings)


def services():
    return ('tfremote', 'smb', 'cups', 'rti') if adapter() == 'rti' else ()


def backup_type(path):
    if adapter() != 'rti':
        return 'rsync'
    return 'RTI' if (path/'usr2').is_dir() else 'Daisy' if (path/'d').is_dir() else 'rsync'
