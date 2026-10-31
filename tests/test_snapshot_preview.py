"""Private preview lifetimes and clone boundaries, with mocked system tools."""
import contextlib
import json
from pathlib import Path
import sys
import time
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_snapshot_preview as p

TOKEN='a'*32
OWNER='b'*64

@pytest.fixture
def preview(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'STATE',tmp_path/'previews')
    monkeypatch.setattr(p.vm,'ROOT',tmp_path/'registry');p.vm.safe_dir(p.vm.ROOT)
    monkeypatch.setattr(p.vm,'POS',tmp_path/'pos');p.vm.POS.mkdir()
    monkeypatch.setattr(p.vm,'storage_root',lambda:tmp_path/'storage')
    monkeypatch.setattr(p.vm,'locked',contextlib.nullcontext)
    calls=[]
    monkeypatch.setattr(p.subprocess,'run',lambda args,**kw:calls.append(args))
    monkeypatch.setattr(p.snapshots,'read_snapshot',lambda *a:(tmp_path,{'os':'win11','capture_mode':'memory'}))
    monkeypatch.setattr(p.secrets,'token_hex',lambda n:TOKEN)
    return tmp_path,calls


def test_preview_queue_is_session_owned_and_never_restores_source(preview):
    root,calls=preview
    value=p.create('12345678','1793400000-abcdef123456',OWNER)
    assert value['state']=='queued' and value['mode']=='memory'
    assert 'owner' not in value and 'key' not in value
    assert calls[0][0]=='systemd-run'
    assert '--collect' in calls[0]
    assert not list(p.vm.ROOT.glob('*.json'))
    with pytest.raises(ValueError,match='not found'):p.heartbeat(TOKEN,'c'*64)
    assert p.heartbeat(TOKEN,OWNER)['token']==TOKEN
    assert p.create('12345678','1793400000-abcdef123456',OWNER)['token']==TOKEN
    assert len(calls)==1


@pytest.mark.parametrize('value',['../x','A'*32,'0'*31,'a'*33,'12345678'])
def test_preview_tokens_cannot_select_host_paths(preview,value):
    with pytest.raises(ValueError):p.directory(value)


def test_close_and_expiry_cannot_be_renewed(preview):
    p.create('12345678','1793400000-abcdef123456',OWNER)
    assert p.close(TOKEN,OWNER)['state']=='closing'
    with pytest.raises(ValueError,match='expired'):p.heartbeat(TOKEN,OWNER)


def test_expired_lease_is_not_resurrected(preview):
    p.create('12345678','1793400000-abcdef123456',OWNER)
    p.write(p.directory(TOKEN)/'lease.json',{'until':time.time()-1})
    with pytest.raises(ValueError,match='expired'):p.heartbeat(TOKEN,OWNER)


def test_native_preview_uses_private_mount_copies_and_no_network(preview,monkeypatch):
    root,calls=preview;folder=root/'snapshot';folder.mkdir();(folder/'rootfs-upper').mkdir()
    saved=folder/'mounts/0/data';saved.mkdir(parents=True);(saved/'saved').write_text('point in time')
    upper=root/'own-upper';upper.mkdir()
    data={'key':'94949494','name':'mykos-preview-'+TOKEN,'token':TOKEN,'owner':OWNER,'state':'starting','expires':time.time()+300}
    p.vm.safe_dir(p.directory(TOKEN));p.write(p.directory(TOKEN)/'status.json',data);p.write(p.directory(TOKEN)/'lease.json',{'until':time.time()+90})
    commands=[]
    def run(args,**kw):
        commands.append(args)
        if args[0]=='/usr/bin/cp':
            import shutil;shutil.copytree(args[-2],args[-1])
        return ''
    monkeypatch.setattr(p.vm,'run',run)
    monkeypatch.setattr(p,'verify_container',lambda d:{'Id':'preview-id','GraphDriver':{'Name':'overlay','Data':{'UpperDir':str(upper)}},'State':{'Running':True}})
    p.start_filesystem(data,folder,{'image':'pinned-image','mounts':[{'source':'/original/customer','destination':'/etc/ipsec.d','rw':True}]})
    create=next(c for c in commands if c[1]=='create')
    assert '--network=none' in create and '--image-volume=ignore' in create and '--security-opt=no-new-privileges' in create
    assert '/original/customer' not in ' '.join(create)
    assert str(saved) not in ' '.join(create)
    assert (p.vm.storage(data['key'])/'mount-0/saved').read_text()=='point in time'
    assert next(c for c in commands if c[0]=='/usr/bin/rsync')[-1]==str(upper)+'/'
    assert not any('restore' in c for c in commands)


@pytest.mark.parametrize('destination',['/','/proc/sys','/sys/fs/cgroup','/dev/sda','relative','/etc/../root','/etc/a,source=/host'])
def test_native_preview_rejects_kernel_and_injected_mounts(preview,monkeypatch,destination):
    root,_=preview
    data={'key':'94949494','name':'mykos-preview-'+TOKEN,'token':TOKEN}
    monkeypatch.setattr(p.vm,'run',lambda *a,**k:pytest.fail('Unsafe mount must fail before create'))
    with pytest.raises(ValueError,match='destination'):
        p.start_filesystem(data,root,{'image':'pinned','mounts':[{'destination':destination}]})


def test_preview_gc_removes_only_expired_owned_resources(preview,monkeypatch):
    p.create('12345678','1793400000-abcdef123456',OWNER)
    deleted=[];monkeypatch.setattr(p,'cleanup',lambda d:deleted.append(d['token']))
    p.collect();assert deleted==[]
    p.write(p.directory(TOKEN)/'lease.json',{'until':0})
    p.collect();assert deleted==[TOKEN] and not p.directory(TOKEN).exists()
