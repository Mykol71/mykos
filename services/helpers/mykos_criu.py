"""Scoped CRIU RPC relay: copy disks at post-dump before tasks can resume.

RHEL CRIU 3.x rejects shell action-scripts together with runc RPC callbacks.
Keep the vendor binary and relay its existing protocol, including SCM_RIGHTS.
Only this snapshot operation puts the private launcher on PATH.
"""
import array
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import struct
import stat
import sys

LIMIT = 2 * 1024 * 1024


def wire_fields(data):
    """Read protobuf wire fields without changing/serializing the RPC payload."""
    offset = 0
    def varint():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(data): raise ValueError('Truncated CRIU RPC field')
            byte = data[offset]; offset += 1; value |= (byte & 127) << shift
            if byte < 128: return value
        raise ValueError('Invalid CRIU RPC integer')
    result = []
    while offset < len(data):
        start = offset
        tag = varint(); number, wire = tag >> 3, tag & 7
        if not number: raise ValueError('Invalid CRIU RPC tag')
        if wire == 0: value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else (8 if wire == 1 else 4)
            if length > len(data) - offset: raise ValueError('Truncated CRIU RPC data')
            value = data[offset:offset + length]; offset += length
        else: raise ValueError('Unsupported CRIU RPC field')
        result.append((number,wire,value,data[start:offset]))
    return result


def fields(data):
    return {number:value for number,_,value,_ in wire_fields(data)}


def integer(value):
    encoded = bytearray()
    while value > 127:
        encoded.append((value & 127) | 128); value >>= 7
    return bytes(encoded) + bytes([value])


def replace_fields(data, replacements):
    result = []
    for number,wire,value,raw in wire_fields(data):
        if number not in replacements: result.append(raw); continue
        value = replacements[number]
        if wire == 0: raw = integer(number << 3) + integer(value)
        elif wire == 2: raw = integer((number << 3) | 2) + integer(len(value)) + value
        else: raise ValueError('Invalid CRIU field replacement')
        result.append(raw)
    return b''.join(result)


def enable_dump_notifications(data):
    """Request callbacks when libcriu's caller (crun) did not register any."""
    options=fields(data)[2]
    if 12 in fields(options):options=replace_fields(options,{12:1})
    else:options+=integer(12<<3)+b'\x01'
    return replace_fields(data,{2:options})


def directory_fds(packet, peer_pid, retained):
    # CRIU opens images_dir_fd/work_dir_fd through /proc/<RPC peer>/fd.
    # The relay is now that peer; retain equivalent directory descriptors here.
    request = fields(packet)
    if 2 not in request: return packet
    options = fields(request[2]); changes = {}
    for number in (1,17):
        fd = options.get(number)
        if isinstance(fd,int) and 0 <= fd < 2**31:
            copied = os.open('/proc/%d/fd/%d' % (peer_pid,fd),os.O_RDONLY | os.O_DIRECTORY)
            retained.append(copied);changes[number] = copied
    options_data = replace_fields(request[2], changes) if changes else request[2]
    # criu_opts.irmap_scan_paths is repeated string field 36. Add the missing
    # D-Bus/NetworkManager directories only to DUMP requests; preserve runtime options.
    # RPC keeps the path storage alive on older CRIU config-parser versions.
    if request.get(1) == 1:
        existing = [v for n, w, v, _ in wire_fields(options_data) if n == 36 and w == 2]
        from mykos_watch_paths import watch_paths
        dynamic=[os.fsencode(path) for path in watch_paths(options.get(2))]
        for hint in (*dynamic, b'/usr/share/dbus-1/system.d', b'/usr/lib/NetworkManager', b'/usr/lib/firmware', b'/usr/share/polkit-1'):
            if hint not in existing:
                options_data += integer((36 << 3) | 2) + integer(len(hint)) + hint
                existing.append(hint)
    return replace_fields(packet, {2: options_data})


def receive(sock):
    data, ancillary, flags, _ = sock.recvmsg(LIMIT, socket.CMSG_SPACE(256 * array.array('i').itemsize))
    descriptors = []
    for level, kind, value in ancillary:
        if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
            raise ValueError('Unexpected CRIU socket control data')
        fds = array.array('i'); fds.frombytes(value); descriptors.extend(fds)
    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        for fd in descriptors: os.close(fd)
        raise ValueError('Oversized CRIU RPC packet')
    return data, ancillary, descriptors


def relay(client, worker, pid, callback, peer_pid=None, retained=None, cgroup=None):
    dumping = False; captured = False; own_notifications = False
    if retained is None: retained = []
    while True:
        ready, _, _ = select.select([client, worker], [], [], 900)
        if not ready: raise TimeoutError('CRIU RPC timed out')
        for source in ready:
            data, ancillary, descriptors = receive(source)
            try:
                if not data: return
                message = fields(data)
                if source is worker and cgroup is not None:cgroup.notification(message)
                if source is client and message.get(1) == 1:
                    if fields(message.get(2, b'')).get(2) != pid:
                        raise ValueError('CRIU dump target does not match snapshot')
                    dumping = True
                    own_notifications=not bool(fields(message.get(2,b'')).get(12))
                    if own_notifications:data=enable_dump_notifications(data)
                if (source is worker and dumping and message.get(1) == 6 and
                        fields(message.get(5, b'')).get(1) == b'post-dump'):
                    if captured: raise ValueError('Duplicate CRIU post-dump')
                    # runc/CRIU remain blocked waiting for this notification/ACK.
                    # On failure closing the RPC aborts the dump; no snapshot is published.
                    try: callback()
                    except Exception:
                        # Reject this notification, but keep forwarding RPC so
                        # runc can unlock networking and CRIU can resume tasks.
                        worker.send(b'\x08\x06\x18\x00')
                        captured = True
                        continue
                    captured = True
                if source is worker and dumping and own_notifications and message.get(1)==6:
                    action=fields(message.get(5,b'')).get(1)
                    # crun does not register dump callbacks. Acknowledge the
                    # lifecycle notifications here after the filesystem copy;
                    # CRIU still performs its normal internal network locking.
                    # Never swallow descriptor handoffs or unknown actions.
                    allowed={b'pre-dump',b'post-dump',b'network-lock',b'network-unlock',
                             b'pre-resume',b'post-resume',b'status-ready',b'query-ext-files'}
                    success=action in allowed and not descriptors
                    worker.send(b'\x08\x06\x18'+bytes([int(success)]))
                    if not success:raise ValueError('Unsupported CRIU dump notification: '+repr(action))
                    continue
                if source is client and peer_pid is not None:
                    data = directory_fds(data,peer_pid,retained)
                if source is client and cgroup is not None:data=cgroup.request(data)
                destination = worker if source is client else client
                if destination.sendmsg([data], ancillary) != len(data):
                    raise RuntimeError('Incomplete CRIU RPC packet')
            finally:
                for fd in descriptors: os.close(fd)


def main():
    real = os.environ['MYKOS_CRIU_BINARY']
    from mykos_criu_binary import checked
    real=checked(real)
    args = sys.argv[1:]
    if len(args) != 2 or args[0] != 'swrk': os.execv(real, [real] + args)
    context = Path(os.environ['MYKOS_CRIU_CONTEXT'])
    status=context.lstat()
    if not stat.S_ISREG(status.st_mode) or status.st_uid!=0 or status.st_mode & 0o077:
        raise ValueError('CRIU context must be a private root-owned regular file')
    value=json.loads(context.read_text());operation=value.get('operation','capture')
    if operation not in ('capture','restore'):raise ValueError('Unexpected CRIU operation')
    cgroup=None
    if value.get('cgroup'):
        from mykos_criu_cgroups import Transaction
        cgroup=Transaction(value['cgroup'],operation)
    pid = int(os.environ['MYKOS_CRIU_PID'])
    hook = Path(__file__).with_name('mykos-criu-hook')
    loader = importlib.machinery.SourceFileLoader('mykos_criu_hook', str(hook))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    def capture():
        try: module.capture_files(context)
        except Exception as error:
            marker=context.parent/'hook.error'
            marker.write_text(str(error));marker.chmod(0o600)
            raise
    client = socket.socket(fileno=int(args[1]))
    peer_pid,peer_uid,_ = struct.unpack('3i',client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    if peer_uid != 0: raise ValueError('CRIU runtime peer must be root')
    retained = []
    worker, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    # RPC options refer to inherited fd numbers (images directory, external namespaces).
    inherited = []
    for entry in Path('/proc/self/fd').iterdir():
        fd = int(entry.name)
        try:
            if fd > 2 and fd not in (client.fileno(), worker.fileno()):
                os.fstat(fd); inherited.append(fd)
        except OSError: pass
    process = subprocess.Popen([real, 'swrk', str(child.fileno())], pass_fds=tuple(inherited))
    if cgroup is not None:cgroup.worker=process.pid;cgroup.peer=peer_pid
    child.close()
    try:
        relay(client, worker, pid, capture, peer_pid, retained,cgroup)
        if cgroup is not None and operation=='restore' and cgroup.succeeded and not cgroup.prepared:
            raise ValueError('CRIU did not prepare the saved cgroup controllers')
    except BaseException as error:
        marker = context.parent/'hook.error'
        marker.write_text(str(error)); marker.chmod(0o600)
        raise
    finally:
        client.close(); worker.close()
        for fd in retained: os.close(fd)
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate(); process.wait(timeout=15)
        if cgroup is not None:cgroup.close()
    if process.returncode: raise SystemExit(process.returncode)


if __name__ == '__main__': main()
