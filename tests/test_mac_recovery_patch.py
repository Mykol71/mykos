import importlib.util
from pathlib import Path
import plistlib
import struct
import sys
import zlib
import pytest

path=Path(__file__).resolve().parents[1]/'pos/install/macos/patch.py'
spec=importlib.util.spec_from_file_location('mykos_recovery_patch',path)
patch=importlib.util.module_from_spec(spec);spec.loader.exec_module(patch)


def dmg(path,script):
    payload=(script+b'\0'+patch.RECOVERY_ORIGINAL).ljust(4096,b'\0')
    stored=zlib.compress(payload,0)
    mish=bytearray(284);mish[:4]=b'mish'
    struct.pack_into('>I',mish,204,patch.UDZO)
    struct.pack_into('>Q',mish,220,8)
    struct.pack_into('>Q',mish,236,len(stored))
    struct.pack_into('>I',mish,244,0xffffffff)
    xml=plistlib.dumps({'resource-fork':{'blkx':[{'Data':bytes(mish)}]}})
    koly=bytearray(512);koly[:4]=b'koly'
    struct.pack_into('>Q',koly,216,len(stored));struct.pack_into('>Q',koly,224,len(xml))
    path.write_bytes(stored+xml+koly)
    return len(stored)


@pytest.mark.parametrize('script',[patch.SCRIPT_ORIGINAL,patch.SCRIPT_MODERN])
def test_recovery_patch_preserves_dmg_geometry_and_boot_tasks(tmp_path,monkeypatch,script):
    image=tmp_path/'base.dmg';length=dmg(image,script);original_size=image.stat().st_size
    monkeypatch.setattr(sys,'argv',['patch.py',str(image)])
    patch.main()
    assert image.stat().st_size==original_size
    data=zlib.decompress(image.read_bytes()[:length])
    assert b'exec /Volumes/installstate/launch.sh' in data
    assert patch.RECOVERY_REPLACEMENT in data
    assert patch.RECOVERY_ORIGINAL not in data
    if script==patch.SCRIPT_MODERN:
        assert b'mount -u -o ro /' in data and b'/usr/libexec/bs-early-tasks "$1"' in data


def test_unrecognized_recovery_is_rejected_without_modifying_image(tmp_path,monkeypatch):
    image=tmp_path/'base.dmg';dmg(image,b'unknown Apple Recovery startup script')
    before=image.read_bytes();monkeypatch.setattr(sys,'argv',['patch.py',str(image)])
    with pytest.raises(RuntimeError,match='supported rc.cdrom.sh'):patch.main()
    assert image.read_bytes()==before


def test_recovery_copy_validation_without_cmp(tmp_path):
    import os, shutil, subprocess
    source = (path.parent / 'recovery.sh').read_text()
    function = 'compare_staged_file() {' + source.split('compare_staged_file() {', 1)[1].split('\ninstall_bootstrap() {', 1)[0]
    tools = tmp_path / 'bin'; tools.mkdir()
    openssl = shutil.which('openssl')
    if not openssl: pytest.skip('OpenSSL required for Recovery fallback test')
    (tools / 'openssl').symlink_to(openssl)
    a = tmp_path / 'a'; b = tmp_path / 'b'
    a.write_bytes(b'package\0binary\xff'); b.write_bytes(a.read_bytes())
    env = dict(os.environ, PATH=str(tools))
    script = function + '\ncompare_staged_file "$1" "$2"'
    assert subprocess.run(['/bin/bash', '-c', script, 'check', str(a), str(b)], env=env).returncode == 0
    b.write_bytes(b'other data')
    assert subprocess.run(['/bin/bash', '-c', script, 'check', str(a), str(b)], env=env).returncode == 1
