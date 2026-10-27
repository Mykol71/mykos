import importlib
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/web'))
import host_history as history


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(history, 'DATABASE', tmp_path / 'history.sqlite3')
    monkeypatch.setattr(history, 'LIVE', tmp_path / 'run/latest.json')
    return tmp_path


def sample(stamp):
    return {'collected_at': stamp, 'cpu_percent': 42, 'cpu_count': 4,
            'host': {'hostname': 'example'}, 'memory': {'percent': 20},
            'graph_totals': {'containers': 2, 'vpns': 1},
            'graph_counts': {'containers': 1, 'vpns': 1}}


def test_history_persists_and_expires(store):
    history.record(sample(10000), now=10000)
    history.record(sample(11000), now=11000)
    assert len(history.read(now=11000)['samples']) == 2
    assert history.read(since=10500, now=11000)['samples'][0]['collected_at'] == 11000
    assert history.read(now=12000)['samples'] == [dict((k, sample(11000).get(k)) for k in history.FIELDS)]
    assert 'host' not in history.read(now=11000)['samples'][0]
    assert history.latest(now=11005)['host']['hostname'] == 'example'
    assert history.latest(now=11011) is None
    history.record(sample(13000), now=13000)
    assert [r['collected_at'] for r in history.read(now=13000)['samples']] == [13000]


def test_duplicate_samples_and_invalid_timestamps(store):
    history.record(sample(10000), now=10000)
    history.record(sample(10000), now=10000)
    assert len(history.read(now=10000)['samples']) == 1
    for stamp in (float('nan'), 1, 20000):
        with pytest.raises(ValueError): history.record(sample(stamp), now=10000)
    for since in (-1, float('inf')):
        with pytest.raises(ValueError): history.read(since, now=10000)


def test_empty_or_missing_history_is_safe(store):
    assert history.read(now=10000)['samples'] == []
    assert history.latest(now=10000) is None
    history.LIVE.parent.mkdir()
    history.LIVE.write_text('invalid')
    assert history.latest(now=10000) is None


def test_history_does_not_invoke_container_commands():
    source = Path(history.__file__).read_text()
    assert 'subprocess' not in source and 'podman' not in source
