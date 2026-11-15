"""Read-only observation of managed and terminal-launched VM builders."""
import os,stat,json
from pathlib import Path

def directory(os_name):
    import mykos_image_build as image
    image.selection(os_name)
    root=image.STATE;folder=root/os_name
    if root.is_symlink() or folder.is_symlink():raise ValueError('Unsafe image build directory')
    return folder

def progress(os_name):
    path=directory(os_name)/'status.json'
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd) as handle:
        st=os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077 or st.st_size>65536:raise ValueError('Unsafe image build status')
        value=json.load(handle)
    if not isinstance(value,dict) or value.get('os')!=os_name:raise ValueError('Invalid image build status')
    return value

def locked_in_kernel(text,device,inode):
    for row in text.splitlines():
        fields=row.split()
        if len(fields)<6 or fields[1]!='FLOCK' or fields[3]!='WRITE':continue
        try:
            major,minor,number=fields[5].split(':')
            key=(int(major,16),int(minor,16),int(number))
        except (ValueError,IndexError):continue
        if key==(os.major(device),os.minor(device),inode):return True
    return False

def worker_lock_held(os_name):
    # Do not acquire a probe lock: even a brief shared/exclusive probe can make
    # a real nonblocking builder start fail. Match the kernel's exact inode.
    try:
        path=directory(os_name)/'build.lock'
        try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except FileNotFoundError:return False
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o022:return None
            with Path('/proc/locks').open() as handle:text=handle.read(1048577)
            if len(text)>1048576:return None
            return locked_in_kernel(text,st.st_dev,st.st_ino)
        finally:os.close(fd)
    except (OSError,ValueError):return None
