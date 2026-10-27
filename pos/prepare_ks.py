"""Keep source kickstarts current while adapting copies for older local builders."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


def modernize(text):
    text=text.replace('--isUtc','--utc')
    # Only remove the obsolete section, never a following %post block.
    text=re.sub(r'^%anaconda\b[^\n]*\n.*?^%end[^\n]*(?:\n|$)','',text,flags=re.M|re.S)
    lines=text.splitlines()
    disable=False
    for i,line in enumerate(lines):
        if line.startswith('timezone ') and '--nontp' in line:
            lines[i]=re.sub(r'\s+--nontp\b','',line);disable=True
    if disable and not any(re.match(r'^timesource\s+.*--ntp-disable\b',line) for line in lines):
        position=next(i for i,line in enumerate(lines) if line.startswith('timezone '))
        lines.insert(position+1,'timesource --ntp-disable')
    return '\n'.join(lines)+'\n'


def for_builder(text, timesource):
    text=modernize(text)
    if not timesource and re.search(r'^timesource\s+--ntp-disable\s*$',text,flags=re.M):
        # RHEL 8 Anaconda predates timesource; this spelling is valid there.
        text=re.sub(r'^timesource\s+--ntp-disable\s*\n','',text,flags=re.M)
        text=re.sub(r'^(timezone\s+[^\n]+)$',r'\1 --nontp',text,count=1,flags=re.M)
    return text


def supports_timesource():
    executable=shutil.which('livemedia-creator')
    if not executable: raise ValueError('Install livemedia-creator before preparing the build kickstart')
    with open(executable) as handle: shebang=handle.readline().strip()
    if not shebang.startswith('#!'): raise ValueError('Cannot determine the image builder Python interpreter')
    interpreter=shlex.split(shebang[2:])
    probe="from pykickstart.version import makeVersion; print(int('timesource' in makeVersion().commands))"
    result=subprocess.run(interpreter+['-c',probe],capture_output=True,text=True,timeout=15,check=True)
    if result.stdout.strip() not in ('0','1'): raise ValueError('Cannot determine image-builder timesource support')
    return result.stdout.strip()=='1'


def prepare(source,destination):
    source,destination=Path(source),Path(destination)
    if source.is_symlink() or destination.is_symlink(): raise ValueError('Symlink kickstart rejected')
    data=for_builder(source.read_text(),supports_timesource())
    fd,name=tempfile.mkstemp(prefix='.mykos-ks-',dir=destination.parent)
    try:
        with os.fdopen(fd,'w') as handle:handle.write(data)
        os.replace(name,destination)
    finally:
        if os.path.exists(name):os.unlink(name)

if __name__=='__main__':
    if len(sys.argv)!=3: raise SystemExit('Usage: prepare_ks.py SOURCE DESTINATION')
    prepare(sys.argv[1],sys.argv[2])
