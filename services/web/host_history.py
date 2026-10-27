"""Bounded, host-only performance history shared across browser sessions."""
import json
from contextlib import closing
import math
import os
from pathlib import Path
import signal
import sqlite3
import tempfile
import threading
import time
from urllib.parse import quote
from mykos_config import BASE

RETENTION = 30 * 60
INTERVAL = 3
DATABASE = BASE / 'data/host-history.sqlite3'
LIVE = Path('/run/mykos-host-metrics/latest.json')
FIELDS = ('collected_at', 'cpu_percent', 'cpu_count', 'memory', 'disk',
          'load_average', 'graph_totals', 'graph_counts')


def latest(now=None):
    now = time.time() if now is None else now
    try:
        value = json.loads(LIVE.read_text())
        if 0 <= now - value['collected_at'] <= 10: return value
    except (OSError, ValueError, TypeError, KeyError): pass
    return None


def read(since=0, now=None):
    now = time.time() if now is None else now
    if not math.isfinite(since) or since < 0: raise ValueError('Invalid history timestamp')
    result = {'retention_seconds': RETENTION, 'sample_interval_seconds': INTERVAL,
              'collected_at': now, 'samples': []}
    try:
        with closing(sqlite3.connect('file:' + quote(str(DATABASE)) + '?mode=ro', uri=True, timeout=.2)) as db:
            rows = db.execute('SELECT sample FROM samples WHERE collected_at >= ? AND collected_at <= ? ORDER BY collected_at LIMIT 650',
                              (max(since, now - RETENTION), now + 1)).fetchall()
        result['samples'] = [json.loads(row[0]) for row in rows]
    except (OSError, sqlite3.Error, ValueError): pass
    return result


def record(value, now=None):
    now = time.time() if now is None else now
    stamp = value['collected_at']
    if not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or not now - RETENTION <= stamp <= now + 1:
        raise ValueError('Invalid sample timestamp')
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(DATABASE), timeout=1)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=NORMAL')
        db.execute('PRAGMA wal_autocheckpoint=128')
        db.execute('CREATE TABLE IF NOT EXISTS samples (collected_at REAL PRIMARY KEY, sample TEXT NOT NULL)')
        sample = {key: value.get(key) for key in FIELDS}
        db.execute('INSERT OR REPLACE INTO samples VALUES (?, ?)', (stamp, json.dumps(sample, separators=(',', ':'), allow_nan=False)))
        db.execute('DELETE FROM samples WHERE collected_at < ? OR collected_at > ?', (now - RETENTION, now + 1))
        db.commit()
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.latest-', dir=str(LIVE.parent))
    try:
        with os.fdopen(fd, 'w') as f: json.dump(value, f, separators=(',', ':'), allow_nan=False)
        os.chmod(name, 0o600); os.replace(name, LIVE)
    finally:
        if os.path.exists(name): os.unlink(name)


def collect():
    from host_metrics import snapshot
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    while not stopped.is_set():
        start = time.monotonic()
        try: record(snapshot())
        except (OSError, ValueError, sqlite3.Error) as error: print('Host history:', error, flush=True)
        stopped.wait(max(.1, INTERVAL - (time.monotonic() - start)))


if __name__ == '__main__': collect()
