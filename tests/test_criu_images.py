from pathlib import Path
import shutil,struct,sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_criu as rpc
import mykos_criu_images as images

def field(n,v):
    return rpc.integer(n<<3)+rpc.integer(v) if isinstance(v,int) else rpc.integer((n<<3)|2)+rpc.integer(len(v))+v

def entry(target=b'/etc/hosts',external=False,**changes):
    values={1:0,2:42,3:100,4:1,5:2097153,6:b'/runtime/hosts',7:target,8:b'tmpfs',9:b'rw',10:123}
    if external:values[18]=target
    values.update({int(k):v for k,v in changes.items()})
    return b''.join(field(n,v) for n,v in values.items())+field(999,b'unknown-field')

def write(path,*records):
    path.write_bytes(images.MAGIC+b''.join(struct.pack('<I',len(record))+record for record in records))

def test_cloned_file_mapping_is_fixed_in_restore_copy_and_idempotent(tmp_path):
    original=tmp_path/'archive-copy';original.mkdir();root=original/'mountpoints-1.img';clone=original/'mountpoints-2.img'
    write(root,entry(external=True));write(clone,entry(),entry(target=b'/data'))
    before={p.name:p.read_bytes() for p in original.iterdir()};working=tmp_path/'transaction';shutil.copytree(original,working)
    assert images.normalize_files(working,{b'/etc/hosts'})==1
    assert all(p.read_bytes()==before[p.name] for p in original.iterdir())
    raw=(working/clone.name).read_bytes();size=struct.unpack_from('<I',raw,8)[0];values=rpc.fields(raw[12:12+size])
    assert values[18]==b'/etc/hosts' and values[15]==1 and values[5]==2097153 and values[10]==123 and values[999]==b'unknown-field'
    assert images.normalize_files(working,{b'/etc/hosts'})==0
    assert raw==(working/clone.name).read_bytes()


@pytest.mark.parametrize('changes',[{'3':101},{'6':b'/different/file'},{'8':b'different'}])
def test_unrelated_mount_identity_is_never_rewritten(tmp_path,changes):
    write(tmp_path/'mountpoints-1.img',entry(external=True));p=tmp_path/'mountpoints-2.img';write(p,entry(**changes));before=p.read_bytes()
    assert images.normalize_files(tmp_path,{b'/etc/hosts'})==0 and p.read_bytes()==before


def test_slave_mount_rejects_whole_update_before_any_write(tmp_path):
    write(tmp_path/'mountpoints-1.img',entry(external=True));p=tmp_path/'mountpoints-2.img';write(p,entry(),entry(**{'11':123}));before=p.read_bytes()
    with pytest.raises(ValueError,match='slave'):images.normalize_files(tmp_path,{b'/etc/hosts'})
    assert p.read_bytes()==before


@pytest.mark.parametrize('data',[b'badmagic',images.MAGIC+b'x',images.MAGIC+struct.pack('<I',200)+b'x'])
def test_corrupt_images_fail_before_mutation(tmp_path,data):
    p=tmp_path/'mountpoints-1.img';p.write_bytes(data)
    with pytest.raises(ValueError):images.normalize_files(tmp_path,{b'/etc/hosts'})
    assert p.read_bytes()==data


def test_runtime_file_types_survive_stopped_container_preparation(tmp_path):
    regular=tmp_path/'regular';regular.touch();directory=tmp_path/'directory';directory.mkdir();link=tmp_path/'link';link.symlink_to(regular)
    state={'Mounts':[{'Type':'bind','Source':str(p),'Destination':dest} for p,dest in [(regular,'/file'),(directory,'/directory'),(link,'/link')]],'HostsPath':str(tmp_path/'not-created-yet')}
    assert images.external_files(state)=={b'/file',b'/etc/hosts',b'/run/.containerenv'}
