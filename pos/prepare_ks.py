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


def for_builder(text, timesource, weakdeps="--exclude-weakdeps", instlangs="--inst-langs"):
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
        if re.search(r'(?<!\S)--(?:inst-langs|instLangs)(?==|\s|$)',line):
            if not instlangs:
                raise ValueError('The installed kickstart parser cannot limit installed languages')
            line=re.sub(r'(?<!\S)--(?:inst-langs|instLangs)(?==|\s|$)',instlangs,line)
        return line
    return re.sub(r'^%packages\b[^\n]*',packages,text,flags=re.M)


def builder_interpreter():
    executable=shutil.which('livemedia-creator')
    if not executable: raise ValueError('Install livemedia-creator before preparing the build kickstart')
    with open(executable) as handle: shebang=handle.readline().strip()
    if not shebang.startswith('#!'): raise ValueError('Cannot determine the image builder Python interpreter')
    interpreter=shlex.split(shebang[2:])
    return interpreter


def builder_capabilities():
    interpreter=builder_interpreter()
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
instlangs = None
for option in ('--inst-langs', '--instLangs'):
    try:
        KickstartParser(makeVersion()).readKickstartFromString('%packages ' + option + '=en\\n%end\\n')
    except KickstartError:
        continue
    instlangs = option
    break
import rpm
print(json.dumps({'timesource': 'timesource' in makeVersion().commands, 'weakdeps': weakdeps,
                  'authselect': 'authselect' in makeVersion().commands, 'instlangs': instlangs,
                  'rpm_backend': rpm.expandMacro('%{_db_backend}')}))
"""
    result=subprocess.run(interpreter+['-c',probe],capture_output=True,text=True,timeout=15,check=True)
    data=json.loads(result.stdout)
    if (type(data.get('timesource')) is not bool or type(data.get('authselect')) is not bool
            or data.get('weakdeps') not in (None,'--exclude-weakdeps','--excludeWeakdeps')
            or data.get('instlangs') not in (None,'--inst-langs','--instLangs')):
        raise ValueError('Cannot determine image-builder kickstart support')
    return data


def native_rpmdb_post(text,source):
    """Use the native RPM database for old guests built by a SQLite installer."""
    match=re.fullmatch(r'(rh7|(?:rh|rocky|alma|oracle|centos)8)\.ks',source.name)
    if not match:return text
    major=7 if source.name=='rh7.ks' else 8
    helper=Path(__file__).with_name('rpmdb_compat.py').resolve(strict=True)
    repositories=[]
    for line in text.splitlines():
        if line.startswith('%'):break
        tokens=shlex.split(line,comments=True)
        if not tokens or tokens[0] not in ('url','repo'):continue
        option='--url' if tokens[0]=='url' else '--baseurl'
        for index,token in enumerate(tokens[1:],1):
            value=token[len(option)+1:] if token.startswith(option+'=') else tokens[index+1] if token==option else None
            if value and value not in repositories:repositories.append(value)
    if not repositories:raise ValueError('Legacy RPM database conversion requires explicit build repositories')
    shell=shlex.join([*builder_interpreter(),str(helper)])+' "$target_root" --major '+str(major)
    for repository in repositories:shell+=' --repo '+shlex.quote(repository)
    return text+('\n%post --nochroot --erroronfail --log=/tmp/mykos-native-rpmdb.log\n'
        'set -eu\ntarget_root=\nfor candidate in /mnt/sysroot /mnt/sysimage; do\n'
        '    if [ -f "$candidate/etc/redhat-release" ] && '
        'grep -Eq "release '+str(major)+'([. ]|$)" "$candidate/etc/redhat-release"; then\n'
        '        target_root=$candidate\n        break\n    fi\ndone\n'
        '[ -n "$target_root" ] || { echo "Legacy installation root not found" >&2; exit 1; }\n'
        +shell+'\n%end\n')


def container_build(text):
    """Apply container-only build rules to a copy, preserving operator sources."""
    def packages(match):
        body=match[2]
        # Respect an operator kickstart that permits a kernel. Newer Anaconda
        # can select one implicitly, and filtering its required firmware makes
        # the whole transaction unsatisfiable. Only use the container-only
        # firmware exclusions when the source explicitly excludes the kernel.
        if '-kernel' not in body.splitlines():return match[0]
        # Containers use the host kernel and hardware; these packages can add
        # gigabytes through package-group defaults or weak dependencies.
        for exclusion in ('-linux-firmware','-*-firmware','-microcode_ctl'):
            if exclusion not in body.splitlines():body+=exclusion+'\n'
        return match[1]+body+match[3]
    text=re.sub(r'(^%packages\b[^\n]*\n)(.*?)(^%end\b)',packages,text,flags=re.M|re.S)
    def tmpfiles(match):
        line=match[0]
        if '--boot' not in line.split():return line
        # Anaconda binds the builder's /run into its installation root. Never
        # let an image's boot-only tmpfiles rule block logins on the builder.
        for prefix in ('/run/nologin','/var/run/nologin'):
            option='--exclude-prefix='+prefix
            if option not in line.split():line+=' '+option
        return line
    return re.sub(r'^[ \t]*systemd-tmpfiles[ \t]+[^\n]*$',tmpfiles,text,flags=re.M)



def rh7_auth_compat(text):
    """Run RH7 authconfig in the target, avoiding the newer installer's shim."""
    # Only inspect top-level commands, never commands inside operator scripts.
    header_end=re.search(r'^%',text,flags=re.M)
    end=header_end.start() if header_end else len(text)
    header=text[:end]
    matches=list(re.finditer(r'^[ \t]*(?:auth|authconfig)\s+([^\n]+)$',header,flags=re.M))
    if not matches:return text
    if len(matches)!=1:raise ValueError('RH7 compatibility requires one authentication command')
    args=shlex.split(matches[0][1],comments=True)
    # Keep this workaround specific to the diagnosed configuration. Do not guess
    # how to translate custom LDAP/SSSD or other authentication requirements.
    if sorted(args)!=['--passalgo=sha512','--useshadow']:
        raise ValueError('RH7 compatibility supports only --useshadow --passalgo=sha512')
    packages=re.search(r'^%packages\b[^\n]*\n(.*?)^%end\b',text,flags=re.M|re.S)
    if not packages or 'authconfig' not in packages[1].splitlines():
        raise ValueError('RH7 compatibility requires the authconfig package')
    match=matches[0]
    header=header[:match.start()]+'# MYKOS: RH7 authconfig runs in the target %post below.'+header[match.end():]
    return header+text[end:]+('\n%post --erroronfail --log=/mykos-authconfig-post.log\n'
        '/usr/sbin/authconfig --update --nostart '+shlex.join(args)+'\n%end\n')


def rh7_download_compat(text):
    """Stage updateos outside the chroot, where the builder's DNS works."""
    marker='# MYKOS: stage RH7 updateos using the installer network.'
    if marker in text:return text
    block="""updateos_tmp=$(mktemp /usr/bin/.updateos.XXXXXX)
trap 'rm -f "$updateos_tmp"' EXIT
curl --fail --show-error --location --retry 2 --connect-timeout 15 --max-time 120 \\
    --output "$updateos_tmp" https://mykol.com/updateos/updateos
[ -s "$updateos_tmp" ]
bash -n "$updateos_tmp"
chmod 0755 "$updateos_tmp"
mv -f "$updateos_tmp" /usr/bin/updateos
trap - EXIT
"""
    if 'https://mykol.com/updateos/updateos' not in text:return text
    sections=list(re.finditer(r'^%post\b[^\n]*\n.*?^%end\b[^\n]*(?:\n|$)',text,flags=re.M|re.S))
    matching=[section for section in sections if block in section[0] and '--nochroot' not in section[0].splitlines()[0]]
    if len(matching)!=1 or text.count(block)!=1:
        raise ValueError('Custom RH7 updateos download needs reconciliation before adapting it')
    download=block.replace('mktemp /usr/bin/.updateos.XXXXXX','mktemp "$target_root/usr/bin/.updateos.XXXXXX"')
    download=download.replace('mv -f "$updateos_tmp" /usr/bin/updateos','mv -f "$updateos_tmp" "$target_root/usr/bin/updateos"')
    host=('\n%post --nochroot --erroronfail --log=/tmp/mykos-rh7-updateos-host.log\n'
          +marker+'\nset -eu\ntarget_root=\n'
          'for candidate in /mnt/sysroot /mnt/sysimage; do\n'
          '    if [ -d "$candidate/usr/bin" ] && '
          'grep -Eq "release 7([. ]|$)" "$candidate/etc/redhat-release"; then\n'
          '        target_root=$candidate\n        break\n    fi\ndone\n'
          '[ -n "$target_root" ] || { echo "RH7 installation root not found" >&2; exit 1; }\n'
          +download+'%end\n\n')
    section=matching[0]
    adapted=section[0].replace(block,'# updateos was staged by the preceding host-side post script.\n'
                                     '[ -s /usr/bin/updateos ]\nbash -n /usr/bin/updateos\n',1)
    return text[:section.start()]+host+adapted+text[section.end():]


def repository_endpoints(text):
    """Apply this host's mirror origin only to the generated build copy."""
    origin=os.environ.get('MYKOS_REPO_ORIGIN')
    if not origin:
        settings=Path('/etc/mykos/config.json')
        if settings.is_file():origin=json.loads(settings.read_text()).get('repository_origin')
    if not origin:return text
    from urllib.parse import urlsplit
    if not isinstance(origin,str) or not re.fullmatch(r'https?://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?/?',origin):
        raise ValueError('Invalid repository_origin; select an HTTP(S) host without credentials or a path')
    parsed=urlsplit(origin)
    if parsed.port is not None and not 1<=parsed.port<=65535:raise ValueError('Invalid repository port')
    return re.sub(r'https://mykol[.]com(?=/(?:rhel-|mykos-1[.]5-|updateos(?:/|\b)))',origin.rstrip('/'),text)


def prepare(source,destination):
    source,destination=Path(source),Path(destination)
    if source.is_symlink() or destination.is_symlink(): raise ValueError('Symlink kickstart rejected')
    capabilities=builder_capabilities()
    authselect=capabilities.pop('authselect')
    backend=capabilities.pop('rpm_backend',None)
    data=source.read_text()
    if source.name=='rh7.ks' and authselect:data=rh7_auth_compat(data)
    if source.name=='rh7.ks':data=rh7_download_compat(data)
    data=for_builder(container_build(data),**capabilities)
    if backend=='sqlite':data=native_rpmdb_post(data,source)
    data=repository_endpoints(data)
    fd,name=tempfile.mkstemp(prefix='.mykos-ks-',dir=destination.parent)
    try:
        with os.fdopen(fd,'w') as handle:handle.write(data)
        os.replace(name,destination)
    finally:
        if os.path.exists(name):os.unlink(name)

if __name__=='__main__':
    if len(sys.argv)!=3: raise SystemExit('Usage: prepare_ks.py SOURCE DESTINATION')
    prepare(sys.argv[1],sys.argv[2])
