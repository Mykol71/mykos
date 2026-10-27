"""CRIU relay protocol tests use local sockets, never containers or guests."""
import array
import os
from pathlib import Path
import socket
import sys
import threading
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_criu as rpc

# criu_req DUMP, opts.pid=42; criu_resp NOTIFY, success, notify.script.
DUMP=b'\x08\x01\x12\x02\x10\x2a'
NOTIFY=b'\x08\x06\x10\x01\x2a\x0b\x0a\x09post-dump'
ACK=b'\x08\x06\x18\x01'
DONE=b'\x08\x01\x10\x01'


@pytest.mark.skipif(sys.platform != 'linux', reason='CRIU uses Linux Unix seqpacket sockets')
def test_relay_copies_before_post_dump_and_preserves_descriptors():
    runtime,client=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    criu,worker=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    for sock in (runtime,client,criu,worker):sock.settimeout(2)
    captured=[];errors=[]
    def run():
        try:rpc.relay(client,worker,42,lambda:captured.append(True))
        except BaseException as error:errors.append(error)
    thread=threading.Thread(target=run);thread.start()
    read,write=os.pipe()
    try:
        runtime.send(DUMP);assert criu.recv(1024)==DUMP
        criu.sendmsg([NOTIFY],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[read]))])
        data,ancillary,fds=rpc.receive(runtime)
        assert captured==[True] and data==NOTIFY and len(fds)==1
        os.write(write,b'preserved descriptor');assert os.read(fds[0],32)==b'preserved descriptor'
        for fd in fds:os.close(fd)
        runtime.send(ACK);assert criu.recv(1024)==ACK
        criu.send(DONE);assert runtime.recv(1024)==DONE
    finally:
        runtime.close();thread.join(3)
        for sock in (client,criu,worker):sock.close()
        os.close(read);os.close(write)
    assert not thread.is_alive() and not errors


@pytest.mark.skipif(sys.platform != 'linux', reason='CRIU uses Linux Unix seqpacket sockets')
def test_relay_rejects_wrong_target_without_dumping():
    runtime,client=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    criu,worker=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    try:
        runtime.send(DUMP)
        with pytest.raises(ValueError,match='target'):rpc.relay(client,worker,43,lambda:pytest.fail('Unexpected capture'))
    finally:
        for sock in (runtime,client,criu,worker):sock.close()


@pytest.mark.parametrize('data',[b'\x12\xff',b'\x12\x05a',b'\x08'+b'\x80'*11,b'\x0b'])
def test_relay_rejects_malformed_wire_data(data):
    with pytest.raises(ValueError):rpc.fields(data)


def test_replacement_preserves_unknown_and_repeated_fields():
    original=b'\x08\x01\x12\x01a\x12\x01b\x18\x09'
    rewritten=rpc.replace_fields(original,{1:300})
    assert rewritten==b'\x08\xac\x02\x12\x01a\x12\x01b\x18\x09'


@pytest.mark.skipif(sys.platform != 'linux', reason='CRIU directory descriptors use Linux procfs')
def test_runtime_directory_descriptors_survive_rpc_peer_change(tmp_path):
    fd=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY);retained=[]
    options=b'\x08'+rpc.integer(fd)+b'\x10\x2a'+rpc.integer(17<<3)+rpc.integer(fd)
    request=b'\x08\x01\x12'+rpc.integer(len(options))+options
    try:
        updated=rpc.fields(rpc.fields(rpc.directory_fds(request,os.getpid(),retained))[2])
        assert updated[2]==42 and len(retained)==2
        for number in (1,17):assert os.fstat(updated[number]).st_ino==os.fstat(fd).st_ino
    finally:
        os.close(fd)
        for copied in retained:os.close(copied)


@pytest.mark.skipif(sys.platform != 'linux', reason='CRIU uses Linux Unix seqpacket sockets')
def test_capture_failure_rejects_notification_but_keeps_network_unlock_rpc():
    runtime,client=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    criu,worker=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    for sock in (runtime,client,criu,worker):sock.settimeout(2)
    errors=[]
    def callback():raise OSError('copy failed')
    def run():
        try:rpc.relay(client,worker,42,callback)
        except BaseException as error:errors.append(error)
    thread=threading.Thread(target=run);thread.start()
    unlock=b'\x08\x06\x10\x01\x2a\x10\x0a\x0enetwork-unlock'
    try:
        runtime.send(DUMP);assert criu.recv(1024)==DUMP
        criu.send(NOTIFY);assert criu.recv(1024)==b'\x08\x06\x18\x00'
        criu.send(unlock);assert runtime.recv(1024)==unlock
        runtime.send(ACK);assert criu.recv(1024)==ACK
        criu.send(b'\x08\x01\x10\x00');assert runtime.recv(1024)==b'\x08\x01\x10\x00'
    finally:
        runtime.close();thread.join(3)
        for sock in (client,criu,worker):sock.close()
    assert not thread.is_alive() and not errors



def test_dump_rpc_adds_scoped_inode_hint_without_replacing_other_options():
    options=b'\x10\x2a'
    request=b'\x08\x01\x12'+rpc.integer(len(options))+options
    updated=rpc.directory_fds(request,os.getpid(),[])
    options_fields=rpc.fields(rpc.fields(updated)[2])
    assert options_fields[2]==42
    hints=[v for n,w,v,_ in rpc.wire_fields(rpc.fields(updated)[2]) if n==36]
    assert hints==[b'/usr/share/dbus-1/system.d',b'/usr/lib/NetworkManager',b'/usr/lib/firmware',b'/usr/share/polkit-1']
    assert rpc.directory_fds(updated,os.getpid(),[])==updated
    restore=b'\x08\x02\x12'+rpc.integer(len(options))+options
    assert rpc.directory_fds(restore,os.getpid(),[])==restore
