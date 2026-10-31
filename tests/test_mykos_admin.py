"""MYKOS menu operations against isolated paths and mocked system commands."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

HELPERS = Path(__file__).resolve().parents[1] / 'services/helpers'
sys.path.insert(0, str(HELPERS))
import mykos_admin as mykos_admin
import mykos_profiles


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(mykos_profiles.runtime, 'config', lambda: {})
    backups = tmp_path/'backups'; backups.mkdir()
    repos = tmp_path/'repos'; repos.mkdir()
    home = backups/'tfrsync-01234567'; home.mkdir()
    user = SimpleNamespace(pw_name=home.name, pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
    monkeypatch.setattr(mykos_admin, 'BACKUPS', backups)
    monkeypatch.setattr(mykos_admin, 'REPOS', repos)
    monkeypatch.setattr(mykos_admin, 'LOCK', tmp_path/'lock')
    monkeypatch.setattr(mykos_admin, 'SIZE_CACHE', tmp_path/'cache')
    monkeypatch.setattr(mykos_admin.pwd, 'getpwall', lambda: [user])
    monkeypatch.setattr(mykos_admin.pwd, 'getpwnam', lambda name: user)
    calls = []
    monkeypatch.setattr(mykos_admin, 'run', lambda args, **kwargs: calls.append(args) or '')
    return home, repos, calls


def test_backup_list_and_retained_data_deletion(sandbox, monkeypatch):
    home, _, calls = sandbox
    (home/'usr2').mkdir(); (home/'.ssh').mkdir()
    (home/'paying_customer').touch()
    monkeypatch.setattr(mykos_admin, 'sizes', lambda _: {home.name: 4096})
    value = mykos_admin.backup_status()['accounts'][0]
    assert value['code'] == '01234567' and value['type'] == 'rsync'
    assert value['subscriber'] is True and value['used_bytes'] == 4096
    mykos_admin.dispatch(['backup-action', 'delete', '01234567', '{"remove_data": false}'])
    assert (home/'usr2').exists() and not (home/'.ssh').exists()
    assert calls == [['/usr/sbin/userdel', home.name]]


def test_backup_subscriber_and_public_key(sandbox):
    home, _, _ = sandbox
    mykos_admin.dispatch(['backup-action', 'subscriber', '01234567', '{"subscriber": true}'])
    assert (home/'paying_customer').is_file()
    mykos_admin.dispatch(['backup-action', 'subscriber', '01234567', '{"subscriber": false}'])
    assert not (home/'paying_customer').exists()
    mykos_admin.dispatch(['backup-action', 'key', '01234567', json.dumps({'key': 'ssh-ed25519 YWJj test'})])
    text = (home/'.ssh/authorized_keys').read_text()
    assert text == f'restrict,command="{home}/tfrsync" ssh-ed25519 YWJj test\n'
    assert (home/'.ssh/authorized_keys').stat().st_mode & 0o777 == 0o600


def test_backup_key_reads_reject_symlinks(sandbox, tmp_path):
    home, _, _ = sandbox
    target = tmp_path/'secret'; target.write_text('must not read')
    (home/'etc').mkdir(); (home/'etc/redhat-release').symlink_to(target)
    with pytest.raises(OSError): mykos_admin.read_at(home, 'etc/redhat-release')


@pytest.mark.parametrize('name', ['../repos', '--all', 'a/b', 'lost+found', 'RPM-GPG-KEY-test'])
def test_repository_target_validation(sandbox, name):
    _, _, calls = sandbox
    with pytest.raises(ValueError):
        mykos_admin.dispatch(['repo-action', 'delete', name, '{}'])
    assert not any(args[1:2] == ['stop'] for args in calls)


def test_repository_symlink_and_delete_during_sync(sandbox, tmp_path, monkeypatch):
    _, repos, _ = sandbox
    outside = tmp_path/'outside'; outside.mkdir()
    (repos/'linked').symlink_to(outside)
    with pytest.raises(ValueError): mykos_admin.repo_path('linked')
    (repos/'rhel-9-rpms').mkdir()
    monkeypatch.setattr(mykos_admin, 'service_state', lambda _: 'activating')
    with pytest.raises(ValueError):
        mykos_admin.dispatch(['repo-action', 'delete', 'rhel-9-rpms', '{}'])
    assert (repos/'rhel-9-rpms').is_dir()


def test_repository_actions_are_fixed(sandbox):
    _, _, calls = sandbox
    mykos_admin.dispatch(['repo-action', 'sync', 'all', '{}'])
    mykos_admin.dispatch(['repo-action', 'disable', 'all', '{}'])
    assert ['/usr/bin/systemctl', 'start', '--no-block', 'mykos-repo-sync.service'] in calls
    assert ['/usr/bin/systemctl', 'stop', 'httpd'] in calls
    with pytest.raises(ValueError): mykos_admin.dispatch(['repo-action', 'exec', 'all', '{}'])


def test_create_and_delete_backup_account(sandbox, tmp_path, monkeypatch):
    existing, _, calls = sandbox
    code = '12345678'; home = existing.parent/('tfrsync-'+code)
    toolkit = tmp_path/'toolkit'; (toolkit/'back/bin').mkdir(parents=True)
    (toolkit/'back/bin/tfrsync').write_text('#!/bin/sh\nexit 0\n')
    monkeypatch.setattr(mykos_admin, 'TOOLKIT', toolkit)
    user = SimpleNamespace(pw_name=home.name, pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
    def lookup(name):
        if not home.exists(): raise KeyError(name)
        return user
    def execute(args, **kwargs):
        calls.append(args)
        if args[0] == '/usr/sbin/useradd': home.mkdir()
        return ''
    monkeypatch.setattr(mykos_admin.pwd, 'getpwnam', lookup)
    monkeypatch.setattr(mykos_admin, 'run', execute)
    mykos_admin.dispatch(['backup-action', 'create', code, json.dumps({
        'key': 'ssh-ed25519 YWJj test', 'subscriber': True})])
    assert (home/'tfrsync').is_file() and (home/'.ssh/authorized_keys').is_file()
    assert (home/'paying_customer').exists()
    mykos_admin.dispatch(['backup-action', 'delete', code, '{"remove_data": true}'])
    assert not home.exists()


def test_backup_storage_alias_and_child_symlink_checks(sandbox, tmp_path, monkeypatch):
    home, _, _ = sandbox
    storage = home.parent
    alias = tmp_path/'backups-alias'; alias.symlink_to(storage, target_is_directory=True)
    monkeypatch.setattr(mykos_admin, 'BACKUPS', alias)
    assert mykos_admin.directory(alias) == alias
    assert mykos_admin.directory(alias/home.name) == alias/home.name
    (home/'escape').symlink_to(tmp_path)
    with pytest.raises(ValueError): mykos_admin.directory(alias/home.name/'escape')
    storage.chmod(0o777)
    with pytest.raises(ValueError): mykos_admin.directory(alias)


def test_repository_listing_excludes_support_directories(sandbox, monkeypatch):
    _, root, _ = sandbox
    names = ['updateos', 'ostools', 'support', 'rhel-9-rpms']
    for name in names:
        (root/name).mkdir()
    monkeypatch.setattr(mykos_admin, 'sizes', lambda _: {})
    result = mykos_admin.repo_status()
    assert [row['name'] for row in result['repos']] == ['rhel-9-rpms']
    assert all((root/name).is_dir() for name in names)


def test_repository_storage_alias_lists_and_confines_deletion(sandbox, tmp_path, monkeypatch):
    _, storage, _ = sandbox
    alias = tmp_path/'web-root'; alias.symlink_to(storage, target_is_directory=True)
    (storage/'rhel-9-rpms').mkdir()
    unrelated = tmp_path/'unrelated'; unrelated.mkdir()
    (unrelated/'sentinel').write_text('preserved')
    (storage/'linked').symlink_to(unrelated, target_is_directory=True)
    monkeypatch.setattr(mykos_admin, 'REPOS', alias)
    monkeypatch.setattr(mykos_admin, 'sizes', lambda _: {'rhel-9-rpms': 100})
    monkeypatch.setattr(mykos_admin, 'service_state', lambda _: 'inactive')
    result = mykos_admin.repo_status()
    assert [row['name'] for row in result['repos']] == ['rhel-9-rpms']
    assert result['repos'][0]['used_bytes'] == 100
    with pytest.raises(ValueError):
        mykos_admin.dispatch(['repo-action', 'delete', 'linked', '{}'])
    mykos_admin.dispatch(['repo-action', 'delete', 'rhel-9-rpms', '{}'])
    assert not (storage/'rhel-9-rpms').exists()
    assert (unrelated/'sentinel').read_text() == 'preserved'
    assert alias.is_symlink()


def test_repository_storage_parent_alias_and_untrusted_target(sandbox, tmp_path, monkeypatch):
    _, storage, _ = sandbox
    (storage/'html').mkdir()
    alias = tmp_path/'www'; alias.symlink_to(storage, target_is_directory=True)
    monkeypatch.setattr(mykos_admin, 'REPOS', alias/'html')
    assert mykos_admin.directory(alias/'html') == alias/'html'
    storage.chmod(0o777)
    with pytest.raises(ValueError): mykos_admin.directory(alias/'html')


def test_backup_last_updated_uses_summary_log(sandbox):
    home, _, _ = sandbox
    assert mykos_admin.backup_status()['accounts'][0]['updated_at'] is None
    log = home/'usr2/bbx/log/tfrsync-summary.log'
    log.parent.mkdir(parents=True)
    log.write_text('backup summary')
    os.utime(log, (1700000000, 1700000000))
    assert mykos_admin.backup_status()['accounts'][0]['updated_at'] == 1700000000
    log.unlink()
    log.symlink_to(home/'outside-log')
    (home/'outside-log').write_text('not a backup log')
    assert mykos_admin.backup_status()['accounts'][0]['updated_at'] is None


def test_repository_sizes_cached_and_preserved_on_scan_failure(tmp_path, monkeypatch):
    root = tmp_path/'repos'; root.mkdir()
    monkeypatch.setattr(mykos_admin, 'SIZE_CACHE', tmp_path/'cache')
    calls = []
    def scan(args, timeout):
        calls.append((args, timeout))
        return '4096\t' + str(root/'rhel-9') + '\n8192\t' + str(root)
    monkeypatch.setattr(mykos_admin, 'run', scan)
    monkeypatch.setattr(mykos_admin.time, 'time', lambda: 1000)
    expected = {'rhel-9':4096, 'repos':8192}
    assert mykos_admin.sizes(root) == expected
    assert calls[0][1] == 120
    assert mykos_admin.sizes(root) == expected
    assert len(calls) == 1
    monkeypatch.setattr(mykos_admin.time, 'time', lambda: 1601)
    def fail(*args, **kwargs):
        raise mykos_admin.subprocess.TimeoutExpired('du', 120)
    monkeypatch.setattr(mykos_admin, 'run', fail)
    assert mykos_admin.sizes(root) == expected


def test_latest_rpm_ignores_symlinks_and_returns_relative_name(tmp_path):
    import os
    from mykos_admin import latest_rpm
    old=tmp_path/'old.rpm';old.write_text('old');os.utime(old,(10,10))
    sub=tmp_path/'Packages';sub.mkdir();new=sub/'new.rpm';new.write_text('new');os.utime(new,(20,20))
    (tmp_path/'link.rpm').symlink_to(new)
    assert latest_rpm(tmp_path)=='Packages/new.rpm'


def test_remote_repository_inventory_and_action_guard(tmp_path, monkeypatch):
    import time
    from io import BytesIO
    import urllib.request
    config=tmp_path/'repository-source.json'
    config.write_text(json.dumps({'host':'repo.example','inventory_url':'http://192.0.2.5/_mykos-inventory.json','public_base_url':'https://example/repos/'}))
    monkeypatch.setattr(mykos_admin,'REPOSITORY_SOURCE',config)
    payload={'repos':[{'name':'rhel-9-rpms'}],'storage':{},'collected_at':int(time.time())}
    class Opener:
        def open(self,url,timeout):
            assert url=='http://192.0.2.5/_mykos-inventory.json' and timeout==8
            return BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(urllib.request,'build_opener',lambda *args:Opener())
    result=mykos_admin.repo_status()
    assert result['source_host']=='repo.example' and result['read_only']
    assert result['public_base_url']=='https://example/repos/'
    with pytest.raises(ValueError,match='read-only'):
        mykos_admin.dispatch(['repo-action','delete','rhel-9-rpms','{}'])
    payload['collected_at']-=901
    with pytest.raises(ValueError,match='stale'):mykos_admin.repo_status()
    payload['collected_at']=int(time.time());payload['repos'][0]['name']='../private'
    with pytest.raises(ValueError,match='name'):mykos_admin.repo_status()
    config.chmod(0o666)
    with pytest.raises(ValueError,match='protected'):mykos_admin.repository_source()
