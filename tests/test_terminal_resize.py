from pathlib import Path
import sys,importlib.util,importlib.machinery,struct,asyncio,json,os,pty,fcntl,termios
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'services/helpers'),str(ROOT/'services/web')]
loader=importlib.machinery.SourceFileLoader('resize_terminal_helper',str(ROOT/'services/helpers/mykos-terminal-helper'));spec=importlib.util.spec_from_loader(loader.name,loader);helper=importlib.util.module_from_spec(spec);loader.exec_module(helper)

def frame(kind,value):return kind+len(value).to_bytes(4,'big')+value

def test_split_and_coalesced_terminal_frames_preserve_input_and_size():
    data=frame(b'I',b'printf hello\r')+frame(b'R',struct.pack('!HH',132,40))+frame(b'I',b'\x00MYKOS_RESIZE ordinary input')
    parser=helper.TerminalFrames();out=[]
    for b in data:out.extend(parser.feed(bytes([b])))
    assert out==[(b'I',b'printf hello\r'),(b'R',struct.pack('!HH',132,40)),(b'I',b'\x00MYKOS_RESIZE ordinary input')]
    assert not parser.buffer

@pytest.mark.parametrize('data',[b'X\0\0\0\0',b'I'+(4097).to_bytes(4,'big'),frame(b'R',b'bad'),frame(b'R',struct.pack('!HH',0,24)),frame(b'R',struct.pack('!HH',100,501))])
def test_invalid_frames_rejected(data):
    with pytest.raises(ValueError):helper.TerminalFrames().feed(data)

def test_resize_reaches_actual_pty_without_shell_input():
    master,slave=pty.openpty()
    try:
        kind,value=helper.TerminalFrames().feed(frame(b'R',struct.pack('!HH',154,38)))[0]
        cols,rows=struct.unpack('!HH',value);fcntl.ioctl(master,termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0));assert os.get_terminal_size(slave)==os.terminal_size((154,38))
    finally:os.close(master);os.close(slave)

@pytest.mark.parametrize('framed',[False,True])
def test_authorized_websocket_resize_and_legacy_input(monkeypatch,framed):
    import app
    written=[];closed=[];packets=[{'type':'websocket.receive','text':'echo hello\r'}]
    if framed:packets.insert(0,{'type':'websocket.receive','bytes':b'\0MYKOS_RESIZE '+json.dumps({'cols':154,'rows':38}).encode()})
    packets.append({'type':'websocket.disconnect'})
    class WS:
        query_params={'geometry':'1'} if framed else {}
        async def accept(self):pass
        async def receive(self):return packets.pop(0)
        async def send_bytes(self,b):pass
        async def send_text(self,b):assert b=="MYKOS_GEOMETRY_READY"
        async def close(self,**kw):closed.append(kw)
    class Reader:
        async def readline(self):return b'MYKOS_TERMINAL_READY\n'
        async def read(self,n):await asyncio.sleep(10)
    class Writer:
        def write(self,b):written.append(b)
        async def drain(self):pass
        def close(self):pass
        async def wait_closed(self):pass
    async def connection(*a):return Reader(),Writer()
    monkeypatch.setattr(app,'mykos_terminal_authority',lambda *a:None);monkeypatch.setattr(asyncio,'open_unix_connection',connection)
    asyncio.run(app.mykos_terminal_bridge(WS(),'host'))
    if framed:
        assert json.loads(written[0])['protocol']=='framed-v1'
        assert written[1:]==[frame(b'R',struct.pack('!HH',154,38)),frame(b'I',b'echo hello\r')]
    else:assert written[1:]==[b'echo hello\r'] and 'protocol' not in json.loads(written[0])
