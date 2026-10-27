"""macOS interactive shell through its private VM bridge. Ubuntu uses serial."""
import ipaddress
import os
import re
import mykos_vm as vm


def destination(key, *, allow_image=False):
    value = vm.read(key)
    role = 'template' if allow_image else 'instance'
    if allow_image:
        from mykos_image_admin import base_container
        base_container(key)
    if value.get('role') != role or not value.get('os', '').startswith('mac'):
        raise ValueError('Only macOS guests use SSH terminals')
    state = vm.inspect(value['name'])
    labels = state.get('Config', {}).get('Labels') or {}
    if not state.get('State', {}).get('Running') or state.get('State', {}).get('Paused'):
        raise ValueError('Start the macOS VM before opening its shell')
    if labels.get('io.mykos.vm.os') != value['os'] or labels.get('io.mykos.vm.role') != role:
        raise ValueError('macOS VM identity mismatch')
    addresses = [n.get('IPAddress') for n in state.get('NetworkSettings', {}).get('Networks', {}).values() if n.get('IPAddress')]
    if len(addresses) != 1: raise ValueError('Expected one private Podman VM bridge address')
    address = ipaddress.IPv4Address(addresses[0])
    if not address.is_private or address.is_loopback or address.is_link_local:
        raise ValueError('Expected a private Podman VM bridge address')
    known = vm.storage(key) / 'ssh_known_hosts'
    if known.is_symlink(): raise ValueError('Unsafe SSH host-key file')
    return str(address), known


def ssh_arguments(address, known, user):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user):
        raise ValueError('Invalid macOS login name')
    return ['/usr/bin/ssh', '-F', '/dev/null', '-tt', '-o', 'ConnectTimeout=10',
            '-o', 'StrictHostKeyChecking=ask', '-o', 'UserKnownHostsFile=' + str(known),
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ClearAllForwardings=yes',
            '-o', 'PermitLocalCommand=no', '-o', 'PubkeyAuthentication=no',
            '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-l', user, address]


def open_terminal(key, *, allow_image=False):
    address, known = destination(key, allow_image=True) if allow_image else destination(key)
    print('macOS shell: enable Remote Login in guest System Settings > General > Sharing first.', flush=True)
    user = input('macOS login user: ').strip()
    args = ssh_arguments(address, known, user)
    if allow_image:
        import shlex
        from mykos_base_session import shell_command
        args+=['/bin/bash -c '+shlex.quote(shell_command(mac=True))]
    os.execv(args[0], args)
