"""Root collectors publish snapshots readable by the web service group."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

WEB = Path(__file__).resolve().parents[1]/'services/web'
sys.path.insert(0, str(WEB))


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), WEB/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_group_is_set_even_when_run_without_systemd(tmp_path, monkeypatch):
    module = load('collect-nodes')
    calls = []
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module.grp, 'getgrnam', lambda name: SimpleNamespace(gr_gid=1234))
    monkeypatch.setattr(module.os, 'chown', lambda *args: calls.append(args))
    path = tmp_path/'inventory.json'
    module.atomic_json(path, {'nodes': []})
    assert json.loads(path.read_text()) == {'nodes': []}
    assert path.stat().st_mode & 0o777 == 0o640
    assert calls[0][1:] == (0, 1234)


def test_details_group_is_set_on_publish(tmp_path, monkeypatch):
    module = load('collect-details')
    calls = []
    monkeypatch.setattr(module, 'OUTPUT', tmp_path/'details.json')
    monkeypatch.setattr(module.host, 'containers', lambda: [])
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module.grp, 'getgrnam', lambda name: SimpleNamespace(gr_gid=1234))
    monkeypatch.setattr(module.os, 'chown', lambda *args: calls.append(args))
    module.collect()
    assert json.loads(module.OUTPUT.read_text())['details'] == {}
    assert calls[0][1:] == (0, 1234)
