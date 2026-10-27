"""Optional direct backup mounts and instance-only identity preparation."""
import ipaddress
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import uuid
import mykos_backup_identity as identity
from mykos_runtime import shopcode

BACKUPS=Path('/backups')
DATA_PATHS=('usr2','d','etc','home','usr/java')
PRIVATE_FILES=('passwd','group','shadow','gshadow','hostname','hosts','machine-id','fstab','crypttab','NetworkManager/conf.d/90-mykos-podman.conf')


def directory(shop,settings):
    shopcode(shop)
    root=Path(settings.get('backup_root') or BACKUPS)
    if not root.is_absolute() or root==Path('/'):raise ValueError('Invalid backup root')
    candidate=root/('tfrsync-'+shop)
    if candidate.is_symlink():raise ValueError('Backup account directory must not be a symlink')
    if not candidate.is_dir():return None
    if root.resolve() not in candidate.resolve().parents:raise ValueError('Backup escaped its configured root')
    return candidate


def source(backup,relative):
    path=backup/relative
    if not path.exists():
        if path.is_symlink():raise ValueError('Broken backup symlink: '+relative)
        return None
    resolved=path.resolve(strict=True)
    if backup.resolve() not in resolved.parents:raise ValueError('Backup path escapes the account directory: '+relative)
    if not (resolved.is_file() or resolved.is_dir()):raise ValueError('Unsupported backup path: '+relative)
    if any(x in str(resolved) for x in (',','\n','\r')):raise ValueError('Unsupported backup mount path')
    return resolved


def mount_pairs(shop,settings):
    backup=directory(shop,settings)
    if backup is None:return []
    pairs=[]
    for rel in DATA_PATHS:
        path=source(backup,rel)
        if path:pairs.append((path,'/'+rel))
    if len(pairs)>512:raise ValueError('Too many backup mounts')
    return pairs


def available(shop,settings):
    return bool(mount_pairs(shop,settings))


def mount_arguments(shop,settings):
    pairs=mount_pairs(shop,settings)
    if not pairs:raise ValueError('Customer backup set is not available')
    result=[]
    for path,destination in pairs:
        result+=['--mount','type=bind,source='+str(path)+',destination='+destination+',ro=false']
    return result


def private_mounts(vpn):
    """Persistent instance-only exceptions inside the complete /etc mount."""
    root=Path(vpn)/'.mykos-restore'
    root.mkdir(mode=0o700)
    result=[]
    try:
        for name in PRIVATE_FILES:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'');path.chmod(0o600 if name in ('shadow','gshadow') else 0o644)
            result+=['--mount','type=bind,source='+str(path)+',destination=/etc/'+name+',ro=false']
    except BaseException:
        shutil.rmtree(root);raise
    return root,result


def export_private(root,private):
    for name in PRIVATE_FILES:
        source=guest_path(root,'etc/'+name)
        destination=guest_path(private,name)
        if not source.is_file():raise ValueError('Missing prepared instance setting: '+name)
        destination.write_bytes(source.read_bytes())
        os.chown(destination,0,0);os.chmod(destination,0o600 if name in ('shadow','gshadow') else 0o644)


def guest_path(root,relative):
    parts=PurePosixPath(relative).parts
    if not parts or any(x in ('..','/') for x in parts):raise ValueError('Invalid guest-relative path')
    path=Path(root)
    if not path.is_absolute() or path==Path('/') or path.is_symlink():raise ValueError('Invalid guest root')
    for part in parts:
        path=path/part
        if path.is_symlink():raise ValueError('Guest path must not follow symlinks: '+relative)
    return path


def write_guest(root,relative,text,mode=0o644):
    path=guest_path(root,relative);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.mykos-restore-',dir=str(path.parent))
    try:
        with os.fdopen(fd,'w') as handle:handle.write(text)
        os.chmod(temp,mode);os.chown(temp,0,0);os.replace(temp,path)
    finally:Path(temp).unlink(missing_ok=True)


def prepare(shop,settings,root,hostname,address=None):
    backup=directory(shop,settings)
    if backup is None:raise ValueError('Customer backup set disappeared')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',hostname):raise ValueError('Invalid instance hostname')
    if address:address=str(ipaddress.IPv4Address(address))
    backup_account='tfrsync-'+shop
    customer=identity.read_files(backup);base=identity.read_files(root)
    homes={row[5] for table in (base,customer) for row in identity.table(table.get('passwd',''),7,'passwd') if row[0]==backup_account}
    overrides=settings.get('backup_identity_overrides',{})
    if not isinstance(overrides,dict):raise ValueError('Invalid backup identity configuration')
    adjusted=identity.adjusted_customer(customer,overrides.get(shop),exclude=[backup_account])
    image=identity.adjusted_customer(base,exclude=[backup_account])
    for home in homes:
        if home not in ('/home/'+backup_account,'/backups/'+backup_account):
            raise ValueError('Unsafe old backup-account home')
        guest_path(root,home.lstrip('/'))
    plan=identity.merge(image,adjusted)
    summary=identity.apply(root,plan)
    # These paths refer only to the created guest root, before backup bind mounts
    # are attached. No deletion or ownership operation ever targets the backup.
    for home in homes:
        if not home.startswith('/') or home in ('/','/home','/root','/usr2','/d','/etc'):
            raise ValueError('Unsafe old backup-account home')
        path=guest_path(root,home.lstrip('/'))
        if path.exists():
            if path.is_dir():shutil.rmtree(path)
            else:path.unlink()
    cron=guest_path(root,'var/spool/cron/'+backup_account)
    if cron.exists():cron.unlink()
    # Keep physical-host mounts out of this container's boot transaction.
    for name in ('fstab','crypttab'):
        path=guest_path(root,'etc/'+name)
        if not path.exists():write_guest(root,'etc/'+name,'')
    write_guest(root,'etc/machine-id',uuid.uuid4().hex+'\n')
    write_guest(root,'etc/hostname',hostname+'\n')
    text='127.0.0.1 localhost localhost.localdomain\n::1 localhost localhost.localdomain\n'
    hostfile=source(backup,'etc/hosts')
    if hostfile:
        if not hostfile.is_file() or hostfile.stat().st_size>1024*1024:raise ValueError('Invalid backup hosts file')
        for line in hostfile.read_text().splitlines():
            fields=line.split('#',1)[0].split()
            if not fields:continue
            try:old=ipaddress.ip_address(fields[0])
            except ValueError:continue
            if old.is_loopback or hostname in fields[1:] or hostname.split('.')[0] in fields[1:]:continue
            text+=line+'\n'
    text+=(address or '127.0.1.1')+' '+hostname+' '+hostname.split('.')[0]+'\n'
    write_guest(root,'etc/hosts',text)
    write_guest(root,'etc/NetworkManager/conf.d/90-mykos-podman.conf',
                '# Podman owns this native container interface.\n[device-mykos-podman]\nmatch-device=interface-name:eth0\nmanaged=0\n')
    return {**summary,'backup_account_removed':backup_account,'backup':str(backup),'mode':'direct-rw'}
