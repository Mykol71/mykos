"""Only newly generated guest BLS entries may select the template boot root."""
from pathlib import Path
from types import SimpleNamespace
import builtins
import pytest
from mykos_profiles import el_vm

ROOT='12345678-1234-1234-1234-123456789abc'
KERNEL='4.18.0-owned-test'


def guest(tmp_path,monkeypatch,initial=None,options=None,config='insmod blscfg\nblscfg\n',unsafe=False):
    for rel in ('etc/kernel','etc/default','boot/grub2','boot/loader/entries','boot/efi/loader/entries'):(tmp_path/rel).mkdir(parents=True)
    defaults=tmp_path/'etc/default/grub'
    if initial is not None:defaults.write_text(initial)
    stale=tmp_path/'boot/loader/entries/old-host.conf'
    stale.write_text('linux /boot/vmlinuz-'+KERNEL+'\noptions root=UUID=wrong-host\n')
    (tmp_path/'boot/efi/loader/entries/old-rescue.conf').write_text('options root=UUID=wrong-host\n')
    if unsafe:
        stale.unlink();stale.symlink_to(tmp_path/'unrelated')
    (tmp_path/'unrelated').write_text('preserve')
    calls=[];synced=[]
    def check_output(args,**kwargs):return ROOT+'\n' if args[0]=='findmnt' else KERNEL+'\n'
    def run(args,**kwargs):
        calls.append(args)
        assert 'GRUB_ENABLE_BLSCFG=true' in defaults.read_text()
        if args[0]=='kernel-install':
            assert not stale.exists(),'Inherited host boot entry survives kernel installation'
            assert not list((tmp_path/'boot/efi/loader/entries').glob('*.conf'))
            entry=tmp_path/'boot/loader/entries/guest-owned.conf'
            entry.write_text('title Guest\nlinux /boot/vmlinuz-'+KERNEL+'\noptions '+(options or 'root=UUID='+ROOT+' ro console=ttyS0,115200n8')+'\n')
        if args[0]=='grub2-mkconfig':(tmp_path/'boot/grub2/grub.cfg').write_text(config)
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    original_import=builtins.__import__
    def import_guest(name,*args,**kwargs):
        if name=='pathlib':return SimpleNamespace(Path=lambda path:tmp_path/str(path).lstrip('/'))
        if name=='subprocess':return SimpleNamespace(PIPE=-1,check_output=check_output,run=run)
        if name=='os':return SimpleNamespace(sync=lambda:synced.append(True))
        return original_import(name,*args,**kwargs)
    def execute(root,kernel):
        exec(compile(original_script(root,kernel),'<owned guest BLS setup>','exec'),{'__builtins__':{**vars(builtins),'__import__':import_guest}})
        return 'verified'
    original_script=el_vm().boot_setup_script
    monkeypatch.setattr(el_vm(),'boot_setup_script',execute)
    return defaults,calls,synced


@pytest.mark.parametrize('initial',[None,'GRUB_TIMEOUT=5\nGRUB_ENABLE_BLSCFG=false\nGRUB_DEFAULT=0\n','GRUB_TIMEOUT=5\nGRUB_ENABLE_BLSCFG=true\n'])
@pytest.mark.parametrize('options',[None,'$kernelopts'])
def test_only_guest_entries_survive_and_default_is_deterministic(tmp_path,monkeypatch,initial,options):
    defaults,calls,synced=guest(tmp_path,monkeypatch,initial,options)
    assert el_vm().boot_setup_script(ROOT,KERNEL)=='verified'
    assert defaults.read_text().count('GRUB_ENABLE_BLSCFG=true')==1
    assert defaults.read_text().count('GRUB_DEFAULT=saved')==1
    if initial is not None:assert 'GRUB_TIMEOUT=5' in defaults.read_text()
    assert any('saved_entry=guest-owned' in args for args in calls)
    assert any(args[-2:]==['unset','next_entry'] for args in calls)
    assert synced==[True]
    assert (tmp_path/'unrelated').read_text()=='preserve'


@pytest.mark.parametrize('root,kernel',[('../escape',KERNEL),(ROOT,'4;id'),(ROOT,'../kernel')])
def test_boot_setup_rejects_unsafe_identity(root,kernel):
    with pytest.raises(ValueError):el_vm().boot_setup_script(root,kernel)


def test_wrong_new_entry_is_rejected_before_selecting_default(tmp_path,monkeypatch):
    _,calls,_=guest(tmp_path,monkeypatch,options='root=UUID=wrong-host')
    with pytest.raises(RuntimeError,match='root'):el_vm().boot_setup_script(ROOT,KERNEL)
    assert not any(args[0]=='grub2-editenv' for args in calls)


def test_comment_is_not_an_enabled_bls_loader(tmp_path,monkeypatch):
    guest(tmp_path,monkeypatch,config='# blscfg disabled\necho no\n')
    with pytest.raises(RuntimeError,match='BLS'):el_vm().boot_setup_script(ROOT,KERNEL)


def test_unexpected_entry_symlink_is_not_followed(tmp_path,monkeypatch):
    guest(tmp_path,monkeypatch,unsafe=True)
    with pytest.raises(RuntimeError,match='entry'):el_vm().boot_setup_script(ROOT,KERNEL)
    assert (tmp_path/'unrelated').read_text()=='preserve'
