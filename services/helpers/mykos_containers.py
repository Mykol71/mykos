"""Explicit deletion of one container and its private MYKOS data.

Shared mounts, rsync backups, OS images and intentional snapshots are retained.
"""
import json
import os
from pathlib import Path
import re
import shutil

import mykos_vm as vm
from mykos_runtime import shopcode, target


def checked_path(path):
    """Fail before stopping anything if a managed cleanup path is unsafe."""
    path = Path(path)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('Symlink cleanup path rejected: ' + str(path))
    if not path.exists():
        return False
    status = path.stat()
    if status.st_uid != os.geteuid() or status.st_mode & 0o022:
        raise ValueError('Managed data must be operator-owned and not group/world writable: ' + str(path))
    if path.is_dir():
        for directory, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                item = Path(directory) / name
                if item.is_symlink():
                    raise ValueError('Symlink inside managed data rejected: ' + str(item))
    elif not path.is_file():
        raise ValueError('Expected a regular managed file: ' + str(path))
    return True


def all_containers():
    ids = vm.run([vm.PODMAN, 'ps', '-a', '--no-trunc', '--format', '{{.ID}}']).splitlines()
    if not ids:
        return []
    if any(not re.fullmatch(r'[0-9a-f]{64}', cid) for cid in ids):
        raise ValueError('Invalid container identity returned by Podman')
    return json.loads(vm.run([vm.PODMAN, 'container', 'inspect', *ids], timeout=60))


def unshared(data, containers, selected_id):
    for other in containers:
        if other.get('Id') == selected_id:
            continue
        for mount in other.get('Mounts', []):
            if not mount.get('Source'):
                continue
            source = Path(mount['Source']).resolve()
            if any(source == p or source in p.parents or p in source.parents for p in data):
                raise ValueError('Private storage is also mounted by another container: ' + other.get('Name', other.get('Id', '?')))


def selection(shop, confirmation, expected_id=None):
    shopcode(shop)
    if confirmation != shop:
        raise ValueError('Confirm the exact container ID before deletion')
    desktop = vm.record_path(shop).is_file()
    if desktop:
        value = vm.read(shop)
        if value.get('role') != 'instance' or value.get('os') not in vm.VERSIONS:
            raise ValueError('Only code-specific containers can be deleted')
        name = value['name']
        if not re.fullmatch(re.escape(shop) + r'(?:\.[A-Za-z0-9_.-]+)?', name):
            raise ValueError('Container identity mismatch')
    else:
        name = target(shop)
    state = vm.inspect(name)
    cid = state.get('Id', '')
    if not re.fullmatch(r'[0-9a-f]{64}', cid) or (expected_id is not None and expected_id != cid):
        raise ValueError('Container identity changed; refresh before deleting')
    if state.get('Name', '').lstrip('/') != name:
        raise ValueError('Container identity mismatch')
    labels = state.get('Config', {}).get('Labels') or {}
    if desktop:
        if labels.get('io.mykos.vm.role') != 'instance' or labels.get('io.mykos.vm.os') != value['os']:
            raise ValueError('Container is not the selected MYKOS instance')
    elif labels.get('io.mykos.vm.os'):
        raise ValueError('VM metadata missing; retained storage must be inspected first')

    # Only these private locations are MYKOS-owned container data. Other mounts
    # (especially /backups and named volumes) are never recursively removed.
    private_roots = [vm.POS]
    data = [vm.POS / ('.' + shop)]
    packaged_pos = Path('/usr/share/mykos/pos')
    mounted = {Path(m['Source']) for m in state.get('Mounts', []) if m.get('Source')}
    if packaged_pos != vm.POS and packaged_pos / ('.' + shop) in mounted:
        data.append(packaged_pos / ('.' + shop))
        private_roots.append(packaged_pos)
    if desktop:
        if not any(m.get('Destination') == '/storage' and Path(m.get('Source', '')) == vm.storage(shop) / 'storage' for m in state.get('Mounts', [])):
            raise ValueError('VM storage mount does not match managed storage; data was left intact')
        data.append(vm.storage(shop))
    metadata = [vm.record_path(shop), vm.POS / '.linux' / (shop + '.json')]
    metadata += [p for root in private_roots for p in root.glob('.' + shop + '.*') if re.fullmatch(r'\.' + shop + r'\.[A-Za-z0-9_-]+', p.name)]
    data = [p for p in data if checked_path(p)]
    metadata = [p for p in metadata if checked_path(p)]
    unshared(data, all_containers(), cid)
    retained = [m['Source'] for m in state.get('Mounts', []) if m.get('Source') and
                not any(Path(m['Source']) == p or p in Path(m['Source']).parents for p in data)]
    return dict(shop=shop, name=name, container_id=cid, state=state, data=data,
                metadata=metadata, retained_mounts=retained)


def delete(shop, confirmation, expected_id=None):
    with vm.locked():
        item = selection(shop, confirmation, expected_id)
        from mykos_vpn_gateway import require_unreferenced
        require_unreferenced(shop)
        cid = item['container_id']
        state = item['state']
        if state.get('State', {}).get('Running'):
            if state.get('HostConfig', {}).get('AutoRemove'):
                vm.run([vm.PODMAN, 'stop', '--time', '120', cid], timeout=150)
            else:
                vm.stop_container(cid)
        remaining = {c['Id']: c for c in all_containers()}
        if cid in remaining:
            current = remaining[cid]
            if current.get('State', {}).get('Running') or current.get('Name', '').lstrip('/') != item['name']:
                raise ValueError('Container changed or did not stop; data was left intact')
            vm.run([vm.PODMAN, 'rm', cid], timeout=60)
        survivors = all_containers()
        if any(c.get('Id') == cid for c in survivors):
            raise RuntimeError('Podman did not remove the container; data was left intact')
        unshared(item['data'], survivors, cid)
        # Never use --force, --volumes, image rm, or paths supplied by the caller.
        for path in item['data'] + item['metadata']:
            if checked_path(path):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        return dict(shop=shop, state='deleted', container_id=cid,
                    removed_data=[str(p) for p in item['data']],
                    retained_mounts=item['retained_mounts'],
                    snapshots_retained=True, images_retained=True)
