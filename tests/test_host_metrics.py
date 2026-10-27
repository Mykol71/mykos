"""Host counters use Linux aggregate CPU and available memory semantics."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/web'))
import host_metrics


def test_host_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(host_metrics, 'PROC', tmp_path)
    (tmp_path / 'stat').write_text('cpu 100 0 50 800 20 10 10 10 40 0\n')
    # The guest counter (40) must not inflate the total.
    assert host_metrics.cpu_times() == (1000, 820)
    def advance(_):
        (tmp_path / 'stat').write_text('cpu 120 0 60 860 20 10 10 20 50 0\n')
    monkeypatch.setattr(host_metrics.time, 'sleep', advance)
    (tmp_path / 'meminfo').write_text(
        'MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\n'
        'SwapTotal: 0 kB\nSwapFree: 0 kB\n')
    (tmp_path / 'uptime').write_text('90061.25 100000\n')
    monkeypatch.setattr(host_metrics.shutil, 'disk_usage', lambda _: (1000, 600, 400))
    monkeypatch.setattr(host_metrics.os, 'getloadavg', lambda: (1.5, 1, 0.5))
    result = host_metrics.snapshot()
    assert result['cpu_percent'] == 40
    assert result['memory'] == {'total_bytes': 1024000, 'used_bytes': 614400, 'percent': 60}
    assert result['swap']['percent'] == 0
    assert result['load_average'] == [1.5, 1, 0.5]
    assert result['disk']['percent'] == 60
    assert result['uptime_seconds'] == 90061.25


def test_unavailable_host_counters(tmp_path, monkeypatch):
    monkeypatch.setattr(host_metrics, 'PROC', tmp_path)
    def unavailable(*args):
        raise OSError('Unavailable')
    monkeypatch.setattr(host_metrics.shutil, 'disk_usage', unavailable)
    monkeypatch.setattr(host_metrics.os, 'getloadavg', unavailable)
    result = host_metrics.snapshot()
    for key in ('cpu_percent', 'memory', 'swap', 'disk', 'load_average', 'uptime_seconds'):
        assert result[key] is None


def test_internal_ip_uses_local_route_without_sending(monkeypatch):
    calls = []
    class RouteSocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def connect(self, destination): calls.append(destination)
        def getsockname(self): return ('192.168.222.5', 12345)
    monkeypatch.setattr(host_metrics.socket, 'socket', lambda *args: RouteSocket())
    assert host_metrics.internal_ip() == '192.168.222.5'
    assert calls == [('8.8.8.8', 53)]
    def unavailable(*args): raise OSError('No route')
    monkeypatch.setattr(host_metrics.socket, 'socket', unavailable)
    assert host_metrics.internal_ip() is None
