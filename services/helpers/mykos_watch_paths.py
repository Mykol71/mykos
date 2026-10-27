"""Find native application inotify paths using host metadata, before CRIU freezes.

No guest commands, application-specific paths, symlink traversal, or file reads.
The root descriptor pins the instance filesystem throughout the bounded scan.
"""
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time

WATCH = re.compile(r'^inotify\s+.*?\bino:([0-9a-fA-F]+)\s+sdev:([0-9a-fA-F]+)',re.M)
EXCLUDED = {'proc','sys','dev','run'}


def device_number(value):
    major,minor=os.major(value),os.minor(value)
    return (minor & 255) | (major << 8) | ((minor & ~255) << 12)


def components(path):
    if not isinstance(path,str) or not path.startswith('/') or path.endswith(' (deleted)'):
        return None
    parts=PurePosixPath(path).parts[1:]
    if '..' in parts or (parts and parts[0] in EXCLUDED):return None
    return parts


def subdirectory(root,parts):
    fd=os.dup(root)
    try:
        for part in parts:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=child
        return fd
    except BaseException:
        os.close(fd);raise


def watch_paths(pid,proc_root=Path('/proc'),seconds=5,max_entries=100000):
    if type(pid) is not int or pid<=1:return []
    target=proc_root/str(pid)
    try:
        namespace=(target/'ns/pid').stat().st_ino
        root=os.open(target/'root',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    except OSError:return []
    deadline=time.monotonic()+seconds
    wanted=set();seeds=set();found={};visited=set();count=0
    try:
        # proc's top-level numeric entries are process leaders, not all threads.
        for process in proc_root.iterdir():
            if time.monotonic()>deadline:break
            if not process.name.isascii() or not process.name.isdigit():continue
            try:
                if (process/'ns/pid').stat().st_ino!=namespace:continue
                watches=set()
                for index,info in enumerate((process/'fdinfo').iterdir()):
                    if index>=8192 or len(wanted|watches)>=4096 or time.monotonic()>deadline:break
                    try:
                        with info.open() as handle:text=handle.read(262144)
                        watches.update((int(dev,16),int(ino,16)) for ino,dev in WATCH.findall(text))
                    except OSError:continue
                if not watches:continue
                wanted.update(watches)
                paths=[]
                try:paths.append(os.readlink(process/'cwd'))
                except OSError:pass
                for index,entry in enumerate((process/'fd').iterdir()):
                    if index>=8192 or time.monotonic()>deadline:break
                    try:paths.append(os.readlink(entry))
                    except OSError:continue
                for path in paths:
                    parts=components(path)
                    if parts:
                        seeds.add(parts[:-1]);seeds.add(parts)
            except OSError:continue
        if not wanted:return []
        wanted_inodes={ino for _,ino in wanted}

        def scan(seed):
            nonlocal count
            pending=[seed]
            while pending and count<max_entries and time.monotonic()<deadline and wanted-found.keys():
                parts=pending.pop()
                try:fd=subdirectory(root,parts)
                except OSError:continue
                try:
                    current=os.fstat(fd);identity=(device_number(current.st_dev),current.st_ino)
                    if identity in visited:continue
                    visited.add(identity)
                    if identity in wanted:found[identity]='/'+ '/'.join(parts)
                    with os.scandir(fd) as entries:
                        for entry in entries:
                            if count>=max_entries or time.monotonic()>deadline:break
                            count+=1
                            if not parts and entry.name in EXCLUDED:continue
                            child=parts+(entry.name,)
                            try:
                                # Directory entry inode lookup avoids stat'ing every
                                # file when scanning a large added application.
                                if entry.inode() in wanted_inodes:
                                    info=os.stat(entry.name,dir_fd=fd,follow_symlinks=False)
                                    identity=(device_number(info.st_dev),info.st_ino)
                                    if identity in wanted and not stat.S_ISLNK(info.st_mode):
                                        found[identity]='/'+'/'.join(child)
                                if entry.is_dir(follow_symlinks=False):pending.append(child)
                            except OSError:continue
                finally:os.close(fd)
        # Application working directories/open files prioritize small relevant
        # trees; root fallback covers watches outside those locations.
        for seed in sorted(seeds,key=lambda x:(len(x),x)):
            if seed:scan(seed)
        if wanted-found.keys():scan(())
        return sorted(set(found.values()))[:4096]
    finally:os.close(root)
