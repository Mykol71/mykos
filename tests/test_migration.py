"""Exercise rename migrations using a neutral predecessor in isolated roots."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import importlib.machinery
import importlib.util
from types import SimpleNamespace
import pytest

REPO = Path(__file__).resolve().parents[1]
MIGRATE = REPO/'bin/mykos-migrate'

def previous(tmp_path):
    root = tmp_path.resolve()/'root'
    files = {
        '/opt/oldweb/app.py': 'app = FastAPI(root_path="/oldweb")\n',
        '/opt/oldweb/inventory.json': '{"nodes":[{"id":"01234567"}]}',
        '/etc/oldtool/config.json': '{"network_device":"enp2s0","backup_root":"/backups","toolkit_root":"/usr/local/share/oldtool"}',
        '/etc/oldtool/web.env': 'OLDWEB_PUBLIC_ORIGIN=https://example.com\nOLDWEB_TOKEN=keep-OLDWEB_secret\n',
        '/etc/oldtool/pos.env': 'VPN_PSK=unchanged-secret\n',
        '/usr/local/share/oldtool/MENU': '#!/bin/sh\n',
        '/usr/local/share/oldtool/.envtype': 'Production',
        '/usr/local/share/oldtool/pos/.01234567.imagetype': 'rh9',
        '/etc/systemd/system/oldweb-web.service': '[Service]\nUser=oldwebuser\nGroup=oldwebuser\n',
        '/etc/systemd/system/oldweb-web.service.d/https.conf': '[Service]\nEnvironment=OLDWEB_PUBLIC_ORIGIN=https://example.com\n',
        '/etc/httpd/conf.d/dashboard.conf': 'ProxyPass /oldweb/ http://127.0.0.1:8000/\nRedirectMatch ^/oldweb$ /oldweb/\n',
    }
    entries=[]
    for name,text in files.items():
        file=root/name.lstrip('/');file.parent.mkdir(parents=True,exist_ok=True);file.write_text(text)
        if '/.envtype' not in name and '/pos/.' not in name and '.service.d/' not in name and '/conf.d/' not in name:
            entries.append({'path':name,'mode':0o640,'owner':'root','mutable':name.endswith(('.env','.json')),'sha256':hashlib.sha256(text.encode()).hexdigest()})
    for name in ('auth.db','api_keys.db'):
        file=root/'opt/oldweb/data'/name;file.parent.mkdir(exist_ok=True)
        with sqlite3.connect(file) as c:
            c.execute('CREATE TABLE preserved (secret TEXT)');c.execute('INSERT INTO preserved VALUES (?)',(name+'-sentinel',))
    manifest=root/'var/lib/oldtool-installer/installed.json';manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'format':1,'files':entries}))
    return root,manifest

def invoke(root,*args):
    return subprocess.run([sys.executable,str(MIGRATE),'--root',str(root),'--public-origin','https://example.com',
                           '--python',sys.executable,*args],capture_output=True,text=True)

def test_migration_preserves_identities_settings_and_proxy(tmp_path):
    root,manifest=previous(tmp_path)
    result=invoke(root,'--dry-run')
    assert result.returncode==0,result.stderr
    assert not (root/'opt/mykos-web').exists()
    result=invoke(root)
    assert result.returncode==0,result.stdout+result.stderr
    for name in ('auth.db','api_keys.db'):
        with sqlite3.connect(root/'opt/mykos-web/data'/name) as c:
            assert c.execute('SELECT secret FROM preserved').fetchone()[0]==name+'-sentinel'
    assert json.loads((root/'etc/mykos/config.json').read_text())=={'network_device':'enp2s0','backup_root':'/backups','toolkit_root':'/usr/local/share/mykos'}
    assert (root/'etc/mykos/web.env').read_text()=='MYKOS_PUBLIC_ORIGIN=https://example.com\nMYKOS_TOKEN=keep-OLDWEB_secret\n'
    assert (root/'etc/mykos/pos.env').read_text()=='VPN_PSK=unchanged-secret\n'
    assert (root/'usr/local/share/mykos/.envtype').read_text()=='Production'
    assert (root/'usr/local/share/mykos/pos/.01234567.imagetype').read_text()=='rh9'
    assert (root/'etc/systemd/system/mykos-web.service.d/https.conf').read_text().endswith('Environment=MYKOS_PUBLIC_ORIGIN=https://example.com\n')
    assert '/mykos/' in (root/'etc/httpd/conf.d/dashboard.conf').read_text()
    assert '/oldweb' not in (root/'etc/httpd/conf.d/dashboard.conf').read_text()
    snapshots=list((root/'var/backups/mykos-installer').glob('before-migration-*.tar.gz'))
    assert len(snapshots)==1
    with tarfile.open(snapshots[0]) as t:
        assert t.extractfile('etc/httpd/conf.d/dashboard.conf').read().startswith(b'ProxyPass /oldweb/')
    assert snapshots[0].stat().st_mode & 0o777==0o600
    again=invoke(root)
    assert again.returncode!=0 and 'already exists' in again.stderr

def test_migration_rejects_symlink_before_writes(tmp_path):
    root,manifest=previous(tmp_path)
    target=root/'etc/oldtool/pos.env';target.unlink();target.symlink_to('/etc/passwd')
    result=invoke(root)
    assert result.returncode!=0 and 'Unsafe migration path' in result.stderr
    assert not (root/'opt/mykos-web').exists()
    assert not (root/'var/backups/mykos-installer').exists()


def load_migrator():
    loader=importlib.machinery.SourceFileLoader('migration_test_module',str(MIGRATE))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    return module


@pytest.mark.parametrize('fail_action',['install','verify'])
def test_resume_interrupted_migration_preserves_copied_data(tmp_path,monkeypatch,fail_action):
    root,manifest=previous(tmp_path)
    module=load_migrator()
    argv=[str(MIGRATE),'--root',str(root),'--public-origin','https://example.com','--python',sys.executable]
    monkeypatch.setattr(sys,'argv',argv)
    original_run=module.run
    def fail(*args):
        if len(args)>2 and str(args[1]).endswith('mykos-install') and args[2]==fail_action:
            raise subprocess.CalledProcessError(1,args)
        original_run(*args)
    monkeypatch.setattr(module,'run',fail)
    with pytest.raises(subprocess.CalledProcessError):module.main()
    snapshot=next((root/'var/backups/mykos-installer').glob('before-migration-*.tar.gz'))
    digest=hashlib.sha256(snapshot.read_bytes()).hexdigest()
    db=root/'opt/mykos-web/data/auth.db'
    with sqlite3.connect(db) as c:c.execute("UPDATE preserved SET secret='keep-resumed-identity'")
    cfg=root/'etc/mykos/config.json'
    settings=json.loads(cfg.read_text());settings['network_device']='keep-device';cfg.write_text(json.dumps(settings))
    monkeypatch.setattr(module,'run',original_run)
    monkeypatch.setattr(sys,'argv',argv+['--resume-from',str(snapshot)])
    module.main()
    with sqlite3.connect(db) as c:assert c.execute('SELECT secret FROM preserved').fetchone()[0]=='keep-resumed-identity'
    assert json.loads(cfg.read_text())['network_device']=='keep-device'
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest()==digest
    assert len(list(snapshot.parent.glob('before-migration-*.tar.gz')))==1


def test_resume_requires_matching_private_snapshot(tmp_path):
    root,manifest=previous(tmp_path)
    result=invoke(root);assert result.returncode==0,result.stderr
    snapshot=next((root/'var/backups/mykos-installer').glob('before-migration-*.tar.gz'))
    snapshot.chmod(0o644)
    result=invoke(root,'--resume-from',str(snapshot))
    assert result.returncode!=0 and 'private regular file' in result.stderr
    snapshot.chmod(0o600)
    manifest.write_text(manifest.read_text()+'\n')
    result=invoke(root,'--resume-from',str(snapshot))
    assert result.returncode!=0 and 'does not match' in result.stderr


def test_resume_accepts_renamed_identity_only_with_matching_uid(monkeypatch):
    module=load_migrator()
    renamed=SimpleNamespace(pw_name='mykosweb',pw_uid=999,pw_gid=999)
    def account(name):
        if name=='mykosweb':return renamed
        raise KeyError(name)
    monkeypatch.setattr(module.pwd,'getpwnam',account)
    monkeypatch.setattr(module.grp,'getgrgid',lambda gid:SimpleNamespace(gr_name='mykosweb'))
    with pytest.raises(ValueError,match='matching --resume-from'):module.migration_identity({'user':'oldwebuser'})
    with pytest.raises(ValueError,match='matching --resume-from'):module.migration_identity({'user':'oldwebuser'},998)
    assert module.migration_identity({'user':'oldwebuser'},999)==renamed
