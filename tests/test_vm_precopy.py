"""Busy-guest capture must converge or fail without leaving its source paused."""
from pathlib import Path
import shutil
from types import SimpleNamespace
import pytest
from test_desktop_vm import desktop
import mykos_vm as vm

@pytest.mark.parametrize('outcome',['completed','timeout','cancel-error'])
def test_busy_vm_has_bounded_fallback_and_always_attempts_resume(desktop,monkeypatch,tmp_path,outcome):
    root,calls=desktop;vm.create('win11','operator','private-password');value=vm.read('win11')
    source=vm.storage('win11')/'storage';(source/'data.img').write_bytes(b'disk');checkpoint=source/'.mykos-checkpoint';checkpoint.mkdir();(checkpoint/'memory.gz').write_bytes(b'prior valid checkpoint')
    original=vm.QemuMonitor
    class Busy(original):
        cancelled=False
        def command(self,command):
            if command=='info migrate' and self.migrated:
                calls.append(['monitor',command])
                return 'Status: completed' if self.paused and outcome=='completed' else 'Status: active'
            if command=='migrate_cancel':
                if outcome=='cancel-error':raise RuntimeError('Injected cancellation error')
            result=super().command(command)
            if command.startswith('migrate -d'):self.paused=False
            return result
    clock=[0]
    def tick():clock[0]+=8;return clock[0]
    monkeypatch.setattr(vm,'QemuMonitor',Busy)
    monkeypatch.setattr(vm,'time',SimpleNamespace(monotonic=tick,sleep=lambda _:None))
    monkeypatch.setattr(vm,'tree_copy',lambda source,destination,**kw:shutil.copytree(source,destination))
    destination=tmp_path/'capture'
    if outcome=='completed':
        vm.capture_live(value,source,destination)
        assert (destination/'.mykos-checkpoint/manifest.json').is_file()
    else:
        with pytest.raises(RuntimeError):vm.capture_live(value,source,destination)
        assert not destination.exists()
        assert (checkpoint/'memory.gz').read_bytes()==b'prior valid checkpoint'
    migration=next(i for i,c in enumerate(calls) if c[0]=='monitor' and c[1].startswith('migrate -d'))
    assert migration<calls.index(['monitor','stop'])<calls.index(['monitor','cont'])
