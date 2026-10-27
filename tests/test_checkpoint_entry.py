from pathlib import Path
import subprocess


def test_later_boot_does_not_reuse_consumed_ram_hook(tmp_path):
    run=tmp_path/'run';run.mkdir()
    storage=tmp_path/'storage/.mykos-checkpoint';storage.mkdir(parents=True)
    (run/'finish.sh').write_text('true\n')
    entry=run/'entry.sh'
    entry.write_text('#!/bin/bash\nARGS=""\n. finish.sh\necho "Booting $ARGS"\n')
    entry.chmod(0o700)
    (storage/'memory.gz').write_bytes(b'not read by test entry')
    (storage/'restore.request').write_text('restore')
    source=Path(__file__).resolve().parents[1]/'services/helpers/mykos-checkpoint-entry'
    text=source.read_text().replace('/storage/',str(tmp_path/'storage')+'/').replace('/run/',str(run)+'/')
    # The real entry uses /run as its working directory.
    loader=tmp_path/'loader';loader.write_text(text)
    first=subprocess.check_output(['bash',str(loader)],cwd=run,text=True)
    assert '-incoming' in first and (storage/'restore.consumed').exists()
    second=subprocess.check_output(['bash',str(loader)],cwd=run,text=True)
    assert '-incoming' not in second and second.strip()=='Booting'
    assert 'mykos-checkpoint-restore' not in entry.read_text()


def test_staged_windows_never_reenters_install_or_deletes_capture_media(tmp_path):
    import os
    run=tmp_path/'run';run.mkdir()
    storage=tmp_path/'storage';storage.mkdir()
    (storage/'data.img').write_bytes(b'installed disk')
    (storage/'setup.img').write_bytes(b'captured removable device')
    install=run/'install.sh'
    install.write_text('''needsInstall() {
 echo destructive-install >&2
 return 0
}
cleanupTemp() {
 rm -f "$DISK" "$MEDIA"
}
cleanupSource() {
 rm -f "$DISK"
}
if needsInstall; then exit 99; fi
cleanupTemp
cleanupSource
''')
    entry=run/'entry.sh';entry.write_text('#!/bin/bash\nset -e\n. ./install.sh\necho staged-disk\n');entry.chmod(0o700)
    source=Path(__file__).resolve().parents[1]/'services/helpers/mykos-checkpoint-entry'
    loader=tmp_path/'loader'
    loader.write_text(source.read_text().replace('/storage/',str(storage)+'/').replace('/run/',str(run)+'/'))
    env={**os.environ,'MYKOS_PRESERVE_WINDOWS_DISK':'Y','DISK':str(storage/'data.img'),'MEDIA':str(storage/'setup.img')}
    for _ in range(2):
        result=subprocess.run(['bash',str(loader)],cwd=run,text=True,capture_output=True,env=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'staged-disk'
        assert (storage/'data.img').read_bytes()==b'installed disk'
        assert (storage/'setup.img').exists()
    assert install.read_text().count('MYKOS_PRESERVE_WINDOWS_DISK') == 3
