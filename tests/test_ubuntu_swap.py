import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('ubuntu_setup',Path(__file__).resolve().parents[1]/'services/web/ubuntu-image-setup.py')
swap=importlib.util.module_from_spec(spec);spec.loader.exec_module(swap)


def test_swap_size_and_fstab_preserve_unrelated_entries():
    assert swap.SIZE==12*1024**3
    text='# swap comment\nUUID=abc / ext4 defaults 0 1\n/swap.img none swap sw 0 0\n'
    updated=swap.fstab_text(text,str(swap.TARGET))
    assert '# swap comment' in updated and 'UUID=abc / ext4 defaults 0 1' in updated
    assert '/swap.img none' not in updated
    assert updated==swap.fstab_text(updated,str(swap.TARGET))


def test_swap_setup_rejects_unknown_devices_and_symlinks(tmp_path):
    with pytest.raises(ValueError,match='Unrecognized'):swap.swap_rows('/dev/sda3 12345')
    assert swap.swap_rows('/swap.img 2147479552')==[('/swap.img',2147479552)]
    path=tmp_path/'swap';path.symlink_to('/etc/passwd')
    with pytest.raises(ValueError):swap.regular_root_file(path)


def test_live_installer_rejected_before_allocating_swap(monkeypatch):
    calls=[]
    monkeypatch.setattr(swap.os,'geteuid',lambda:0)
    monkeypatch.setattr(Path,'read_text',lambda self:'ID=ubuntu\n')
    def command(args):
        calls.append(args)
        return 'kvm' if args[0]=='systemd-detect-virt' else 'overlay'
    monkeypatch.setattr(swap,'command',command)
    with pytest.raises(ValueError,match='live installer'):swap.setup()
    assert calls==[['systemd-detect-virt','--vm'],['findmnt','-n','-o','FSTYPE','/']]
