"""Repository safety checks use private fixtures, never real sync workers."""
from pathlib import Path
import base64
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('repo_sync_safe', ROOT / 'repo/bin/sync_repos.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def make_repo(root, packages):
    (root / 'repodata').mkdir(parents=True, exist_ok=True)
    rows = []
    for name, data in packages.items():
        path = root / 'Packages' / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        rows.append('<package type="rpm"><name>fixture</name><checksum type="sha256">%s</checksum><size package="%s"/><location href="Packages/%s"/></package>' %
                    (hashlib.sha256(data).hexdigest(), len(data), name))
    primary = gzip.compress(('<metadata xmlns="%s" packages="%s">%s</metadata>' %
                             (sync.NS['p'], len(rows), ''.join(rows))).encode(), mtime=0)
    digest = hashlib.sha256(primary).hexdigest()
    href = 'repodata/' + digest + '-primary.xml.gz'
    (root / href).write_bytes(primary)
    (root / 'repodata/repomd.xml').write_text('<repomd xmlns="%s"><data type="primary"><checksum type="sha256">%s</checksum><size>%s</size><location href="%s"/></data></repomd>' %
                                            (sync.NS['r'], digest, len(primary), href))


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def seed(root, major=9):
    for name in sync.repositories(major):
        make_repo(root / name, {'old.rpm': b'previous package'})


def clone(live, staged):
    shutil.copytree(live, staged) if live.exists() else staged.mkdir()


@pytest.mark.parametrize('major,count', [(6, 3), (7, 3), (8, 2), (9, 2), (10, 2)])
def test_release_selection_and_constrained_worker(major, count, tmp_path):
    assert len(sync.repositories(major)) == count
    cmd = sync.worker_command(major, tmp_path, 'a' * 32)
    assert '--cgroup-parent' in cmd and sync.SLICE in cmd
    assert '--cpus=1' in cmd and '--memory=2g' in cmd
    assert '--memory-swap=2g' in cmd and '--pids-limit=256' in cmd
    assert '--oom-score-adj=500' in cmd
    assert '--cpu-shares=128' in cmd and '--cgroup-conf=io.weight=default 10' in cmd
    assert str(tmp_path / 'stage') + ':/stage:rw' in cmd
    assert not any('/var/www/html:' in x for x in cmd)
    assert '--rm' in cmd and '--replace' not in cmd


@pytest.mark.parametrize('argument', ['5', '11', 'garbage'])
def test_invalid_release_does_not_create_scratch(argument, tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'repo/bin/sync_repos.py'), argument],
                            cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 2 and 'invalid' in result.stderr
    assert not list(tmp_path.iterdir())


def test_validate_complete_repository(tmp_path):
    make_repo(tmp_path, {'one.rpm': b'one', 'two.rpm': b'two'})
    assert sync.validate_repository(tmp_path) == 2


@pytest.mark.parametrize('failure', ['missing_package', 'bad_package', 'bad_metadata', 'missing_primary'])
def test_validation_rejects_incomplete_or_corrupt_mirror(tmp_path, failure):
    make_repo(tmp_path, {'one.rpm': b'one'})
    if failure == 'missing_package':
        (tmp_path / 'Packages/one.rpm').unlink()
    elif failure == 'bad_package':
        (tmp_path / 'Packages/one.rpm').write_bytes(b'corrupted')
    elif failure == 'bad_metadata':
        next((tmp_path / 'repodata').glob('*.gz')).write_bytes(b'corrupted')
    else:
        (tmp_path / 'repodata/repomd.xml').write_text('<repomd/>')
    with pytest.raises(ValueError):
        sync.validate_repository(tmp_path)


@pytest.mark.parametrize('href', ['../outside', '%2e%2e/outside', '/etc/passwd', 'https://example.com/a', 'Packages/link.rpm'])
def test_repository_locations_cannot_escape_or_follow_symlinks(tmp_path, href):
    (tmp_path / 'Packages').mkdir()
    (tmp_path / 'Packages/link.rpm').symlink_to('/etc/passwd')
    with pytest.raises(ValueError):
        sync.relative_file(tmp_path, href)


def test_worker_failure_keeps_live_metadata_and_cleans_private_stage(tmp_path):
    seed(tmp_path)
    before = snapshot(tmp_path)
    calls = []
    def runner(cmd):
        work = Path(cmd[cmd.index('--cidfile') + 1]).parent
        # Emulate reposync downloading new metadata before its RPMs, then failing.
        for name in sync.repositories(9):
            make_repo(work / 'stage' / name, {'new.rpm': b'new'})
            (work / 'stage' / name / 'Packages/new.rpm').unlink()
        raise subprocess.CalledProcessError(37, cmd)
    with pytest.raises(subprocess.CalledProcessError):
        sync.sync_major(9, tmp_path, runner=runner, clone=clone,
                        publish=lambda *args: calls.append(args), cleanup=lambda *args: None)
    assert not calls and snapshot(tmp_path) == before
    assert not list(tmp_path.glob('.mykos-sync-*'))


def test_bad_component_publishes_none_of_the_selected_major(tmp_path):
    seed(tmp_path)
    before = snapshot(tmp_path)
    calls = []
    def runner(cmd):
        work = Path(cmd[cmd.index('--cidfile') + 1]).parent
        for name in sync.repositories(9):
            make_repo(work / 'stage' / name, {'new.rpm': b'new'})
        (work / 'stage' / sync.repositories(9)[1] / 'Packages/new.rpm').write_bytes(b'bad')
    with pytest.raises(ValueError):
        sync.sync_major(9, tmp_path, runner=runner, clone=clone,
                        publish=lambda *args: calls.append(args), cleanup=lambda *args: None)
    assert not calls and snapshot(tmp_path) == before


def test_interruption_cleans_owned_stage_and_keeps_live_tree(tmp_path):
    seed(tmp_path)
    before = snapshot(tmp_path)
    cleanup_calls = []
    def interrupted(cmd):
        raise InterruptedError('fixture cancellation')
    with pytest.raises(InterruptedError):
        sync.sync_major(9, tmp_path, runner=interrupted, clone=clone,
                        cleanup=lambda *args: cleanup_calls.append(args))
    assert cleanup_calls and snapshot(tmp_path) == before
    assert not list(tmp_path.glob('.mykos-sync-*'))


@pytest.mark.skipif(sys.platform != 'linux', reason='renameat2 is a Linux publication primitive')
def test_atomic_exchange_publishes_complete_tree_and_retains_old_urls(tmp_path):
    seed(tmp_path)
    old_files = snapshot(tmp_path)
    def runner(cmd):
        if cmd[0] == 'restorecon':
            return
        work = Path(cmd[cmd.index('--cidfile') + 1]).parent
        for name in sync.repositories(9):
            make_repo(work / 'stage' / name, {'new.rpm': b'new'})
    sync.sync_major(9, tmp_path, runner=runner, clone=clone, cleanup=lambda *args: None)
    for name in sync.repositories(9):
        assert sync.validate_repository(tmp_path / name) == 1
        assert (tmp_path / name / 'Packages/old.rpm').read_bytes() == b'previous package'
    for rel, data in old_files.items():
        if not rel.endswith('/repodata/repomd.xml'):
            assert (tmp_path / rel).read_bytes() == data
    assert not list(tmp_path.glob('.mykos-sync-*'))


def test_existing_immutable_url_cannot_be_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "run", lambda args: shutil.copy2(args[-2], args[-1]))
    old, new = tmp_path / 'live', tmp_path / 'stage'
    make_repo(old, {'old.rpm': b'old'})
    make_repo(new, {'old.rpm': b'changed content'})
    with pytest.raises(ValueError, match='immutable URL'):
        sync.preserve_previous(old, new)


def test_clone_requires_cow_and_never_hardlinks(tmp_path, monkeypatch):
    old = tmp_path / 'live'
    old.mkdir()
    calls = []
    monkeypatch.setattr(sync, 'run', lambda args: calls.append(args))
    sync.clone_repository(old, tmp_path / 'stage')
    assert '--reflink=always' in calls[0]
    assert '-l' not in calls[0]


def test_cleanup_refuses_worker_with_different_owner(tmp_path, monkeypatch):
    cid = 'a' * 64
    (tmp_path / 'worker.cid').write_text(cid)
    monkeypatch.setattr(sync.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(sync.subprocess, 'check_output', lambda *a, **k: json.dumps([{'Config': {'Labels': {sync.LABEL: 'other-run'}}}]))
    with pytest.raises(ValueError, match='ownership mismatch'):
        sync.cleanup_worker(tmp_path, 'our-run')


def test_cleanup_stops_only_recorded_owned_worker(tmp_path, monkeypatch):
    cid = 'b' * 64
    (tmp_path / 'worker.cid').write_text(cid)
    calls = []
    monkeypatch.setattr(sync.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(sync.subprocess, 'check_output', lambda *a, **k: json.dumps([{'Config': {'Labels': {sync.LABEL: 'ours'}}}]))
    monkeypatch.setattr(sync, 'run', lambda args, **k: calls.append(args))
    sync.cleanup_worker(tmp_path, 'ours')
    assert calls == [['podman', 'stop', '--time', '15', cid], ['podman', 'rm', cid]]


def test_unrecognized_stale_scratch_is_preserved(tmp_path):
    stale = tmp_path / '.mykos-sync-unknown'
    stale.mkdir()
    (stale / 'user-data').write_text('preserve')
    with pytest.raises(ValueError, match='Unrecognized|Untrusted'):
        sync.recover_stale(tmp_path)
    assert (stale / 'user-data').read_text() == 'preserve'


@pytest.mark.parametrize('major', [7, 8, 9, 10])
@pytest.mark.parametrize('bad_signature', [False, True])
@pytest.mark.parametrize('missing_repo', [False, True])
def test_worker_enforces_signatures_private_config_and_metadata(tmp_path, major, bad_signature, missing_repo):
    tools, repos, modules, upstream, stage = [tmp_path / n for n in ['bin', 'repos', 'modules', 'upstream', 'stage']]
    for path in [tools, repos, modules, upstream, stage]:
        path.mkdir()
    key = tmp_path / 'redhat-public-key'
    key.write_text('public fixture')
    events = tmp_path / 'events'
    for name in sync.repositories(major):
        make_repo(upstream / name, {'one.rpm': b'fixture'})
        group = b'<comps><group>fixture</group></comps>'
        (upstream / name / 'repodata/groups.xml').write_bytes(group)
        tree = ET.parse(upstream / name / 'repodata/repomd.xml')
        node = ET.SubElement(tree.getroot(), '{%s}data' % sync.NS['r'], {'type': 'group'})
        ET.SubElement(node, '{%s}checksum' % sync.NS['r'], {'type': 'sha256'}).text = hashlib.sha256(group).hexdigest()
        ET.SubElement(node, '{%s}size' % sync.NS['r']).text = str(len(group))
        ET.SubElement(node, '{%s}location' % sync.NS['r'], {'href': 'repodata/groups.xml'})
        tree.write(upstream / name / 'repodata/repomd.xml')
        (stage / name).mkdir()
    (repos / 'redhat.repo').write_text(''.join('[%s]\nbaseurl=https://cdn.example/%s\nenabled=1\ngpgcheck=0\nsslverify=0\ngpgkey=https://untrusted.example/key\nthrottle=0\n' % (n, n) for n in (sync.repositories(major)[:-1] if missing_repo else sync.repositories(major))))
    checker = tools / 'checker.py'
    checker.write_text('''import configparser,os,sys
args=sys.argv[1:]
with open(os.environ['EVENTS'],'a') as f:f.write(' '.join(args)+'\\n')
if any('reposync' in x for x in args):
 assert '--gpgcheck' in args
 config=next(x.split('=',1)[1] for x in args if x.startswith('--config='))
 p=configparser.RawConfigParser();p.read(config)
 assert p.get('main','plugins')=='0'
 if args[0]=='dnf':
  assert '--setopt=plugins=1' in args, 'reposync plugin must be enabled at command scope'
  assert '--disableplugin=*' in args and '--enableplugin=reposync' in args
 assert not os.listdir(p.get('main','reposdir'))
 for name in p.sections():
  if name=='main':continue
  assert p.get(name,'gpgcheck')=='1' and p.get(name,'sslverify')=='1'
  assert p.get(name,'throttle')=='20M'
  assert p.get(name,'gpgkey')=='file://'+os.environ['KEY']
 if os.environ['BAD_SIGNATURE']=='1':sys.exit(37)
''')
    for name in ['yum', 'dnf', 'reposync', 'rpm']:
        tool = tools / name
        # Use the actual test interpreter; this only logs/asserts fixture commands.
        tool.write_text('#!/bin/bash\nexec ' + sys.executable + ' ' + str(checker) + ' ' + name + ' "$@"\n')
        tool.chmod(0o755)
    (modules / 'yum.py').write_text('''import os
from pathlib import Path
import xml.etree.ElementTree as ET
class Repo:
 def __init__(self,name):self.cachedir=os.environ['UPSTREAM']+'/'+name+'/repodata';self.repoXML=object()
 def retrieveMD(self,kind):
  ns={'r':'http://linux.duke.edu/metadata/repo'}
  tree=ET.parse(Path(self.cachedir)/'repomd.xml')
  entry=next(x for x in tree.getroot().findall('r:data',ns) if x.get('type')==kind)
  return str(Path(self.cachedir)/Path(entry.find('r:location',ns).get('href')).name)
class Repos:
 def disableRepo(self,*a):pass
 def enableRepo(self,*a):pass
 def getRepo(self,name):return Repo(name)
class YumBase:
 def __init__(self):self.repos=Repos()
 def doConfigSetup(self,**k):pass
 def doRepoSetup(self):pass
''')
    worker = (ROOT / 'repo/bin/sync_worker.sh').read_text().replace('/etc/yum.repos.d', str(repos)).replace('/etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release', str(key)).replace('/stage', str(stage)).replace('/tmp/mykos-repo-config', str(tmp_path / 'config')).replace('/run/mykos-entitlement', str(tmp_path / 'missing-entitlement')).replace('/run/mykos-rhsm-ca', str(tmp_path / 'missing-ca')).replace('/etc/rhsm/ca', str(tmp_path / 'missing-image-ca'))
    result = subprocess.run(['bash', '-s', '--', str(major)], input=worker, text=True, capture_output=True,
                            env={**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH'], 'PYTHONPATH': str(modules), 'EVENTS': str(events), 'KEY': str(key), 'UPSTREAM': str(upstream), 'BAD_SIGNATURE': '1' if bad_signature else '0'})
    assert result.returncode == (1 if missing_repo else 37 if bad_signature else 0), result.stdout + result.stderr
    assert 'rpm --import' in events.read_text()
    if missing_repo:
        assert 'Required upstream repositories unavailable' in result.stderr
        assert '--gpgcheck' not in events.read_text()
    else:
        assert '--gpgcheck' in events.read_text()
    assert not list(tmp_path.glob('config.*'))
    if major == 7 and not bad_signature and not missing_repo:
        for name in sync.repositories(major):
            assert (stage / name / 'repodata/repomd.xml').read_bytes() == (upstream / name / 'repodata/repomd.xml').read_bytes()
            assert list((stage / name / 'repodata').glob('*primary.xml.gz'))
            assert (stage / name / 'repodata/groups.xml').read_bytes() == b'<comps><group>fixture</group></comps>'


@pytest.mark.skipif(sys.platform != 'linux', reason='GNU reflink support is checked on Linux/dev')
def test_real_cow_clone_cannot_mutate_live_package(tmp_path):
    old, new = tmp_path / 'live', tmp_path / 'stage'
    make_repo(old, {'one.rpm': b'previous payload'})
    sync.clone_repository(old, new)
    (new / 'Packages/one.rpm').write_bytes(b'changed in worker')
    assert (old / 'Packages/one.rpm').read_bytes() == b'previous payload'
    assert (old / 'Packages/one.rpm').stat().st_ino != (new / 'Packages/one.rpm').stat().st_ino


def test_direct_cli_uses_the_managed_background_budget(tmp_path):
    tools = tmp_path / 'bin'
    tools.mkdir()
    event = tmp_path / 'event'
    (tools / 'readlink').write_text('#!/bin/sh\nprintf "%s\\n" "$2"\n')
    (tools / 'readlink').chmod(0o755)
    (tools / 'systemd-run').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$EVENT"\n')
    (tools / 'systemd-run').chmod(0o755)
    wrapper = tmp_path / 'sync_repos'
    wrapper.write_text((ROOT / 'repo/bin/sync_repos').read_text().replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'export PATH="$TEST_PATH:$PATH"'))
    env = {**os.environ, 'TEST_PATH': str(tools), 'EVENT': str(event)}
    env.pop('MYKOS_REPO_SYNC_MANAGED', None)
    result = subprocess.run(['bash', str(wrapper), '9'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    args = event.read_text().splitlines()
    assert '--slice=mykosreposync.slice' in args
    assert '--property=Nice=15' in args and '--property=IOWeight=10' in args
    assert '--wait' in args and '--collect' in args


def test_wrapper_rejects_rh5_without_starting_a_unit(tmp_path):
    result = subprocess.run(['bash', str(ROOT / 'repo/bin/sync_repos'), '5'], cwd=tmp_path,
                            text=True, capture_output=True)
    assert result.returncode == 2 and 'Usage:' in result.stderr
    assert not list(tmp_path.iterdir())


def test_actual_sigterm_preserves_live_files_and_cleans_stage(tmp_path):
    import time
    live = tmp_path / 'live'
    live.mkdir()
    seed(live)
    before = snapshot(live)
    ready = tmp_path / 'ready'
    child = tmp_path / 'child.py'
    child.write_text('''import importlib.util,signal,time,shutil,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('sync',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
signal.signal(signal.SIGTERM,m.interrupted)
def clone(a,b):shutil.copytree(a,b)
def worker(cmd):
 Path(sys.argv[3]).touch()
 time.sleep(60)
try:m.sync_major(9,Path(sys.argv[2]),runner=worker,clone=clone,cleanup=lambda *a:None)
except InterruptedError:sys.exit(23)
''')
    process = subprocess.Popen([sys.executable, str(child), str(ROOT / 'repo/bin/sync_repos.py'), str(live), str(ready)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            if ready.exists() or process.poll() is not None:
                break
            time.sleep(.05)
        assert ready.exists(), process.communicate(timeout=5)
        process.terminate()
        out, err = process.communicate(timeout=10)
        assert process.returncode == 23, (out, err)
        assert snapshot(live) == before
        assert not list(live.glob('.mykos-sync-*'))
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_creation_interruption_recovers_owned_worker_without_cidfile(tmp_path, monkeypatch):
    cid = 'c' * 64
    (tmp_path / 'owner.json').write_text(json.dumps({'worker_name': 'mykos-repo-sync-rhel9'}))
    calls = []
    monkeypatch.setattr(sync.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(sync.subprocess, 'check_output', lambda *a, **k: json.dumps([{'Id': cid, 'Config': {'Labels': {sync.LABEL: 'ours'}}}]))
    monkeypatch.setattr(sync, 'run', lambda args, **k: calls.append(args))
    sync.cleanup_worker(tmp_path, 'ours')
    assert calls == [['podman', 'stop', '--time', '15', cid], ['podman', 'rm', cid]]


def test_name_collision_without_our_cid_is_never_removed(tmp_path, monkeypatch):
    (tmp_path / 'owner.json').write_text(json.dumps({'worker_name': 'mykos-repo-sync-rhel9'}))
    monkeypatch.setattr(sync.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(sync.subprocess, 'check_output', lambda *a, **k: json.dumps([{'Id': 'd' * 64, 'Config': {'Labels': {sync.LABEL: 'someone-else'}}}]))
    monkeypatch.setattr(sync, 'run', lambda *a, **k: pytest.fail('A pre-existing container must not be touched'))
    sync.cleanup_worker(tmp_path, 'ours')


@pytest.mark.parametrize('major', [7, 8, 9, 10])
@pytest.mark.parametrize('status', ['200', '403'])
def test_missing_standard_sources_use_only_authenticated_matching_major(tmp_path, major, status):
    import configparser
    repos, credentials, ca, tools, work = [tmp_path / n for n in ['repos', 'credentials', 'ca', 'bin', 'work']]
    for p in [repos, credentials, ca, tools, work]:
        p.mkdir()
    (work / 'empty-repos').mkdir()
    (credentials / '123.pem').write_text('public certificate fixture')
    (credentials / '123-key.pem').write_text('PRIVATE KEY SENTINEL')
    (ca / 'redhat-uep.pem').write_text('CA fixture')
    urls = tmp_path / 'urls'
    curl = tools / 'curl'
    curl.write_text('''#!/bin/bash
printf '%s\n' "${@: -1}" >> "$URLS"
printf '%s' "$HTTP_STATUS"
''')
    curl.chmod(0o755)
    code = (ROOT / 'repo/bin/sync_worker.sh').read_text().split("<<'CONFIG'\n", 1)[1].split('\nCONFIG', 1)[0]
    code = code.replace('/etc/yum.repos.d', str(repos)).replace('/run/mykos-entitlement', str(credentials)).replace('/run/mykos-rhsm-ca', str(ca)).replace('/etc/rhsm/ca', str(tmp_path / 'no-image-ca'))
    result = subprocess.run([sys.executable, '-', str(major), str(work)], input=code,
                            text=True, capture_output=True,
                            env={**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH'], 'URLS': str(urls), 'HTTP_STATUS': status})
    assert (result.returncode == 0) == (status == '200'), result.stderr
    assert 'PRIVATE KEY SENTINEL' not in result.stdout + result.stderr
    for url in urls.read_text().splitlines():
        prefix = 'https://cdn.redhat.com/content/dist/rhel/server/7/7Server/' if major == 7 else 'https://cdn.redhat.com/content/dist/rhel%s/%s/' % (major, major)
        assert url.startswith(prefix) and url.endswith('/repodata/repomd.xml')
    if status == '200':
        config = configparser.RawConfigParser()
        config.read(work / 'sync.conf')
        for name in sync.repositories(major):
            assert config.get(name, 'gpgcheck') == '1'
            assert config.get(name, 'sslverify') == '1'
            assert config.get(name, 'sslclientkey') == str(credentials / '123-key.pem')
        assert 'PRIVATE KEY SENTINEL' not in (work / 'sync.conf').read_text()
    else:
        assert not (work / 'sync.conf').exists()
