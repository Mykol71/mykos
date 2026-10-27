"""Direct Ubuntu VM serial console. No guest network connection or wrapper shell."""
import fcntl
import os
from pathlib import Path
import selectors
import socket
import stat
import termios
import tty
import mykos_vm as vm
from mykos_host_monitoring import process_token


def serial_path(key, *, allow_image=False):
    value = vm.read(key)
    role = 'template' if allow_image else 'instance'
    if allow_image:
        from mykos_image_admin import base_container
        base_container(key)
    if value.get('role') != role or not value.get('os', '').startswith('ubuntu'):
        raise ValueError('Only Ubuntu guests use this serial terminal')
    state = vm.inspect(value['name'])
    labels = state.get('Config', {}).get('Labels') or {}
    if not state.get('State', {}).get('Running') or state.get('State', {}).get('Paused'):
        raise ValueError('Start the Ubuntu VM before opening its terminal')
    if labels.get('io.mykos.vm.os') != value['os'] or labels.get('io.mykos.vm.role') != role:
        raise ValueError('Ubuntu VM identity mismatch')
    pid = state['State']['Pid']
    token = process_token(pid)
    path = Path('/proc/%d/root/dev/shm/mykos-serial.sock' % pid)
    for part in (path.parent.parent, path.parent, path):
        if part.is_symlink(): raise ValueError('Unsafe serial socket path')
    if not path.exists() or not stat.S_ISSOCK(path.stat().st_mode):
        raise ValueError('Direct serial console unavailable; recreate the Ubuntu image with current console support')
    if process_token(pid) != token: raise ValueError('Ubuntu VM restarted; reconnect')
    return path, pid, token


def open_terminal(key, *, allow_image=False):
    path, pid, token = serial_path(key, allow_image=True) if allow_image else serial_path(key)
    marker=None
    if allow_image:
        import secrets
        marker='MYKOSBASELOGOUT'+secrets.token_hex(16)
    lock = os.open(vm.storage(key)/'.serial-console.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('This Ubuntu console is already open; close its other terminal first')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as serial:
            serial.settimeout(10); serial.connect(str(path)); serial.settimeout(None)
            if process_token(pid) != token: raise ValueError('Ubuntu VM restarted; reconnect')
            print('Direct Ubuntu guest console: su - root. If blank, run Ubuntu image setup --console-only in the guest before capture.', flush=True)
            original = termios.tcgetattr(0)
            try:
                tty.setraw(0)
                serial.sendall(b'\r')
                if allow_image:
                    from mykos_base_session import shell_command
                    serial.sendall((shell_command(logout_marker=marker)+'\r').encode())
                    serial.settimeout(10)
                    startup=b'';banner=b'BASE IMAGE:'
                    while banner not in startup:
                        part=serial.recv(4096)
                        if not part:raise ValueError('Guarded base console disconnected')
                        startup+=part
                        if len(startup)>32768:raise ValueError('Could not start a guarded base console')
                    os.write(1,startup[startup.index(banner):]);serial.settimeout(None)
                else:
                    # A memory image can contain the base console process. Replace
                    # that login shell so ID-specific guests do not inherit its guards.
                    serial.sendall(b'trap - EXIT; exec /bin/su - root\r')
                output=b''
                with selectors.DefaultSelector() as selector:
                    selector.register(0, selectors.EVENT_READ)
                    selector.register(serial, selectors.EVENT_READ)
                    while True:
                        for event,_ in selector.select():
                            if event.fileobj == 0:
                                data = os.read(0,4096)
                                if not data: return
                                serial.sendall(data)
                            else:
                                data = serial.recv(4096)
                                if not data: return
                                if marker:
                                    output+=data
                                    token=marker.encode()
                                    if token in output:
                                        os.write(1,output.split(token,1)[0]);return
                                    keep=0
                                    for length in range(1,min(len(output),len(token)-1)+1):
                                        if output.endswith(token[:length]):keep=length
                                    if keep:
                                        os.write(1,output[:-keep]);output=output[-keep:]
                                    else:os.write(1,output);output=b''
                                else:os.write(1,data)
            finally: termios.tcsetattr(0,termios.TCSANOW,original)
    finally: os.close(lock)
