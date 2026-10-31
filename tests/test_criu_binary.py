from pathlib import Path
from types import SimpleNamespace
import stat,sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_criu_binary as tool


def info(path,**changes):
    values={'st_uid':0,'st_mode':(stat.S_IFREG|0o755) if path.name=='criu' else (stat.S_IFDIR|0o755)}
    values.update(changes);return SimpleNamespace(**values)


def test_managed_binary_requires_trusted_file_and_every_parent():
    value='/opt/mykos-criu/4.2.1/sbin/criu';seen=[]
    def read(path):seen.append(path);return info(path)
    assert tool.checked(value,read)==value
    assert seen==[Path(value),*Path(value).parents]


@pytest.mark.parametrize('bad',[
    {'st_uid':1000}, {'st_mode':stat.S_IFREG|0o777}, {'st_mode':stat.S_IFLNK|0o777}, {'st_mode':stat.S_IFREG|0o644},
])
def test_managed_binary_rejects_untrusted_or_nonexecutable_file(bad):
    def read(path):return info(path,**bad) if path.name=='criu' else info(path)
    with pytest.raises(ValueError):tool.checked('/opt/mykos-criu/4.2.1/sbin/criu',read)


@pytest.mark.parametrize('directory',['/opt','/opt/mykos-criu','/opt/mykos-criu/4.2.1/sbin'])
def test_managed_binary_rejects_replaceable_parent(directory):
    def read(path):return info(path,st_mode=stat.S_IFDIR|0o777) if str(path)==directory else info(path)
    with pytest.raises(ValueError,match='parent directories'):tool.checked('/opt/mykos-criu/4.2.1/sbin/criu',read)


@pytest.mark.parametrize('value',['/tmp/criu','/opt/mykos-criu/../criu','/opt/mykos-criu/latest/sbin/criu','/opt/mykos-criu/4.2.1/sbin/criu;id',None])
def test_configuration_cannot_select_arbitrary_executables(value):
    with pytest.raises(ValueError):tool.checked(value)


def test_vendor_discovery_normalizes_merged_sbin_alias(monkeypatch,tmp_path):
    monkeypatch.setattr(tool,'ROOT',tmp_path/'no-managed-engine')
    monkeypatch.setattr(tool.shutil,'which',lambda name:'/sbin/criu')
    monkeypatch.setattr(Path,'resolve',lambda path,strict=False:Path('/usr/sbin/criu'))
    assert tool.binary({})=='/usr/sbin/criu'


def test_packaged_engine_is_preferred_without_overriding_explicit_selection(monkeypatch,tmp_path):
    monkeypatch.setattr(tool,'ROOT',tmp_path)
    path=tmp_path/'4.2.1/sbin/criu';path.parent.mkdir(parents=True);path.touch()
    monkeypatch.setattr(tool,'checked',lambda value:value)
    assert tool.binary({})==str(path)
    assert tool.binary({'criu_binary':'/usr/sbin/criu'})=='/usr/sbin/criu'


def test_unsafe_packaged_path_does_not_silently_fall_back(monkeypatch,tmp_path):
    monkeypatch.setattr(tool,'ROOT',tmp_path)
    path=tmp_path/'4.2.1/sbin/criu';path.parent.mkdir(parents=True);path.symlink_to('/missing')
    with pytest.raises(ValueError):tool.binary({})
