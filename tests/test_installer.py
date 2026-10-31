"""All installer mutations are confined to temporary staged filesystems."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / 'bin/mykos-install'

def run(root, action, *args, ok=True):
    value = subprocess.run([sys.executable,str(INSTALL),action,'--root',str(root),*map(str,args)],
                           capture_output=True,text=True)
    if ok: assert value.returncode==0, value.stdout+value.stderr
    else: assert value.returncode!=0, value.stdout
    return value

def test_dry_run_has_no_side_effects(tmp_path):
    root=tmp_path.resolve()/'absent'
    run(root,'install','--dry-run')
    desktop=run(root,'install','--dry-run','--desktop-network','192.168.222.0/24')
    assert 'desktop_network_if_requested' in desktop.stdout
    assert '192.168.222.0/24' in desktop.stdout
    run(root,'install','--desktop-network','192.168.222.0/24',ok=False)
    run(root,'install','--dry-run','--desktop-network','0.0.0.0/0',ok=False)
    assert not root.exists()


def test_documentation_ancestors_are_traversable_with_restrictive_umask(tmp_path):
    root=tmp_path.resolve()/'root'
    result=subprocess.run([sys.executable,str(INSTALL),'install','--root',str(root)],
                          capture_output=True,text=True,umask=0o077)
    assert result.returncode==0,result.stdout+result.stderr
    docs=root/'usr/local/share/mykos/docs'
    assert docs.parent.stat().st_mode & 0o777==0o755
    assert docs.stat().st_mode & 0o777==0o755
    assert (docs/'rendered').stat().st_mode & 0o777==0o755
    assert (root/'etc/mykos/config.json').stat().st_mode & 0o777==0o600
    assert (root/'opt/mykos-web/data').stat().st_mode & 0o777==0o700
    run(root,'verify')

def test_install_update_backup_restore_and_remove(tmp_path):
    root=tmp_path.resolve()/'root'
    run(root,'install')
    run(root,'verify')
    app=root/'opt/mykos-web'
    assert not (app/'data/auth.db').exists()
    assert not (app/'data/api_keys.db').exists()
    assert not (app/'data/api-token').exists()
    assert not (root/'usr/local/share/mykos/bin/mykos-web.img').exists()
    cfg=root/'etc/mykos/config.json'
    cfg.write_text('{"terminal_enabled":true}')
    identity=app/'data/auth.db';identity.write_bytes(b'untouched identity sentinel')
    (app/'app.py').write_text('# pre-update marker\n')
    run(root,'install')
    assert cfg.read_text()=='{"terminal_enabled":true}'
    assert identity.read_bytes()==b'untouched identity sentinel'
    assert '# pre-update marker' not in (app/'app.py').read_text()
    manifest=json.loads((root/'var/lib/mykos-installer/installed.json').read_text())
    guides = {'INSTALL_SERVICES.md', 'ARCHITECTURE.md', 'IDENTITY.md',
              'API.md', 'SCRIPTS.md', 'VALIDATION.md', 'DESKTOPS.md', 'RPM.md', 'KEY_ISSUES.md'}
    for entry in manifest['files']:
        if entry['path'].startswith('/usr/local/share/mykos/docs/') and Path(entry['path']).name in guides:
            assert entry['owner'] == 'web-read'
            assert entry['mode'] == 0o640
            guides.remove(Path(entry['path']).name)
    assert not guides
    snapshot=manifest['snapshot']
    run(root,'verify')
    output=tmp_path/'backup.tar.gz'
    run(root,'backup','--output',output,'--dry-run')
    assert not output.exists()
    run(root,'backup','--output',output)
    with tarfile.open(output) as t:
        assert not any('auth.db' in m.name for m in t.getmembers())
    run(root,'restore','--snapshot',snapshot)
    assert (app/'app.py').read_text()=='# pre-update marker\n'
    assert identity.read_bytes()==b'untouched identity sentinel'
    run(root,'install')
    run(root,'uninstall','--dry-run')
    assert (app/'app.py').exists()
    run(root,'uninstall')
    assert not (app/'app.py').exists()
    assert cfg.exists() and identity.exists()

def test_staging_rejects_host_actions_and_symlinks(tmp_path):
    root=tmp_path.resolve()/'root'
    run(root,'install','--enable',ok=False)
    run(root,'install','--python-deps',ok=False)
    assert not root.exists()
    root.mkdir()
    (root/'opt').symlink_to(tmp_path.resolve()/'elsewhere',target_is_directory=True)
    run(root,'install',ok=False)
    assert not (tmp_path/'elsewhere').exists()

def test_verify_detects_tamper_and_uninstall_preserves_it(tmp_path):
    root=tmp_path.resolve()/'root';run(root,'install')
    app=root/'opt/mykos-web/app.py';app.write_text('local edit')
    run(root,'verify',ok=False)
    run(root,'uninstall',ok=False)
    assert app.read_text()=='local edit'

def test_restore_rejects_unsafe_manifest_before_writes(tmp_path):
    root=tmp_path.resolve()/'root';run(root,'install')
    archive=tmp_path/'evil.tar.gz'
    payload=b'bad'
    manifest={'format':2,'kind':'mykos-snapshot','files':[{'path':'/opt/mykos-web/../../outside',
        'mode':0o644,'uid':0,'gid':0,'sha256':hashlib.sha256(payload).hexdigest()}],'absent':[]}
    with tarfile.open(archive,'w:gz') as t:
        data=json.dumps(manifest).encode();m=tarfile.TarInfo('manifest.json');m.size=len(data);t.addfile(m,io.BytesIO(data))
    before=(root/'opt/mykos-web/app.py').read_bytes()
    run(root,'restore','--snapshot',archive,ok=False)
    assert (root/'opt/mykos-web/app.py').read_bytes()==before

def test_data_backup_requires_explicit_restore_and_uses_sqlite(tmp_path):
    import sqlite3
    root=tmp_path.resolve()/'root';run(root,'install')
    db=root/'opt/mykos-web/data/auth.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE sentinel (value TEXT)');c.execute("INSERT INTO sentinel VALUES ('old')")
    output=tmp_path/'with-data.tar.gz'
    run(root,'backup','--output',output,'--include-data')
    with sqlite3.connect(db) as c:c.execute("UPDATE sentinel SET value='new'")
    run(root,'restore','--snapshot',output,ok=False)
    with sqlite3.connect(db) as c:assert c.execute('SELECT value FROM sentinel').fetchone()[0]=='new'
    run(root,'restore','--snapshot',output,'--include-data')
    with sqlite3.connect(db) as c:assert c.execute('SELECT value FROM sentinel').fetchone()[0]=='old'


def test_install_preserves_external_backup_symlink(tmp_path):
    root = tmp_path.resolve()/'root'; root.mkdir()
    storage = tmp_path.resolve()/'backup-volume'; storage.mkdir(mode=0o750)
    sentinel = storage/'customer-data'; sentinel.write_text('preserved')
    (root/'backups').symlink_to(storage, target_is_directory=True)
    before = storage.stat()
    run(root, 'install')
    run(root, 'verify')
    assert (root/'backups').is_symlink()
    assert (root/'backups').readlink() == storage
    assert sentinel.read_text() == 'preserved'
    assert list(storage.iterdir()) == [sentinel]
    assert storage.stat().st_mode == before.st_mode
    assert storage.stat().st_uid == before.st_uid
    assert storage.stat().st_gid == before.st_gid


def test_install_rejects_broken_backup_alias_before_snapshot(tmp_path):
    root = tmp_path.resolve()/'root'; root.mkdir()
    (root/'backups').symlink_to(tmp_path.resolve()/'missing')
    result = run(root, 'install', ok=False)
    assert 'existing directory' in result.stderr
    assert not (root/'var/backups/mykos-installer').exists()


def test_launcher_upgrade_removes_legacy_and_snapshot_can_restore_it(tmp_path):
    root=tmp_path.resolve()/'root'
    legacy=root/'usr/local/bin/mykos';legacy.parent.mkdir(parents=True)
    legacy.write_text('#!/bin/bash\ncd /usr/local/share/mykos && ./MENU\n');legacy.chmod(0o755)
    old=legacy.read_bytes()
    run(root,'install')
    launcher=root/'usr/bin/mykos'
    assert launcher.stat().st_mode & 0o777 == 0o755
    assert '/usr/share/mykos' in launcher.read_text()
    assert not legacy.exists()
    subprocess.run(['bash','-n',str(launcher)],check=True)
    manifest=json.loads((root/'var/lib/mykos-installer/installed.json').read_text())
    run(root,'verify')
    run(root,'restore','--snapshot',manifest['snapshot'])
    assert legacy.read_bytes()==old
    assert not launcher.exists()


def test_update_clears_notes_but_preserves_identity(tmp_path):
    import sqlite3
    root=tmp_path/'root';run(root,'install')
    database=root/'opt/mykos-web/data/auth.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE pane_notes(pane TEXT, text TEXT)')
        db.execute("INSERT INTO pane_notes VALUES('host','change request')")
        db.execute('CREATE TABLE personal_notes(pane TEXT, text TEXT)')
        db.execute("INSERT INTO personal_notes VALUES('host','private personal text')")
        db.execute('CREATE TABLE users(name TEXT, hash TEXT)')
        db.execute("INSERT INTO users VALUES('admin','preserved')")
        db.execute('CREATE TABLE api_keys(digest TEXT)')
        db.execute("INSERT INTO api_keys VALUES('preserved-key')")
    run(root,'install')
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT * FROM pane_notes').fetchall()==[]
        assert db.execute('SELECT * FROM personal_notes').fetchall()==[('host','private personal text')]
        assert db.execute('SELECT * FROM users').fetchall()==[('admin','preserved')]
        assert db.execute('SELECT * FROM api_keys').fetchall()==[('preserved-key',)]


def test_rpm_and_installer_include_kickstart_validator(tmp_path):
    result=run(tmp_path/'root','install','--dry-run')
    plan=json.loads(result.stdout[result.stdout.index('{'):])
    assert 'pykickstart' in plan['system_packages_if_requested']
    spec=(REPO/'packaging/rpm/mykos.spec').read_text()
    assert any(line.startswith('Requires:') and 'pykickstart' in line.split() for line in spec.splitlines())




def test_http_ip_origin_and_explicit_https_switch(tmp_path):
    root=tmp_path.resolve()/'root'
    run(root,'install','--public-origin','http://192.168.22.10')
    env=root/'etc/mykos/web.env'
    assert env.read_text()=='MYKOS_PUBLIC_ORIGIN=http://192.168.22.10\nMYKOS_SECURE_COOKIE=0\n'
    run(root,'install','--public-origin','https://example.com','--set-public-origin')
    assert env.read_text()=='MYKOS_PUBLIC_ORIGIN=https://example.com\nMYKOS_SECURE_COOKIE=1\n'
    run(tmp_path.resolve()/'bad','install','--public-origin','http://example.com',ok=False)
    run(tmp_path.resolve()/'staged','install','--configure-https',ok=False)


def test_firstboot_venv_is_readable_by_service_group_without_touching_link_targets(tmp_path,monkeypatch):
    import importlib.machinery,importlib.util,os
    loader=importlib.machinery.SourceFileLoader('installer_venv_test',str(INSTALL))
    spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    root=tmp_path/'venv';(root/'bin').mkdir(parents=True,mode=0o700)
    script=root/'bin/uvicorn';script.write_text('entrypoint');script.chmod(0o700)
    data=root/'module.py';data.write_text('module');data.chmod(0o600)
    external=tmp_path/'python';external.write_text('system interpreter');external.chmod(0o755)
    (root/'bin/python').symlink_to(external)
    owners=[];monkeypatch.setattr(module.os,'chown',lambda *args:owners.append(args))
    module.readable_venv(root,123)
    assert (root/'bin').stat().st_mode&0o777==0o750
    assert script.stat().st_mode&0o777==0o750 and data.stat().st_mode&0o777==0o640
    assert external.stat().st_mode&0o777==0o755
    assert not any(p==root/'bin/python' or p==external for p,*rest in owners)


def test_upgrade_preserves_operator_kickstart_package_and_post_sections(tmp_path):
    root=tmp_path.resolve()/'root'
    run(root,'install')
    p=root/'usr/local/share/mykos/pos/ks/rh7.ks'
    custom=p.read_bytes()+b'\n# Operator-owned additions\n%post\necho keep-my-application-settings\n%end\n'
    p.write_bytes(custom)
    run(root,'install')
    assert p.read_bytes()==custom
    manifest=json.loads((root/'var/lib/mykos-installer/installed.json').read_text())
    entry=next(e for e in manifest['files'] if e['path']=='/usr/local/share/mykos/pos/ks/rh7.ks')
    assert entry['mutable'] and entry['sha256']==hashlib.sha256(custom).hexdigest()
    run(root,'verify')




def load_venv_installer():
    import importlib.machinery,importlib.util
    loader=importlib.machinery.SourceFileLoader('installer_venv_migration_test',str(INSTALL))
    spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module);return module


def test_runtime_migration_keeps_old_environment_and_second_install_reuses_new_one(tmp_path,monkeypatch):
    module=load_venv_installer();venv=tmp_path/'.venv';(venv/'bin').mkdir(parents=True);(venv/'bin/python').write_text('old');(venv/'old-library').write_text('keep me')
    def version(args,**kwargs):return '[3, 9]' if str(args[0])==str(venv/'bin/python') and (venv/'old-library').exists() else '[3, 11]'
    monkeypatch.setattr(module.subprocess,'check_output',version);calls=[]
    def runner(args):
        calls.append(args)
        if args[1:3]==['-m','venv']:(venv/'bin').mkdir(parents=True);(venv/'bin/python').write_text('new')
    backup=module.provision_venv(venv,'/selected/python',tmp_path/'requirements.txt',runner=runner)
    assert (backup/'old-library').read_text()=='keep me' and not (venv/'old-library').exists()
    assert (venv/'bin/python').read_text()=='new'
    before=len([c for c in calls if c[1:3]==['-m','venv']])
    assert module.provision_venv(venv,'/selected/python',tmp_path/'requirements.txt',runner=runner) is None
    assert len([c for c in calls if c[1:3]==['-m','venv']])==before


def test_failed_runtime_dependency_install_restores_original_environment(tmp_path,monkeypatch):
    import pytest
    module=load_venv_installer();venv=tmp_path/'.venv';(venv/'bin').mkdir(parents=True);(venv/'bin/python').write_text('old');(venv/'old-library').write_text('original')
    monkeypatch.setattr(module.subprocess,'check_output',lambda args,**kwargs:'[3, 9]' if str(args[0])==str(venv/'bin/python') else '[3, 11]')
    def runner(args):
        if args[1:3]==['-m','venv']:(venv/'bin').mkdir(parents=True);(venv/'bin/python').write_text('partial new environment')
        elif args[1:4]==['-m','pip','install']:raise RuntimeError('dependency download failed')
    with pytest.raises(RuntimeError,match='dependency download failed'):module.provision_venv(venv,'/selected/python',tmp_path/'requirements.txt',runner=runner)
    assert (venv/'old-library').read_text()=='original' and (venv/'bin/python').read_text()=='old'
