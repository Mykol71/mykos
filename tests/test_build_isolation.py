import json
from pathlib import Path
import subprocess
import sys
import time
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_runtime as runtime


def test_inventory_lock_retains_last_good_data_and_reaps_child(tmp_path):
    cache = tmp_path/'cache'
    expected = {'images': [{'os': 'mac12'}], 'builds': []}
    assert runtime.inventory_read('stage-images', lambda: expected, cache) == expected
    started = time.monotonic()
    def blocked():
        return subprocess.run([sys.executable, '-c', 'import time;time.sleep(10)'], check=True)
    result = runtime.inventory_read('stage-images', blocked, cache, deadline=.05)
    assert time.monotonic()-started < 1
    assert result['images'] == expected['images']
    assert result['inventory_stale'] is True
    assert json.loads((cache/'stage-images.json').read_text())['value'] == expected
    assert runtime.inventory_read('stage-images', lambda: expected, cache) == expected
    assert not list(cache.glob('.inventory-*'))


def test_cache_rejects_mutations_and_unsafe_directory(tmp_path):
    with pytest.raises(ValueError): runtime.inventory_read('stop-pos', lambda: None, tmp_path/'cache')
    directory=tmp_path/'unsafe';directory.mkdir(mode=0o777);directory.chmod(0o777)
    with pytest.raises(ValueError, match='Unsafe'): runtime.inventory_read('list', lambda: [], directory)


def test_runtime_guest_does_not_inherit_builder_priority(monkeypatch):
    monkeypatch.setattr(runtime.os,'geteuid',lambda:0)
    monkeypatch.setattr(runtime.os,'getpriority',lambda *args:15)
    monkeypatch.setattr(runtime.Path,'is_dir',lambda path:True)
    args=[runtime.PODMAN,'run','-d','-e','MYKOS_BUILD_NICE=0','runtime']
    command=runtime.podman_scope(args)
    assert '/usr/bin/nice' in command and '-15' in command
    assert '--property=CPUWeight=10' not in command
    command=runtime.podman_scope([runtime.PODMAN,'run','-d','-e','MYKOS_BUILD_NICE=1','builder'])
    assert '--property=CPUWeight=10' in command and '--property=IOWeight=10' in command
    assert '/usr/bin/nice' not in command
