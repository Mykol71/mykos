"""Merge trusted, separately packaged business extensions into a live install.

Core source/RPM builders explicitly disable this loader. Descriptors and payloads
are root-owned local files; they never contain customer databases or host state.
"""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import stat

IDENTIFIER=re.compile(r'[a-z][a-z0-9_-]{0,31}\Z')
DIGEST=re.compile(r'[a-f0-9]{64}\Z')
PREFIXES=('/usr/local/share/mykos/','/usr/local/sbin/','/usr/local/bin/','/opt/mykos-web/')
DENIED=('/opt/mykos-web/data/','/opt/mykos-web/.venv/')
LIMIT=16*1024*1024


def _read(path,owner,limit=LIMIT):
    if path.is_symlink():raise ValueError('Extension source must not be a symlink')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=owner or info.st_mode&0o022 or info.st_size>limit:
        raise ValueError('Unsafe extension source ownership, mode or size')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        current=os.fstat(stream.fileno())
        if (current.st_dev,current.st_ino)!=(info.st_dev,info.st_ino):raise ValueError('Extension source changed while reading')
        data=stream.read(limit+1)
    if len(data)>limit:raise ValueError('Extension source exceeded its limit')
    return data


def _directory(path,owner):
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=owner or info.st_mode&0o022:
        raise ValueError('Unsafe extension directory ownership or mode')


def merge(root,entries,allowed,live=False):
    """Return verified runtime entries and provenance; never execute a hook."""
    root=Path(root);registry=root/'usr/share/mykos-extensions'
    if not registry.exists() and not registry.is_symlink():return entries,[]
    owner=0 if live else os.geteuid();_directory(registry,owner)
    result={name:dict(value) for name,value in entries.items()};touched=set();provenance=[];total=0
    for folder in sorted(registry.iterdir()):
        if not folder.is_dir() and not folder.is_symlink():continue
        if not IDENTIFIER.fullmatch(folder.name):raise ValueError('Invalid extension identifier')
        _directory(folder,owner)
        raw=_read(folder/'manifest.json',owner,4*1024*1024);manifest=json.loads(raw)
        if set(manifest)!={'format','id','version','files'} or manifest['format']!=1 or manifest['id']!=folder.name:
            raise ValueError('Invalid extension manifest')
        if not isinstance(manifest['version'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}',manifest['version']):
            raise ValueError('Invalid extension version')
        if not isinstance(manifest['files'],list) or len(manifest['files'])>10000:raise ValueError('Invalid extension file list')
        for entry in manifest['files']:
            if not isinstance(entry,dict) or set(entry)!={'path','source','sha256','base_sha256','mode','owner','mutable'}:
                raise ValueError('Invalid extension file declaration')
            name=entry['path'];relative=entry['source']
            if not isinstance(name,str) or not name.startswith(PREFIXES) or any(name==p.rstrip('/') or name.startswith(p) for p in DENIED) or not allowed(name):
                raise ValueError('Unmanaged extension destination')
            if '..' in PurePosixPath(name).parts or any(p.startswith('.') for p in PurePosixPath(name).parts):
                raise ValueError('Private state is not an extension destination')
            if name in touched:raise ValueError('Extensions must not overlap')
            if not isinstance(relative,str) or not relative.startswith('payload/') or any(p in ('','..','.') or p.startswith('.') for p in PurePosixPath(relative).parts):
                raise ValueError('Invalid extension payload path')
            source=folder/relative
            for parent in source.parents:
                if parent==folder:break
                _directory(parent,owner)
            digest=entry['sha256'];base=entry['base_sha256'];mode=entry['mode'];file_owner=entry['owner']
            if not isinstance(digest,str) or not DIGEST.fullmatch(digest) or (base is not None and (not isinstance(base,str) or not DIGEST.fullmatch(base))):raise ValueError('Invalid extension checksum')
            if type(mode) is not int or not 0<mode<=0o777 or mode&0o022 or file_owner not in ('root','web-read') or type(entry['mutable']) is not bool:
                raise ValueError('Invalid extension file permissions')
            previous=entries.get(name)
            if previous is not None:
                if previous['mutable'] or base!=hashlib.sha256(previous['data']).hexdigest():raise ValueError('Extension does not match this core build')
                if mode!=previous['mode'] or file_owner!=previous['owner'] or entry['mutable']:raise ValueError('Extension may not weaken core file permissions')
            elif base is not None:raise ValueError('Extension expects a missing core file')
            if entry['mutable'] and not (name.startswith('/usr/local/share/mykos/pos/ks/') and name.endswith('.ks')):
                raise ValueError('Only extension image recipes may be mutable')
            data=_read(source,owner);total+=len(data)
            if total>128*1024*1024:raise ValueError('Extension payload is too large')
            if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Extension payload checksum mismatch')
            result[name]={'data':data,'mode':mode,'owner':file_owner,'mutable':entry['mutable'],'extension':folder.name};touched.add(name)
        provenance.append({'id':folder.name,'version':manifest['version'],'manifest_sha256':hashlib.sha256(raw).hexdigest(),'files':len(manifest['files'])})
    return result,provenance
