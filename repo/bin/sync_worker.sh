#!/bin/bash
# Only /stage is writable host storage; credentials remain in standard RO secrets.
set -euo pipefail
release=$1
mode=${2:-sync}
case "$mode" in sync|check) ;; *) exit 2 ;; esac
case "$release" in
  6|7) yum -y --disablerepo='*' --enablerepo='ubi-7*' --setopt=throttle=20M --setopt=max_connections=1 install yum-utils ;;
  8|9|10) dnf -y --disablerepo='*' --enablerepo="ubi-${release}*" --setopt=throttle=20M --setopt=max_parallel_downloads=1 install dnf-plugins-core ;;
  *) echo 'Unsupported repository release' >&2; exit 2 ;;
esac
key=/etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release
test -s "$key"
rpm --import "$key"
config_work=$(mktemp -d /tmp/mykos-repo-config.XXXXXX)
trap 'rm -rf -- "$config_work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
chmod 700 "$config_work"
mkdir "$config_work/empty-repos"
repo_python=
for candidate in python3 python /usr/libexec/platform-python; do
  if command -v "$candidate" >/dev/null; then repo_python=$candidate; break; fi
done
test -n "$repo_python"
"$repo_python" - "$release" "$config_work" <<'CONFIG'
from __future__ import print_function
import glob, os, subprocess, sys
try:
    import ConfigParser as configparser
except ImportError:
    import configparser
major = int(sys.argv[1]); work = sys.argv[2]
source = configparser.RawConfigParser(); source.read(glob.glob('/etc/yum.repos.d/*.repo'))
names = ['rhel-%s-server%s-rpms' % (major, part) for part in ('', '-optional', '-extras')] if major in (6, 7) else ['rhel-%s-for-x86_64-%s-rpms' % (major, part) for part in ('baseos', 'appstream')]
missing = [name for name in names if not source.has_section(name)]
if missing:
    ca = next((p for p in ['/run/mykos-rhsm-ca/redhat-uep.pem', '/etc/rhsm/ca/redhat-uep.pem'] if os.path.isfile(p)), None)
    pairs = [(p, p[:-4] + '-key.pem') for p in sorted(glob.glob('/run/mykos-entitlement/*.pem')) if not p.endswith('-key.pem') and os.path.isfile(p[:-4] + '-key.pem')]
    if not ca or not pairs:
        sys.exit('Required upstream repositories unavailable: ' + ', '.join(missing))
    for name in missing:
        if major in (6, 7):
            component = 'optional/os' if '-optional-' in name else 'extras/os' if '-extras-' in name else 'os'
            url = 'https://cdn.redhat.com/content/dist/rhel/server/%s/%sServer/x86_64/' % (major, major) + component
        else:
            component = 'appstream' if '-appstream-' in name else 'baseos'
            url = 'https://cdn.redhat.com/content/dist/rhel%s/%s/x86_64/%s/os' % (major, major, component)
        status = 'unavailable'
        for cert, key in pairs:
            process = subprocess.Popen(['curl', '--silent', '--show-error', '--location', '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '10', '--max-time', '45', '--cert', cert, '--key', key, '--cacert', ca, '--output', '/dev/null', '--write-out', '%{http_code}', url + '/repodata/repomd.xml'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            output, error = process.communicate(); status = output.decode('ascii', 'replace').strip()
            if process.returncode == 0 and status == '200':
                source.add_section(name)
                for key_name, value in [('name', name), ('baseurl', url), ('sslclientcert', cert), ('sslclientkey', key), ('sslcacert', ca)]:
                    source.set(name, key_name, value)
                print('Authenticated upstream repository: ' + name)
                break
        else:
            sys.exit('Required upstream repositories unavailable: ' + name + ' (HTTP ' + status + ')')
target = configparser.RawConfigParser(); target.add_section('main')
for key, value in [('reposdir', os.path.join(work, 'empty-repos')), ('plugins', '0'), ('gpgcheck', '1'), ('sslverify', '1'), ('throttle', '20M')]:
    target.set('main', key, value)
if major in (6, 7):
    target.set('main', 'max_connections', '1')
else:
    target.set('main', 'max_parallel_downloads', '1')
for name in names:
    target.add_section(name)
    for key, value in source.items(name): target.set(name, key, value)
    for key, value in [('enabled', '1'), ('gpgcheck', '1'), ('sslverify', '1'), ('gpgkey', 'file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release'), ('throttle', '20M')]:
        target.set(name, key, value)
with open(os.path.join(work, 'sync.conf'), 'w') as handle: target.write(handle)
with open(os.path.join(work, 'names'), 'w') as handle: handle.write('\n'.join(names) + '\n')
CONFIG
if [ "$mode" = check ]; then
    if [ "$release" = 6 ] || [ "$release" = 7 ]; then
        yum -y --config="$config_work/sync.conf" makecache
    else
        dnf -y --config="$config_work/sync.conf" --refresh makecache
    fi
    echo "RHEL $release upstream preflight complete; no mirror publication performed."
    exit 0
fi
while IFS= read -r repo; do
  printf 'MYKOS_JOB_COMPONENT %s\n' "$repo"
  if [ -d "/stage/$repo" ]; then
    "$repo_python" /run/mykos-verify-cached "/stage/$repo"
  fi
  if [ "$release" = 6 ] || [ "$release" = 7 ]; then
    reposync --config="$config_work/sync.conf" --gpgcheck --download-metadata --repo="$repo" -p /stage
    # Legacy yum-utils omits core metadata from --download-metadata. Copy the
    # complete upstream manifest, preserving groups/updateinfo without recreaterepo.
    "$repo_python" - "$config_work/sync.conf" "$repo" <<'METADATA'
from __future__ import print_function
import os, shutil, sys
import xml.etree.ElementTree as ET
import yum
base = yum.YumBase(); base.doConfigSetup(fn=sys.argv[1], init_plugins=False)
base.repos.disableRepo('*'); base.repos.enableRepo(sys.argv[2]); base.doRepoSetup()
repo = base.repos.getRepo(sys.argv[2]); manifest = repo.repoXML
source = os.path.join(repo.cachedir, 'repomd.xml')
root = os.path.join('/stage', sys.argv[2])
ns = {'r': 'http://linux.duke.edu/metadata/repo'}
for item in ET.parse(source).getroot().findall('r:data', ns):
    href = item.find('r:location', ns).get('href')
    target = os.path.normpath(os.path.join(root, href))
    if not target.startswith(root + '/repodata/'):
        sys.exit('Unsafe upstream metadata location')
    cached = repo.retrieveMD(item.get('type'))
    if not os.path.isdir(os.path.dirname(target)): os.makedirs(os.path.dirname(target))
    shutil.copyfile(cached, target)
shutil.copyfile(source, os.path.join(root, 'repodata', 'repomd.xml'))
METADATA
  else
    # reposync is a DNF plugin. Keep subscription/other plugins disabled,
    # but explicitly enable this one for the download command only.
    dnf -y --config="$config_work/sync.conf" --setopt=plugins=1 --disableplugin='*' --enableplugin=reposync reposync --gpgcheck --download-metadata --repo="$repo" -p /stage
  fi
done < "$config_work/names"
