#!/usr/bin/env python3
"""Read a full native OS version from its rootfs archive without extracting it."""
import re
import shlex
import sys
import tarfile


def version(path):
    with tarfile.open(path, 'r|*') as archive:
        for member in archive:
            if member.name.lstrip('./') not in ('etc/os-release','usr/lib/os-release') or not member.isfile() or member.size > 65536:
                continue
            text = archive.extractfile(member).read().decode('utf-8')
            for line in text.splitlines():
                if line.startswith('VERSION_ID='):
                    value = shlex.split(line.split('=',1)[1])
                    if len(value)==1 and re.fullmatch(r'[0-9]+(?:\.[0-9]+)+',value[0]): return value[0]
    raise ValueError('Rootfs archive has no full OS VERSION_ID')


if __name__ == '__main__':
    if len(sys.argv)!=2: raise SystemExit('Usage: image_version.py ROOTFS.tar.xz')
    print(version(sys.argv[1]))
