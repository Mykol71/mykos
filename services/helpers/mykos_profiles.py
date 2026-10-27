"""Explicit application adapters. Generic virtualization selects none by default."""
from pathlib import Path
import mykos_runtime as runtime


def adapter(settings=None):
    value = (runtime.config() if settings is None else settings).get('application_adapter', 'none')
    if value not in ('none', 'rti'):
        raise ValueError('Unsupported application_adapter')
    return value


def has_backup(shop, settings=None):
    return adapter(settings) == 'rti' and (Path('/backups/tfrsync-' + shop) / 'usr2').is_dir()


def mounts(shop):
    if not has_backup(shop):
        raise ValueError('Restore requires an explicitly configured application adapter and its backup')
    backup = Path('/backups/tfrsync-' + shop)
    result = []
    for rel in ('usr2','etc/cups','etc/samba','etc/mail','etc/hosts','etc/passwd','etc/passwd-',
                'etc/group','etc/group-','etc/shadow','etc/shadow-','etc/gshadow','etc/gshadow-',
                'etc/sudoers','usr/java','home'):
        source = backup / rel
        if source.exists():
            if backup.resolve() not in source.resolve().parents:
                raise ValueError('Backup mount escapes account directory: ' + rel)
            result += ['-v', str(source) + ':/' + rel + ':rw']
    return result


def services():
    return ('tfremote', 'smb', 'cups', 'rti') if adapter() == 'rti' else ()


def backup_type(path):
    if adapter() != 'rti':
        return 'rsync'
    return 'RTI' if (path/'usr2').is_dir() else 'Daisy' if (path/'d').is_dir() else 'rsync'
