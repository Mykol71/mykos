"""Purge tests use disposable trees and mock every Podman/systemctl operation."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_prune as purge
import mykos_vm as vm
_BUILDERS=purge.builders


@pytest.fixture
def environment(tmp_path, monkeypatch):
    base=tmp_path.resolve()
    web=base/'web'; (web/'data').mkdir(parents=True)
    storage=base/'disks'; storage.mkdir(mode=0o700)
    toolkit=base/'toolkit'; (toolkit/'pos').mkdir(parents=True)
    state=base/'purge'
    monkeypatch.setattr(purge,'WEB',web); monkeypatch.setattr(purge,'TOOLKITS',[toolkit])
    monkeypatch.setattr(purge,'STATE',state); monkeypatch.setattr(purge,'BUILD',base/'build')
    monkeypatch.setattr(purge,'RUNTIME',[base/'jobs',base/'commands']);monkeypatch.setattr(purge,'STAGING',base/'staging');monkeypatch.setattr(purge,'EXPORTS',[base/'exports'])
    monkeypatch.setattr(vm,'STORAGE',storage); monkeypatch.setattr(vm,'storage_root',lambda:storage)
    monkeypatch.setattr(vm,'ROOT',toolkit/'pos/.vm')
    monkeypatch.setattr(purge,'mounts',lambda:[]); monkeypatch.setattr(purge,'builders',lambda:{})
    monkeypatch.setattr(purge,'cancel_builders',lambda:None)
    monkeypatch.setattr(purge.grp,'getgrnam',lambda name:type('Group',(),{'gr_gid':os.getgid()})())
    monkeypatch.setattr(purge.os,'chown',lambda *a:None)
    resources={'containers':[{'Names':['running-vm']}],'images':[{'Names':['localhost/win11']} ]}
    calls=[]
    def run(args,timeout=300):
        calls.append(args)
        if args[1:]==['info','--format','json']:
            return json.dumps({'store':{'graphRoot':str(base/'podman/storage'),'runRoot':str(base/'podman/run'),'volumePath':str(base/'podman/storage/volumes')}})
        if args[1:]==['ps','-a','--format','json']:return json.dumps(resources['containers'])
        if args[1:]==['images','--format','json']:return json.dumps(resources['images'])
        if args[1:]==['system','reset','--force']:
            resources.update(containers=[],images=[])
        if 'list-units' in args:return 'mykos-web.service loaded active running\nmykos-collector.timer loaded active waiting\nmykos-stage@12345678.service loaded active running'
        return ''
    monkeypatch.setattr(purge,'run',run)
    return base,web,storage,toolkit,state,resources,calls


def seed(environment):
    base,web,storage,toolkit,state,resources,calls=environment
    for name in ['win11','12345678','snapshots/12345678/snap1','.recovery/old-disk']:
        folder=storage/name;folder.mkdir(parents=True);(folder/'disk').write_text('private disk/RAM')
    for name in ['.12345678','.linux','.vm']:
        folder=toolkit/'pos'/name;folder.mkdir(exist_ok=True);(folder/'data').write_text('private runtime')
    (toolkit/'pos/.12345678.container').write_text('old ID')
    (toolkit/'pos/.shopcode').write_text('12345678')
    (toolkit/'pos/pos').write_text('keep source')
    (toolkit/'docs').mkdir();(toolkit/'docs/operator.md').write_text('keep operator doc')
    (purge.BUILD).mkdir();(purge.BUILD/'rootfs.tar.xz').write_text('build data')
    (web/'data/api-token').write_text('existing monitoring key');(web/'data/status.json').write_text('{}');(web/'data/legacy-api-token').write_text('obsolete')
    (web/'inventory.json').write_text('{"nodes":[{"id":"12345678"}]}')
    for db,table,value in [('auth.db','users','password hash'),('api_keys.db','api_keys','key digest')]:
        with sqlite3.connect(web/'data'/db) as c:
            c.execute('CREATE TABLE '+table+'(value TEXT)');c.execute('INSERT INTO '+table+' VALUES(?)',(value,))
    with sqlite3.connect(web/'data/auth.db') as c:
        c.execute('CREATE TABLE sessions(value TEXT)');c.execute("INSERT INTO sessions VALUES('browser token')")
        c.execute('CREATE TABLE pane_notes(value TEXT)');c.execute("INSERT INTO pane_notes VALUES('old notes')")
    external=base/'backups';external.mkdir();(external/'data').write_text('keep backup')
    (storage/'12345678/backup-link').symlink_to(external,target_is_directory=True)
    return external


def confirm():
    value={'state':'queued','confirmation':purge.plan()['confirmation']}
    with purge.locked():purge.save(value)


def test_plan_is_read_only_and_lists_every_snapshot_disk_and_marker(environment):
    external=seed(environment)
    result=purge.plan()
    assert result['containers']==1 and result['images']==1
    assert any(p.endswith('/snapshots') for p in result['remove_paths'])
    assert any(p.endswith('/.recovery') for p in result['remove_paths'])
    assert not purge.STATE.exists()
    assert external.exists() and (environment[2]/'win11/disk').exists()
    assert not any('reset' in c or 'stop' in c for c in environment[-1])


def test_purge_removes_state_and_preserves_identity_and_sources(environment):
    external=seed(environment);base,web,storage,toolkit,state,resources,calls=environment
    confirm();result=purge.execute()
    assert result['state']=='done' and list(storage.iterdir())==[]
    assert not (toolkit/'pos/.12345678').exists() and not purge.BUILD.exists()
    assert (toolkit/'pos/pos').read_text()=='keep source'
    assert (toolkit/'docs/operator.md').read_text()=='keep operator doc'
    assert (external/'data').read_text()=='keep backup'
    assert json.loads((web/'inventory.json').read_text())=={'nodes':[]}
    assert {p.name for p in (web/'data').iterdir()}=={'auth.db','api_keys.db','api-token'}
    assert (web/'data/api-token').read_text()=='existing monitoring key'
    with sqlite3.connect(web/'data/auth.db') as c:
        assert c.execute('SELECT * FROM users').fetchall()==[('password hash',)]
        assert c.execute('SELECT * FROM sessions').fetchall()==[('browser token',)]
        assert c.execute('SELECT * FROM pane_notes').fetchall()==[]
    with sqlite3.connect(web/'data/api_keys.db') as c:assert c.execute('SELECT * FROM api_keys').fetchall()==[('key digest',)]
    reset=next(i for i,c in enumerate(calls) if 'reset' in c)
    assert any('stop' in c for c in calls[:reset])
    resume=calls[-1];assert resume==['/usr/bin/systemctl','start','--no-block','mykos-web.service','mykos-collector.timer']


def test_wrong_confirmation_cannot_queue_or_execute(environment):
    with pytest.raises(ValueError,match='exact confirmation'):purge.queue('PURGE wrong-host')
    assert not any('start' in c or 'reset' in c for c in environment[-1])
    with pytest.raises(ValueError,match='No confirmed'):purge.execute()


def test_queue_requires_exact_phrase_and_refuses_duplicate(environment):
    result=purge.queue(purge.plan()['confirmation']);assert result['state']=='queued'
    assert environment[-1][-1]==['/usr/bin/systemctl','start','--no-block','mykos-purge.service']
    with pytest.raises(ValueError,match='already queued'):purge.queue(purge.plan()['confirmation'])


def test_reset_failure_retains_disks_and_resumes_infrastructure(environment,monkeypatch):
    seed(environment);confirm();original=purge.run
    def fail(args,**kwargs):
        if 'reset' in args:raise RuntimeError('reset failed')
        return original(args,**kwargs)
    monkeypatch.setattr(purge,'run',fail)
    with pytest.raises(RuntimeError,match='reset failed'):purge.execute()
    assert (environment[2]/'12345678/disk').exists()
    assert purge.status()['state']=='failed'
    assert 'mykos-web.service' in environment[-1][-1]


@pytest.mark.parametrize('unsafe',['/','/home','/etc/mykos','/var/backups'])
def test_broad_and_protected_storage_rejected(environment,monkeypatch,unsafe):
    monkeypatch.setattr(vm,'storage_root',lambda:Path(unsafe))
    with pytest.raises(ValueError):purge.plan()
    assert not any('reset' in c or 'stop' in c for c in environment[-1])


def test_nested_mount_and_symlink_storage_rejected(environment,monkeypatch):
    seed(environment);storage=environment[2]
    monkeypatch.setattr(purge,'mounts',lambda:[storage/'12345678/mounted'])
    with pytest.raises(ValueError,match='Unmount'):purge.plan()
    monkeypatch.setattr(purge,'mounts',lambda:[])
    link=environment[0]/'storage-link';link.symlink_to(storage,target_is_directory=True)
    monkeypatch.setattr(vm,'storage_root',lambda:link)
    with pytest.raises(ValueError,match='Symlink'):purge.plan()


def test_podman_storage_cannot_overlap_identity(environment,monkeypatch):
    original=purge.run
    def bad(args,**kwargs):
        if 'info' in args:return json.dumps({'store':{'graphRoot':str(environment[1]),'runRoot':'/run/containers/storage'}})
        return original(args,**kwargs)
    monkeypatch.setattr(purge,'run',bad)
    with pytest.raises(ValueError,match='preserved'):purge.plan()


def test_builder_selection_excludes_other_installers(environment,monkeypatch):
    table={1:{'args':['python3','/usr/sbin/livemedia-creator','--ks=/var/tmp/rh9.ks'],'parent':0,'started':'10'},
           2:{'args':['dnf'],'parent':1,'started':'11'},
           3:{'args':['anaconda','--kickstart','/unrelated/install.ks'],'parent':0,'started':'12'}}
    monkeypatch.setattr(purge,'process_table',lambda:table)
    assert set(_BUILDERS())=={1,2}


def test_identity_symlink_is_rejected_before_stop(environment):
    web=environment[1]
    outside=environment[0]/'identity';outside.write_text('secret')
    (web/'data/auth.db').symlink_to(outside)
    with pytest.raises(ValueError,match='Symlink'):purge.plan()
    assert outside.read_text()=='secret'
    assert not any('reset' in c or 'stop' in c for c in environment[-1])


def test_podman_resources_left_after_reset_prevent_disk_deletion(environment,monkeypatch):
    seed(environment);confirm();original=purge.run
    def incomplete(args,**kwargs):
        if 'reset' in args:return ''
        return original(args,**kwargs)
    monkeypatch.setattr(purge,'run',incomplete)
    with pytest.raises(RuntimeError,match='still contains resources'):purge.execute()
    assert (environment[2]/'12345678/disk').exists()
    assert purge.status()['state']=='failed'


def test_identity_storage_cannot_be_the_configured_vm_root(environment,monkeypatch):
    monkeypatch.setattr(vm,'storage_root',lambda:environment[1])
    with pytest.raises(ValueError,match='preserved'):purge.plan()


def test_builder_cancel_never_signals_a_reused_pid(environment,monkeypatch):
    # The fixture stubs cancellation; use the actual module source function.
    import importlib.util
    spec=importlib.util.spec_from_file_location('isolated_prune',Path(purge.__file__))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'builders',lambda:{123:{'started':'old'}})
    monkeypatch.setattr(module,'process_table',lambda:{123:{'started':'new'}})
    calls=[];monkeypatch.setattr(module.os,'kill',lambda *args:calls.append(args))
    module.cancel_builders()
    assert calls==[]
