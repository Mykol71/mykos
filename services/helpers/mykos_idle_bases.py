"""Retire idle captured-image sandboxes, never customer instances or images."""
import contextlib
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

import mykos_image_admin as images
import mykos_vm as vm
from mykos_runtime import PODMAN, NATIVE_OS_NAMES, run

IDLE_SECONDS = 300


def touch(os_name):
    images.selection(os_name)
    vm.safe_dir(images.STATE)
    path = images.STATE / (os_name + '.activity')
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try: os.utime(fd, None)
    finally: os.close(fd)


def open_base(os_name):
    """Restore an idle base on demand; no installation or image rebuild."""
    images.selection(os_name)
    with vm.locked():
        if images.job(os_name).get('state') in ('queued', 'deleting', 'resetting'):
            raise ValueError('An image operation is in progress')
        touch(os_name)
        try: state = vm.inspect(os_name)
        except RuntimeError:
            # A real inspection failure must not be mistaken for an absent base.
            matches = json.loads(run([PODMAN, 'ps', '-a', '--filter', 'name=^' + os_name + '$', '--format', 'json'], timeout=20))
            if matches: raise
            state = None
        if state and state.get('State', {}).get('Running'):
            if os_name in vm.VERSIONS:
                value = vm.read(os_name)
                if not value.get('ready') or value.get('pending_commit'):
                    # Installation consoles remain available; the idle worker
                    # independently excludes every unfinished image.
                    return
            images.base_container(os_name)
            return
        # An unfinished VM is never reset or started by idle management.
        if os_name in vm.VERSIONS:
            value = vm.read(os_name)
            if not value.get('ready') or not value.get('committed') or value.get('pending_commit'):
                raise ValueError('Finish the image build before opening its captured base')
        else:
            # Verify the saved tag exists before attempting a native launch.
            json.loads(run([PODMAN, 'image', 'inspect', images.reference(os_name)], timeout=20))[0]
    # reset_base owns the same registry lock, so call it outside the lock above.
    # It restores the saved golden state rather than booting leftover working files.
    images.reset_base(os_name, only_if_stopped=True)
    touch(os_name)


@contextlib.contextmanager
def available_registry():
    """A periodic cleanup must not wait behind or interrupt a capture/restore."""
    vm.safe_dir(vm.ROOT)
    fd = os.open(vm.ROOT / 'registry.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            from mykos_host_maintenance import admission
            try:admission()
            except ValueError:yield False
            else:yield True
    finally: os.close(fd)


def connected_ports():
    # Includes proxied WebSockets, direct noVNC, RDP, VNC and SSH connections.
    text = run(['/usr/sbin/ss', '-Htn', 'state', 'established'], timeout=10)
    return {int(token.rsplit(':', 1)[1]) for token in text.split()
            if ':' in token and token.rsplit(':', 1)[1].isdigit()}


def busy(os_name):
    import mykos_prune as prune
    state = run(['/usr/bin/systemctl', 'show', 'mykos-image-build@' + os_name + '.service', '-p', 'ActiveState', '--value'], timeout=10)
    if state.strip() in ('active', 'activating', 'deactivating', 'reloading'): return True
    if images.job(os_name).get('state') in ('queued', 'deleting', 'resetting'): return True
    for process in prune.process_table().values():
        args = process['args']
        from mykos_runtime import native_build_os
        if native_build_os(args) == os_name: return True
        if any(Path(arg).name == 'pos' for arg in args[:3]) and os_name in args: return True
        if any(Path(arg).name == 'podman' for arg in args[:2]):
            if any(arg in ('build', 'commit', 'exec', 'attach', 'checkpoint', 'restore') for arg in args[1:3]):
                if any(arg in (os_name, os_name + ':latest', images.reference(os_name)) for arg in args): return True
    return False


def eligible(os_name, base, now, ports):
    if base.get('Name', '').lstrip('/') != os_name: return False
    state = base.get('State', {})
    if not state.get('Running') or state.get('Paused'): return False
    labels = base.get('Config', {}).get('Labels') or {}
    if labels.get('io.mykos.persistent') == 'true': return False
    image = json.loads(run([PODMAN, 'image', 'inspect', images.reference(os_name)], timeout=20))[0]
    ident = lambda value: str(value or '').removeprefix('sha256:')
    if os_name in vm.VERSIONS:
        value = vm.read(os_name)
        if (value.get('name') != os_name or value.get('role') != 'template' or
            not value.get('ready') or not value.get('committed') or value.get('pending_commit') or
            labels.get('io.mykos.vm.os') != os_name or labels.get('io.mykos.vm.role') != 'template' or
            ident(value.get('image')) != ident(image['Id'])): return False
        vm.restore_manifest(value, vm.storage(os_name) / 'golden')
    else:
        if labels.get('io.mykos.os') != os_name or base.get('ImageName') != images.reference(os_name): return False
        # Native commit may retag a child while its generic base uses the parent.
        if ident(base.get('Image')) not in (ident(image['Id']), ident(image.get('Parent'))): return False
    started = images.produced_at(state.get('StartedAt'))
    if started is None: return False
    activity = images.STATE / (os_name + '.activity')
    if activity.is_symlink(): raise ValueError('Unsafe base activity file')
    last = max(started, activity.stat().st_mtime if activity.exists() else started)
    if now - last < IDLE_SECONDS: return False
    mapped = base.get('NetworkSettings', {}).get('Ports') or {}
    bound = {int(p['HostPort']) for group in mapped.values() for p in (group or [])}
    if ports & bound:
        touch(os_name)
        return False
    return not busy(os_name)


def sweep(*, dry_run=False, now=None):
    from mykos_base_session import acquire
    now = time.time() if now is None else now
    result = {'stopped': [], 'eligible': [], 'errors': {}}
    # Fail closed if connection inspection is unavailable.
    connected_ports()
    # One inventory read per sweep; inspect only running, exact supported names.
    rows = json.loads(run([PODMAN, 'ps', '--format', 'json'], timeout=20))
    names = set()
    for row in rows:
        value = row.get('Names') or []
        names.update([value] if isinstance(value, str) else value)
    for os_name in (*NATIVE_OS_NAMES, *vm.VERSIONS):
        if os_name not in names: continue
        session = None
        try:
            session = acquire(os_name, operation=True)
            with available_registry() as locked:
                if not locked: return result
                base = vm.inspect(os_name)
                if not eligible(os_name, base, now, connected_ports()): continue
                result['eligible'].append(os_name)
                if not dry_run:
                    # Stop only this verified ID. Saved images, disks and snapshots stay.
                    run([PODMAN, 'stop', '--time', '30', base['Id']], timeout=45)
                    result['stopped'].append(os_name)
        except ValueError as error:
            if 'already has an open console' not in str(error): result['errors'][os_name] = str(error)
        except (RuntimeError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
            result['errors'][os_name] = str(error)
        finally:
            if session is not None: os.close(session)
    return result
