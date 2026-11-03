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
    assert history.read(now=10000 + history.RETENTION + 1)['samples'] == [dict((k, sample(11000).get(k)) for k in history.FIELDS)]
    assert 'host' not in history.read(now=11000)['samples'][0]
    assert history.latest(now=11005)['host']['hostname'] == 'example'
    assert history.latest(now=11011) is None
    history.record(sample(11000 + history.RETENTION + 1), now=11000 + history.RETENTION + 1)
    assert [r['collected_at'] for r in history.read(now=11000 + history.RETENTION + 1)['samples']] == [11000 + history.RETENTION + 1]


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


def test_full_hour_recall_is_not_truncated_by_old_row_limit(store):
    import sqlite3
    now = 10000
    history.record(sample(now), now=now)
    stamps = list(range(now-history.RETENTION, now+1, history.INTERVAL))
    with sqlite3.connect(str(history.DATABASE)) as db:
        db.executemany('INSERT OR REPLACE INTO samples VALUES (?,?)',
                       [(stamp,json.dumps({key:sample(stamp).get(key) for key in history.FIELDS})) for stamp in stamps])
    result=history.read(now=now)
    assert result['retention_seconds']==3900
    assert len(result['samples'])==len(stamps)>650
    assert result['samples'][0]['collected_at']==now-history.RETENTION
    assert result['samples'][-1]['collected_at']==now
    assert len(result['samples'])<=history.MAX_SAMPLES
