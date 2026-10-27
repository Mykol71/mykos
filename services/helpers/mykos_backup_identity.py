"""Guest-only account reconciliation for direct customer-backup mounts.

Customer numeric IDs stay intact. Conflicting image accounts are relocated in
that instance's writable root before init starts. Backup files are never chowned.
"""
import errno
import os
from pathlib import Path
import re
import stat
import struct
import tempfile

NAME=re.compile(r'[A-Za-z_][A-Za-z0-9_.-]*[$]?\Z')
LIMIT=4*1024*1024


def table(text,columns,label):
    if len(text)>LIMIT or '\x00' in text:raise ValueError('Invalid '+label+' size')
    rows=[];seen=set()
    for line in text.splitlines():
        if not line or line.startswith('#'):continue
        fields=line.split(':')
        if len(fields)!=columns or not NAME.fullmatch(fields[0]) or fields[0] in seen:
            raise ValueError('Invalid or duplicate '+label+' account')
        seen.add(fields[0]);rows.append(fields)
    return rows


def number(text):
    if not text.isascii() or not text.isdigit() or not 0<=int(text)<2**31:
        raise ValueError('Invalid numeric account ID')
    return int(text)


def assignments(base,customer,column):
    incoming={r[0]:number(r[column]) for r in customer}
    reserved=set(incoming.values())|{number(r[column]) for r in base}|{0,65534}
    target={};mapping={}
    for row in customer:
        if incoming[row[0]]==0 and row[0]!='root':
            raise ValueError('Backup contains an additional root identity; review it before restoring')
    if 'root' in incoming and incoming['root']!=0:raise ValueError('Backup root ID must remain zero')
    for row in base:
        name=row[0];old=number(row[column])
        if name=='root':wanted=0
        elif name in incoming:wanted=incoming[name]
        elif old in incoming.values() and not (old==65534 and name in ('nobody','nfsnobody')):
            candidates=range(100,1000) if old<1000 else range(1000,60001)
            wanted=next((x for x in candidates if x not in reserved),None)
            if wanted is None:raise ValueError('No free guest account IDs remain')
            reserved.add(wanted)
        else:wanted=old
        if old in mapping and mapping[old]!=wanted:
            raise ValueError('Image account aliases require conflicting ownership mappings')
        mapping[old]=wanted;target[name]=wanted
    target.update(incoming)
    target['root']=0
    return target,{old:new for old,new in mapping.items() if old!=new}


def adjusted_customer(customer,overrides=None,exclude=()):
    """Explicit per-backup receiver-ID mappings; never infer application names."""
    values={name:table(customer.get(name,''),columns,'backup '+name) for name,columns in (('passwd',7),('group',4),('shadow',9),('gshadow',4))}
    overrides=overrides or {}
    if not isinstance(overrides,dict) or set(overrides)-{'users','groups'}:raise ValueError('Invalid backup identity overrides')
    excluded=set(exclude)
    for key,filename,column in (('users','passwd',2),('groups','group',2)):
        mapping=overrides.get(key,{})
        if not isinstance(mapping,dict):raise ValueError('Invalid backup identity mapping')
        by_name={row[0]:row for row in values[filename]}
        for name,wanted in mapping.items():
            if name=='root' or name not in by_name or type(wanted) is not int or not 1<=wanted<2**31:
                raise ValueError('Invalid or missing mapped backup identity')
            row=by_name[name];previous=number(row[column]);row[column]=str(wanted)
            if key=='groups':
                for user in values['passwd']:
                    if number(user[3])==previous:user[3]=str(wanted)
    for filename in ('passwd','shadow'):values[filename]=[r for r in values[filename] if r[0] not in excluded]
    primary={number(r[3]) for r in values['passwd']}
    removed_groups={r[0] for r in values['group'] if r[0] in excluded and number(r[2]) not in primary}
    values['group']=[r for r in values['group'] if r[0] not in removed_groups]
    values['gshadow']=[r for r in values['gshadow'] if r[0] not in removed_groups]
    for row in values['group']:row[3]=','.join(x for x in row[3].split(',') if x not in excluded)
    for row in values['gshadow']:
        for at in (2,3):row[at]=','.join(x for x in row[at].split(',') if x not in excluded)
    return {name:''.join(':'.join(r)+'\n' for r in rows) for name,rows in values.items()}


def merge(base,customer):
    users=table(base.get('passwd',''),7,'image passwd')
    groups=table(base.get('group',''),4,'image group')
    source_users=table(customer.get('passwd',''),7,'backup passwd')
    source_groups=table(customer.get('group',''),4,'backup group')
    if not users or not groups or not any(r[0]=='root' and r[2]=='0' and r[3]=='0' for r in users):
        raise ValueError('Image must contain a valid root account')
    for row in users+source_users:
        number(row[2]);number(row[3])
        if not row[5].startswith('/') or not row[6].startswith('/'):
            raise ValueError('Invalid account home or shell')
    for row in groups+source_groups:
        number(row[2])
        if any(not NAME.fullmatch(name) for name in row[3].split(',') if name):
            raise ValueError('Invalid supplementary group member')
    target_uids,uid_map=assignments(users,source_users,2)
    target_gids,gid_map=assignments(groups,source_groups,2)
    source_by_name={r[0]:r for r in source_users};source_by_group={r[0]:r for r in source_groups}
    merged_users=[]
    for row in users:
        name=row[0];value=list(row);value[2]=str(target_uids[name])
        value[3]=str(number(source_by_name[name][3]) if name in source_by_name and name!='root' else gid_map.get(number(row[3]),number(row[3])))
        merged_users.append(value)
    old_users={r[0] for r in users}
    merged_users.extend(list(r) for r in source_users if r[0] not in old_users)
    merged_groups=[]
    for row in groups:
        name=row[0];value=list(row);value[2]=str(target_gids[name])
        members=list(dict.fromkeys([*filter(None,row[3].split(',')),*filter(None,source_by_group.get(name,['','','',''])[3].split(','))]))
        value[3]=','.join(members);merged_groups.append(value)
    old_groups={r[0] for r in groups}
    merged_groups.extend(list(r) for r in source_groups if r[0] not in old_groups)
    group_ids={number(r[2]) for r in merged_groups}
    if any(number(r[3]) not in group_ids for r in merged_users):
        raise ValueError('Backup account references a missing primary group')
    all_names={r[0] for r in merged_users}
    for row in merged_groups:
        row[3]=','.join(name for name in row[3].split(',') if name in all_names)
    old_shadow={r[0]:r for r in table(base.get('shadow',''),9,'image shadow')}
    new_shadow={r[0]:r for r in table(customer.get('shadow',''),9,'backup shadow')}
    shadow=[];locked=[]
    for row in merged_users:
        name=row[0]
        fields=old_shadow.get(name) if name=='root' else new_shadow.get(name,old_shadow.get(name))
        if fields is None:
            fields=[name,'!','','','','','','',''];locked.append(name)
        shadow.append(fields)
    old_gshadow={r[0]:r for r in table(base.get('gshadow',''),4,'image gshadow')}
    new_gshadow={r[0]:r for r in table(customer.get('gshadow',''),4,'backup gshadow')}
    gshadow=[]
    for row in merged_groups:
        fields=list(old_gshadow.get(row[0],[row[0],'!','','']) if row[0]=='root' else new_gshadow.get(row[0],old_gshadow.get(row[0],[row[0],'!','',''])))
        fields[3]=row[3];gshadow.append(fields)
    encoded=lambda rows:''.join(':'.join(row)+'\n' for row in rows)
    return {'files':{'passwd':encoded(merged_users),'group':encoded(merged_groups),'shadow':encoded(shadow),'gshadow':encoded(gshadow)},
            'uid_map':uid_map,'gid_map':gid_map,'summary':{'imported_users':len(all_names-old_users),'imported_groups':len({r[0] for r in merged_groups}-old_groups),
            'relocated_image_uids':len(uid_map),'relocated_image_gids':len(gid_map),'accounts_locked_without_shadow':locked}}


def remap_acl(value,uid_map,gid_map):
    if len(value)<4 or (len(value)-4)%8 or struct.unpack_from('<I',value)[0]!=2:
        raise ValueError('Unsupported guest POSIX ACL')
    result=bytearray(value)
    for at in range(4,len(value),8):
        tag,permissions,identifier=struct.unpack_from('<HHI',value,at)
        mapped=uid_map.get(identifier,identifier) if tag==2 else gid_map.get(identifier,identifier) if tag==8 else identifier
        struct.pack_into('<HHI',result,at,tag,permissions,mapped)
    return bytes(result)


def read_files(root):
    result={}
    rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        etc=os.open('etc',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd)
        try:
            for name in ('passwd','group','shadow','gshadow'):
                try:fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=etc)
                except FileNotFoundError:continue
                with os.fdopen(fd) as handle:
                    info=os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size>LIMIT:raise ValueError('Invalid identity file: '+name)
                    text=handle.read(LIMIT+1)
                    if len(text)>LIMIT:raise ValueError('Invalid identity file size')
                    result[name]=text
        finally:os.close(etc)
    finally:os.close(rootfd)
    return result


def apply(root,plan):
    """Call only on the created, stopped container's verified writable root."""
    root=Path(root)
    if not root.is_absolute() or root==Path('/') or root.is_symlink():raise ValueError('Invalid guest root')
    if (root/'etc').is_symlink() or not (root/'etc').is_dir():raise ValueError('Invalid guest identity directory')
    changes=[]
    # Collect original ownership first: mapping cycles and hard links must not
    # cause a target ID to be transformed twice during the subsequent updates.
    walk=os.walk(root,followlinks=False) if plan['uid_map'] or plan['gid_map'] else []
    for directory,dirs,files in walk:
        if Path(directory)==root:dirs[:]=[x for x in dirs if x not in ('proc','sys','dev')]
        for name in dirs+files:
            path=Path(directory)/name;info=path.lstat()
            uid=plan['uid_map'].get(info.st_uid,info.st_uid);gid=plan['gid_map'].get(info.st_gid,info.st_gid)
            ownership_changed=(uid,gid)!=(info.st_uid,info.st_gid)
            attributes={};acl_changed=False
            if not stat.S_ISLNK(info.st_mode):
                keys=('system.posix_acl_access','system.posix_acl_default')+ (('security.capability',) if ownership_changed else ())
                for key in keys:
                    try:
                        original=os.getxattr(path,key,follow_symlinks=False)
                        value=remap_acl(original,plan['uid_map'],plan['gid_map']) if key.startswith('system.posix_acl_') else original
                        attributes[key]=value;acl_changed|=value!=original
                    except OSError as error:
                        if error.errno not in (errno.ENODATA,errno.ENOTSUP,getattr(errno,'ENOATTR',errno.ENODATA)):raise
            if ownership_changed or acl_changed:changes.append((path,uid,gid,info.st_mode,attributes,ownership_changed))
    for path,uid,gid,mode,attributes,ownership_changed in changes:
        if ownership_changed:os.lchown(path,uid,gid)
        if not stat.S_ISLNK(mode):
            if ownership_changed:os.chmod(path,stat.S_IMODE(mode))
            for key,value in attributes.items():os.setxattr(path,key,value,follow_symlinks=False)
    for name,text in plan['files'].items():
        path=root/'etc'/name
        if path.is_symlink():raise ValueError('Unsafe guest identity destination')
        fd,temp=tempfile.mkstemp(prefix='.mykos-identity-',dir=str(path.parent))
        try:
            with os.fdopen(fd,'w') as handle:handle.write(text)
            os.chmod(temp,0o600 if name in ('shadow','gshadow') else 0o644)
            os.chown(temp,0,0)
            if path.exists():
                try:os.setxattr(temp,'security.selinux',os.getxattr(path,'security.selinux'))
                except OSError as error:
                    if error.errno not in (errno.ENODATA,errno.ENOTSUP,getattr(errno,'ENOATTR',errno.ENODATA)):raise
            os.replace(temp,path)
        finally:Path(temp).unlink(missing_ok=True)
    return plan['summary']
