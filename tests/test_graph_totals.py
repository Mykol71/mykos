import importlib.util
from pathlib import Path
import sys

WEB = Path(__file__).resolve().parents[1] / 'services/web'
sys.path.insert(0, str(WEB))
spec = importlib.util.spec_from_file_location('graph_collector', WEB / 'collect-nodes.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def test_graph_counts_configured_files_without_following_links(tmp_path):
    root = tmp_path / 'pos'
    root.mkdir()
    (root / '.12345678.container').write_text('container-id')
    (root / '.87654321.container').write_text('stopped-container-id')
    (root / '.invalid.container').touch()
    account = root / '.12345678'
    account.mkdir()
    (account / '.11111111.container').touch()  # Container metadata is top-level only.
    (account / 'one.conn.conf').touch()
    nested = account / 'nested'
    nested.mkdir()
    (nested / 'two.conn.conf').touch()
    (nested / 'ipsec.secrets').write_text('not read')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'ignored.conn.conf').touch()
    (root / 'linked-directory').symlink_to(outside, target_is_directory=True)
    (root / 'linked.conn.conf').symlink_to(outside / 'ignored.conn.conf')
    assert collector.graph_totals(root) == {'containers': 2, 'vpns': 2}
    assert collector.graph_totals(tmp_path / 'missing') is None


def test_empty_pos_is_zero(tmp_path):
    assert collector.graph_totals(tmp_path) == {'containers': 0, 'vpns': 0}


def test_host_metrics_only_exposes_fresh_valid_totals(tmp_path, monkeypatch):
    import json
    import host_metrics
    monkeypatch.setattr(host_metrics, 'BASE', tmp_path)
    monkeypatch.setattr(host_metrics, 'PROC', tmp_path / 'proc')
    monkeypatch.setattr(host_metrics.time, 'time', lambda: 1000)
    (tmp_path / 'data').mkdir()
    status = tmp_path / 'data/status.json'
    for timestamp, totals, expected in [
        (990, {'containers': 3, 'vpns': 7}, {'containers': 3, 'vpns': 7}),
        (800, {'containers': 3, 'vpns': 7}, None),
        (990, {'containers': -1, 'vpns': 7}, None),
        (990, {'containers': True, 'vpns': 7}, None),
    ]:
        status.write_text(json.dumps({'collected_at': timestamp, 'graph_totals': totals}))
        assert host_metrics.snapshot()['graph_totals'] == expected
