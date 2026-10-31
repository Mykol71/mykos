"""Repair cloned external file mappings in a private CRIU 3.x restore copy."""
import os
from pathlib import Path
import struct
from mykos_criu import fields,integer,replace_fields

MAGIC=bytes.fromhex('1943565428395655')


def external_files(state):
    sources={m['Destination']:m['Source'] for m in state.get('Mounts',[]) if m.get('Type')=='bind' and m.get('Source','').startswith('/')}
    # Podman recreates these regular runtime files while starting a stopped
    # container. Their source files need not exist yet during restore preparation.
    regular={b'/run/.containerenv'}
    for key,target in [('ResolvConfPath','/etc/resolv.conf'),('HostsPath','/etc/hosts'),('HostnamePath','/etc/hostname')]:
        if state.get(key) and os.path.isabs(state[key]):regular.add(os.fsencode(target))
    regular.update(os.fsencode(target) for target,source in sources.items() if Path(source).is_file() and not Path(source).is_symlink())
    return regular


def normalize_files(directory,regular):
    images=[];external={};changed=0
    for path in directory.glob('mountpoints-*.img'):
        if path.is_symlink() or not path.is_file() or path.stat().st_size>64*1024*1024:
            raise ValueError('Unsafe CRIU mount image')
        data=path.read_bytes()
        if data[:8]!=MAGIC:raise ValueError('Unsupported CRIU mount image format')
        records=[];offset=8
        while offset<len(data):
            if len(data)-offset<4:raise ValueError('Truncated CRIU mount image')
            size=struct.unpack_from('<I',data,offset)[0];offset+=4
            if not 0<size<=min(len(data)-offset,2*1024*1024):raise ValueError('Invalid CRIU mount record')
            record=data[offset:offset+size];offset+=size;entry=fields(record);records.append((record,entry))
            if entry.get(7) in regular and entry.get(18):
                identity=(entry.get(3),entry.get(6),entry.get(8))
                key=(entry[7],identity)
                if key in external and external[key]!=entry[18]:raise ValueError('Conflicting external mount mapping')
                external[key]=entry[18]
        images.append((path,data[:8],records))
    updates={}
    for path,header,records in images:
        output=bytearray(header);modified=False
        for record,entry in records:
            key=external.get((entry.get(7),(entry.get(3),entry.get(6),entry.get(8))))
            if key and not entry.get(18):
                if entry.get(11) or entry.get(13):raise ValueError('Unsupported external slave or legacy mapping')
                if 15 in entry:record=replace_fields(record,{15:1})
                else:record+=integer(15<<3)+b'\x01'
                record+=integer((18<<3)|2)+integer(len(key))+key
                changed+=1;modified=True
            output+=struct.pack('<I',len(record))+record
        if modified:updates[path]=output
    for path,data in updates.items():path.write_bytes(data)
    return changed
