import copy
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/helpers'))
import mykos_idle_bases as idle
import mykos_image_admin as images
import mykos_prune as prune
import mykos_runtime as runtime
import mykos_vm as vm


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(images, 'STATE', tmp_path / 'state')
    monkeypatch.setattr(vm, 'ROOT', tmp_path / 'vm')
    monkeypatch.setattr(idle, 'NATIVE_OS_NAMES', ('rh9',))
    monkeypatch.setattr(vm, 'VERSIONS', {})
    monkeypatch.setattr(images, 'job', lambda name: {'state': 'idle'})
    monkeypatch.setattr(prune, 'process_table', lambda: {})
    base = {'Id': 'base-id', 'Name': '/rh9', 'Image': 'parent',
            'ImageName': 'localhost/rh9:latest',
            'State': {'Running': True, 'StartedAt': '1970-01-01T00:00:01Z'},
            'Config': {'Labels': {'io.mykos.os': 'rh9'}},
            'NetworkSettings': {'Ports': {}}}
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ['image', 'inspect']:
            return json.dumps([{'Id': 'captured', 'Parent': 'parent'}])
        if args[1] == 'ps': return json.dumps([{'Names': [base['Name'].lstrip('/')]}])
        if args[0].endswith('systemctl'): return 'inactive'
        return ''
    monkeypatch.setattr(idle, 'run', run)
    monkeypatch.setattr(vm, 'inspect', lambda name: copy.deepcopy(base))
    return base, calls


def stopped(calls):
    return [args for args in calls if args[1:2] == ['stop']]


def test_idle_native_stops_only_verified_id_and_never_removes_images(sandbox):
    base, calls = sandbox
    assert idle.sweep(now=1000)['stopped'] == ['rh9']
    assert stopped(calls) == [[runtime.PODMAN, 'stop', '--time', '30', 'base-id']]
    assert not any(any(arg in ('rm', 'rmi', '--force', 'prune') for arg in a) for a in calls)


@pytest.mark.parametrize('change', ['instance', 'wrong-label', 'persistent', 'wrong-image', 'paused', 'stopped', 'recent', 'unknown-age'])
def test_unsafe_or_active_candidates_stay_running(sandbox, change):
    base, calls = sandbox
    if change == 'instance': base['Name'] = '/12345678.prod.myk.sh'
    if change == 'wrong-label': base['Config']['Labels']['io.mykos.os'] = 'rh8'
    if change == 'persistent': base['Config']['Labels']['io.mykos.persistent'] = 'true'
    if change == 'wrong-image': base['Image'] = 'different'
    if change == 'paused': base['State']['Paused'] = True
    if change == 'stopped': base['State']['Running'] = False
    if change == 'recent': base['State']['StartedAt'] = '1970-01-01T00:15:00Z'
    if change == 'unknown-age': base['State'].pop('StartedAt')
    assert idle.sweep(now=1000)['stopped'] == []
    assert not stopped(calls)


def test_open_terminal_lock_prevents_stop(sandbox):
    from mykos_base_session import acquire
    _, calls = sandbox
    fd = acquire('rh9')
    try: assert idle.sweep(now=1000)['stopped'] == []
    finally: os.close(fd)
    assert not stopped(calls)


def test_capture_or_restore_registry_lock_prevents_stop(sandbox):
    _, calls = sandbox
    with vm.locked(): assert idle.sweep(now=1000)['stopped'] == []
    assert not stopped(calls)


def test_live_console_connection_and_recent_console_activity_prevent_stop(sandbox, monkeypatch):
    base, calls = sandbox
    base['NetworkSettings']['Ports'] = {'8006/tcp': [{'HostPort': '18012'}]}
    monkeypatch.setattr(idle, 'connected_ports', lambda: {18012})
    assert not idle.sweep()['stopped']
    monkeypatch.setattr(idle, 'connected_ports', lambda: set())
    assert not idle.sweep()['stopped']
    assert not stopped(calls)


@pytest.mark.parametrize('args', [
    ['podman', 'commit', 'rh9', 'localhost/rh9:latest'],
    ['podman', 'exec', '-it', 'rh9', 'bash'],
    ['podman', 'build', '-t', 'rh9', '.'],
    ['livemedia-creator', '--ks=/var/tmp/rh9.ks'],
    ['bash', './pos', 'rh9'],
])
def test_build_capture_or_direct_exec_prevents_stop(sandbox, monkeypatch, args):
    _, calls = sandbox
    monkeypatch.setattr(prune, 'process_table', lambda: {1: {'args': args}})
    assert not idle.sweep(now=1000)['stopped']
    assert not stopped(calls)


def test_pending_image_operation_prevents_stop(sandbox, monkeypatch):
    _, calls = sandbox
    monkeypatch.setattr(images, 'job', lambda name: {'state': 'resetting'})
    assert not idle.sweep(now=1000)['stopped']
    assert not stopped(calls)


def test_dry_run_leaves_base_running(sandbox):
    _, calls = sandbox
    result = idle.sweep(now=1000, dry_run=True)
    assert result['eligible'] == ['rh9'] and not result['stopped']
    assert not stopped(calls)


def test_connection_inspection_failure_fails_closed(sandbox, monkeypatch):
    _, calls = sandbox
    def failed(): raise RuntimeError('ss failed')
    monkeypatch.setattr(idle, 'connected_ports', failed)
    with pytest.raises(RuntimeError): idle.sweep(now=1000)
    assert not stopped(calls)


def test_idle_vm_requires_finished_capture_and_preserves_disks(sandbox, monkeypatch):
    base, calls = sandbox
    monkeypatch.setattr(idle, 'NATIVE_OS_NAMES', ())
    monkeypatch.setattr(vm, 'VERSIONS', {'win11': ('windows', '11')})
    base.update(Name='/win11', Image='captured')
    base['Config']['Labels'] = {'io.mykos.vm.os': 'win11', 'io.mykos.vm.role': 'template'}
    value = {'name': 'win11', 'role': 'template', 'ready': True, 'committed': True, 'image': 'captured'}
    monkeypatch.setattr(vm, 'read', lambda name: value)
    monkeypatch.setattr(vm, 'restore_manifest', lambda *a: {})
    monkeypatch.setattr(vm, 'storage', lambda name: Path('/unused-test-storage'))
    value['pending_commit'] = True
    assert not idle.sweep(now=1000)['stopped']
    value['pending_commit'] = False
    assert idle.sweep(now=1000)['stopped'] == ['win11']
    assert stopped(calls) == [[runtime.PODMAN, 'stop', '--time', '30', 'base-id']]


def test_reopening_stopped_base_uses_captured_state(sandbox, monkeypatch):
    base, _ = sandbox
    base['State']['Running'] = False
    reset = []
    monkeypatch.setattr(images, 'reset_base', lambda *a, **kw: reset.append((a, kw)))
    idle.open_base('rh9')
    assert reset == [(('rh9',), {'only_if_stopped': True})]
    assert (images.STATE / 'rh9.activity').exists()


def test_unfinished_running_vm_console_stays_available(sandbox, monkeypatch):
    base, _ = sandbox
    monkeypatch.setattr(vm, 'VERSIONS', {'win11': ('windows', '11')})
    monkeypatch.setattr(vm, 'read', lambda name: {'ready': False})
    monkeypatch.setattr(images, 'reset_base', lambda *a, **kw: pytest.fail('Must not reset a builder'))
    idle.open_base('win11')


def test_native_image_remains_startable_without_base_container(monkeypatch):
    monkeypatch.setattr(vm, 'prepared_templates', lambda: [])
    def run(args):
        return json.dumps([] if args[1] == 'ps' else [{'Names': ['localhost/rh9:latest']}])
    monkeypatch.setattr(runtime, 'run', run)
    assert runtime.stage_images() == [{'os': 'rh9', 'image': 'localhost/rh9:latest'}]


def test_concurrent_console_open_does_not_reset_base_already_reopened(sandbox, monkeypatch):
    import subprocess
    import mykos_containers as containers
    base, calls = sandbox
    monkeypatch.setattr(images, 'run', idle.run)
    monkeypatch.setattr(images.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 3, 'inactive', ''))
    monkeypatch.setattr(containers, 'all_containers', lambda: [base])
    result = images.reset_base('rh9', only_if_stopped=True)
    assert result['base_running'] is True
    assert not stopped(calls)
    assert not any(args[1] in ('rm', 'run') for args in calls)
