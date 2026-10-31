"""Exercise release selection and worker commands without network or containers."""
from pathlib import Path
import os
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('release,expected', [('6',[6]),('7',[7]),('',[6,7,8,9,10])])
def test_repository_sync_workers(tmp_path,release,expected):
    tools=tmp_path/'bin';tools.mkdir()
    events=tmp_path/'events'
    for name in ('yum','dnf','createrepo'):
        tool=tools/name
        tool.write_text('#!/bin/bash\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$EVENTS"\n')
        tool.chmod(0o755)
    reposync=tools/'reposync'
    reposync.write_text('#!'+sys.executable+'''\nimport configparser,os,sys
with open(os.environ['EVENTS'],'a') as f:f.write('reposync '+' '.join(sys.argv[1:])+'\\n')
if any(arg.startswith('--repo=rhel-6-') for arg in sys.argv):
    config=next(arg.split('=',1)[1] for arg in sys.argv if arg.startswith('--config='))
    parser=configparser.RawConfigParser();parser.read(config)
    assert set(parser.sections())=={'main','rhel-6-server-rpms','rhel-6-server-optional-rpms','rhel-6-server-extras-rpms'}
    assert parser.get('main','plugins')=='0'
    assert not os.listdir(parser.get('main','reposdir'))
    for section in parser.sections()[1:]:
        assert '/6/6Server/' in parser.get(section,'baseurl')
        assert parser.get(section,'sslclientcert')=='/run/secrets/entitlement.pem'
''');reposync.chmod(0o755)
    (tools/'python').write_text('#!/bin/sh\nexec '+__import__('shlex').quote(sys.executable)+' "$@"\n');(tools/'python').chmod(0o755)
    repos=tmp_path/'repos';repos.mkdir()
    (repos/'redhat.repo').write_text('[rhel-7-server-rpms]\nbaseurl=https://cdn.example/7/$releasever/os\nenabled=1\n'+''.join(
        '[rhel-6-server%s-rpms]\nbaseurl=https://cdn.example/6/$releasever/x86_64/os\nsslclientcert=/run/secrets/entitlement.pem\nenabled=0\n'%part
        for part in ('','-optional','-extras')))
    (tools/'flock').write_text('#!/bin/bash\nexit 0\n');(tools/'flock').chmod(0o755)
    (tools/'podman').write_text('''#!/bin/bash
printf 'worker %s\n' "${@: -1}" >> "$EVENTS"
exec bash -s -- "${@: -1}"
''');(tools/'podman').chmod(0o755)
    source=(ROOT/'repo/bin/sync_repos').read_text()
    source=source.replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin','export PATH="$TEST_PATH:$PATH"')
    source=source.replace('/run/lock/mykos-repo-sync.lock',str(tmp_path/'sync.lock'))
    source=source.replace('/etc/yum.repos.d',str(repos))
    source=source.replace('/tmp/mykos-rh6',str(tmp_path/'mykos-rh6'))
    result=subprocess.run(['bash','-s','--',*([release] if release else [])],input=source,
                          text=True,capture_output=True,env={**os.environ,'TEST_PATH':str(tools),'EVENTS':str(events)})
    assert result.returncode==0,result.stderr
    lines=events.read_text().splitlines()
    assert [int(line.split()[1]) for line in lines if line.startswith('worker ')]==expected
    for major in expected:
        if major in (6,7):
            for component in ('','-optional','-extras'):
                line=next(line for line in lines if line.startswith('reposync ') and '--repo=rhel-%s-server%s-rpms'%(major,component) in line)
                assert '--download-metadata' in line
                assert ('--arch=x86_64' in line)==(major==6)
                assert '--releasever' not in line
        else:
            for component in ('baseos','appstream'):
                assert any(line.startswith('dnf reposync ') and '--repo=rhel-%s-for-x86_64-%s-rpms'%(major,component) in line for line in lines)
    assert '/etc/yum/vars/releasever' not in source


def test_missing_rh6_credentials_fails_before_repo_download(tmp_path):
    source=(ROOT/'repo/bin/sync_repos').read_text().split("<<'RH6_CONFIG'\n",1)[1].split('\nRH6_CONFIG',1)[0]
    repos=tmp_path/'repos';repos.mkdir()
    (repos/'redhat.repo').write_text('[rhel-7-server-rpms]\nbaseurl=https://cdn.example/7/$releasever/os\nenabled=1\n')
    source=source.replace('/etc/yum.repos.d',str(repos))
    source=source.replace('/run/mykos-entitlement',str(tmp_path/'missing-credentials'))
    destination=tmp_path/'rh6.conf'
    result=subprocess.run([sys.executable,'-',str(destination),str(tmp_path/'empty')],input=source,text=True,capture_output=True)
    assert result.returncode!=0 and 'needs the host entitlement' in result.stderr
    assert not destination.exists()


@pytest.mark.parametrize('optional_status,success',[('200',True),('403',False)])
def test_rh6_direct_sources_authenticate_metadata_and_keep_credentials_private(tmp_path,optional_status,success):
    import configparser
    source=(ROOT/'repo/bin/sync_repos').read_text().split("<<'RH6_CONFIG'\n",1)[1].split('\nRH6_CONFIG',1)[0]
    repos=tmp_path/'repos';repos.mkdir()
    (repos/'redhat.repo').write_text('[rhel-7-server-rpms]\nbaseurl=https://cdn.example/7/$releasever/os\nenabled=1\n')
    credentials=tmp_path/'credentials';credentials.mkdir()
    ca=tmp_path/'ca';ca.mkdir();(ca/'redhat-uep.pem').write_text('CA fixture')
    (credentials/'123.pem').write_text('certificate fixture')
    (credentials/'123-key.pem').write_text('PRIVATE KEY SENTINEL')
    tools=tmp_path/'bin';tools.mkdir()
    curl=tools/'curl'
    curl.write_text('''#!/bin/bash
case "${@: -1}" in
  https://cdn.redhat.com/content/dist/rhel/server/6/6Server/x86_64/optional/os/repodata/repomd.xml) printf '%s' "$OPTIONAL_STATUS" ;;
  https://cdn.redhat.com/content/dist/rhel/server/6/6Server/x86_64/os/repodata/repomd.xml|https://cdn.redhat.com/content/dist/rhel/server/6/6Server/x86_64/extras/os/repodata/repomd.xml) printf 200 ;;
  *) exit 97 ;;
esac
''');curl.chmod(0o755)
    source=source.replace('/etc/yum.repos.d',str(repos)).replace('/run/mykos-entitlement',str(credentials)).replace('/run/mykos-rhsm-ca',str(ca))
    destination=tmp_path/'rh6.conf';empty=tmp_path/'empty';empty.mkdir()
    result=subprocess.run([sys.executable,'-',str(destination),str(empty)],input=source,text=True,capture_output=True,
        env={**os.environ,'PATH':str(tools)+':'+os.environ['PATH'],'OPTIONAL_STATUS':optional_status})
    assert (result.returncode==0)==success,result.stderr
    assert 'PRIVATE KEY SENTINEL' not in result.stdout+result.stderr
    if success:
        config=configparser.RawConfigParser();config.read(destination)
        assert set(config.sections())=={'main','rhel-6-server-rpms','rhel-6-server-optional-rpms','rhel-6-server-extras-rpms'}
        assert config.get('rhel-6-server-rpms','sslclientkey')==str(credentials/'123-key.pem')
        assert config.get('rhel-6-server-rpms','sslverify')=='1'
        assert config.get('rhel-6-server-rpms','gpgcheck')=='1'
        assert 'PRIVATE KEY SENTINEL' not in destination.read_text()
    else:
        assert 'rhel-6-server-optional-rpms' in result.stderr and '403' in result.stderr
        assert not destination.exists()


def test_failed_worker_stops_sync_before_later_releases(tmp_path):
    source=(ROOT/'repo/bin/sync_repos').read_text()
    source=source.replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin',
        'flock() { :; }; podman() { echo "failed worker ${@: -1}"; return 37; }')
    source=source.replace('/run/lock/mykos-repo-sync.lock',str(tmp_path/'sync.lock'))
    result=subprocess.run(['bash','-s'],input=source,text=True,capture_output=True)
    assert result.returncode==37
    assert result.stdout=='failed worker 6\n'
