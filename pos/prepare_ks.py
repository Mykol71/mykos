"""Keep source kickstarts current while adapting copies for older local builders."""
import os
import json
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


def for_builder(text, timesource, weakdeps="--exclude-weakdeps"):
    text=modernize(text)
    if not timesource and re.search(r'^timesource\s+--ntp-disable\s*$',text,flags=re.M):
        # RHEL 8 Anaconda predates timesource; this spelling is valid there.
        text=re.sub(r'^timesource\s+--ntp-disable\s*\n','',text,flags=re.M)
        text=re.sub(r'^(timezone\s+[^\n]+)$',r'\1 --nontp',text,count=1,flags=re.M)
    # Older pykickstart has the same option with a camel-case spelling.
    def packages(match):
        line=match.group(0)
        if re.search(r'(?<!\S)--(?:exclude-weakdeps|excludeWeakdeps)(?!\S)',line):
            if not weakdeps:
                raise ValueError('The installed kickstart parser cannot exclude weak dependencies')
            line=re.sub(r'(?<!\S)--(?:exclude-weakdeps|excludeWeakdeps)(?!\S)',weakdeps,line)
        return line
    return re.sub(r'^%packages\b[^\n]*',packages,text,flags=re.M)


def builder_capabilities():
    executable=shutil.which('livemedia-creator')
    if not executable: raise ValueError('Install livemedia-creator before preparing the build kickstart')
    with open(executable) as handle: shebang=handle.readline().strip()
    if not shebang.startswith('#!'): raise ValueError('Cannot determine the image builder Python interpreter')
    interpreter=shlex.split(shebang[2:])
    probe="""import json
from pykickstart.version import makeVersion
from pykickstart.parser import KickstartParser
from pykickstart.errors import KickstartError
weakdeps = None
for option in ('--exclude-weakdeps', '--excludeWeakdeps'):
    try:
        KickstartParser(makeVersion()).readKickstartFromString('%packages ' + option + '\\n%end\\n')
    except KickstartError:
        continue
    weakdeps = option
    break
print(json.dumps({'timesource': 'timesource' in makeVersion().commands, 'weakdeps': weakdeps}))
"""
    result=subprocess.run(interpreter+['-c',probe],capture_output=True,text=True,timeout=15,check=True)
    data=json.loads(result.stdout)
    if type(data.get('timesource')) is not bool or data.get('weakdeps') not in (None,'--exclude-weakdeps','--excludeWeakdeps'):
        raise ValueError('Cannot determine image-builder kickstart support')
    return data



def prepare(source,destination):
    source,destination=Path(source),Path(destination)
    if source.is_symlink() or destination.is_symlink(): raise ValueError('Symlink kickstart rejected')
    data=for_builder(source.read_text(),**builder_capabilities())
    fd,name=tempfile.mkstemp(prefix='.mykos-ks-',dir=destination.parent)
    try:
        with os.fdopen(fd,'w') as handle:handle.write(data)
        os.replace(name,destination)
    finally:
        if os.path.exists(name):os.unlink(name)

if __name__=='__main__':
    if len(sys.argv)!=3: raise SystemExit('Usage: prepare_ks.py SOURCE DESTINATION')
    prepare(sys.argv[1],sys.argv[2])
