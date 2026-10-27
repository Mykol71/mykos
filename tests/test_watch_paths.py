import os
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_watch_paths as watch
import mykos_criu as rpc


@pytest.fixture
def instance(tmp_path):
    proc=tmp_path/'proc';process=proc/'42';guest=tmp_path/'guest'
    for p in (process/'ns',process/'fdinfo',process/'fd',guest/'apps/example/bin',guest/'apps/example/cfg'):p.mkdir(parents=True,exist_ok=True)
    (process/'ns/pid').write_text('namespace')
    (process/'root').symlink_to(guest,target_is_directory=True)
    (process/'cwd').symlink_to('/apps/example/bin')
    (process/'fd/3').symlink_to('/apps/example/bin/server')
    def watched(path,number='7',dev=None):
        info=path.stat();device=watch.device_number(info.st_dev) if dev is None else dev
        (process/'fdinfo'/number).write_text(f'pos:\t0\ninotify wd:1 ino:{info.st_ino:x} sdev:{device:x} mask:3c6\n')
    return proc,process,guest,watched


def test_added_application_watches_resolved_without_application_paths(instance):
    proc,process,guest,watched=instance
    watched(guest/'apps/example/cfg')
    assert watch.watch_paths(42,proc)==['/apps/example/cfg']


def test_same_inode_wrong_device_is_not_accepted(instance):
    proc,process,guest,watched=instance
    watched(guest/'apps/example/cfg',dev=0xdeadbeef)
    assert watch.watch_paths(42,proc)==[]


def test_other_pid_namespace_watches_are_excluded(instance):
    proc,process,guest,watched=instance
    other=proc/'77';(other/'ns').mkdir(parents=True);(other/'fdinfo').mkdir()
    (other/'ns/pid').write_text('different namespace')
    path=guest/'apps/example/cfg';info=path.stat()
    (other/'fdinfo/8').write_text(f'inotify wd:1 ino:{info.st_ino:x} sdev:{watch.device_number(info.st_dev):x}\n')
    assert watch.watch_paths(42,proc)==[]


def test_guest_symlink_cannot_escape_to_host_files(instance,tmp_path):
    proc,process,guest,watched=instance
    outside=tmp_path/'outside';outside.mkdir();secret=outside/'private';secret.write_text('host sentinel')
    (guest/'escape').symlink_to(outside,target_is_directory=True)
    (process/'cwd').unlink();(process/'cwd').symlink_to('/escape')
    watched(secret)
    assert watch.watch_paths(42,proc)==[]
    assert secret.read_text()=='host sentinel'


def test_fallback_finds_watch_away_from_cwd_and_open_files(instance):
    proc,process,guest,watched=instance
    extra=guest/'custom-data/customer';extra.mkdir(parents=True)
    watched(extra)
    assert watch.watch_paths(42,proc)==['/custom-data/customer']


def test_scan_is_bounded_and_missing_process_is_harmless(instance):
    proc,process,guest,watched=instance;watched(guest/'apps/example/cfg')
    assert watch.watch_paths(42,proc,max_entries=0)==[]
    assert watch.watch_paths(42,proc,seconds=0)==[]
    assert watch.watch_paths(999,proc)==[]


def test_dump_rpc_hints_are_dynamic_deduplicated_and_restore_unchanged(monkeypatch):
    monkeypatch.setattr(watch,'watch_paths',lambda pid:['/apps/example/cfg','/usr/lib/NetworkManager'])
    request=b'\x08\x01\x12\x02\x10\x2a'
    result=rpc.directory_fds(request,os.getpid(),[])
    hints=[v for n,w,v,_ in rpc.wire_fields(rpc.fields(result)[2]) if n==36]
    assert hints[0]==b'/apps/example/cfg' and hints.count(b'/usr/lib/NetworkManager')==1
    assert rpc.directory_fds(result,os.getpid(),[])==result
    restore=b'\x08\x02\x12\x02\x10\x2a'
    assert rpc.directory_fds(restore,os.getpid(),[])==restore
