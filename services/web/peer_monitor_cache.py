"""Bounded, process-local cache for peer monitoring, never controls or identity.

Each caller must pass the current local authorization and current peer lookup.
Keys bind the browser actor and complete remote trust identity. Cached samples
retain their original collection timestamps; a failed refresh discards its old
entry immediately. No background work runs without a browser read.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from threading import Lock
import time


class PeerCacheBusy(RuntimeError):
    pass


class PeerMonitoringCache:
    # Fresh and hard age limits in seconds; query-bearing URLs bypass the cache.
    POLICY = {
        '/api/web/host-metrics': (.75, 1.5),
        '/api/web/host-uptime': (.75, 1.5),
        '/api/web/host-history': (5., 10.),
        '/api/web/host-version': (5., 10.),
        '/api/nodes': (1., 2.),
    }

    def __init__(self, clock=time.monotonic, workers=4, max_entries=64,
                 max_bytes=16 * 1024 * 1024, max_entry_bytes=2 * 1024 * 1024,
                 max_inflight=16):
        self._clock = clock
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='peer-monitor')
        self._lock = Lock()
        self._entries = OrderedDict()
        self._inflight = {}
        self._bytes = 0
        self._epoch = 0
        self._limits = max_entries, max_bytes, max_entry_bytes, max_inflight

    def _key(self, peer, actor, path):
        identity = [peer.get(k, '') for k in ('id', 'node', 'ip', 'local_ip', 'origin', 'certificate', 'outgoing')]
        trust = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).digest()
        return self._epoch, trust, actor, path

    def _drop(self, key):
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= len(entry[1][2])

    def invalidate(self):
        # Writes invalidate all monitoring before dispatch. An older in-flight
        # read cannot repopulate or satisfy a post-action request.
        with self._lock:
            self._epoch += 1
            self._entries.clear()
            self._bytes = 0

    def _load(self, key, loader):
        started = self._clock()
        try:
            value = loader()
            status, content_type, body = value
            cacheable = (status == 200 and 'application/json' in content_type.lower()
                         and isinstance(body, bytes) and len(body) <= self._limits[2])
            if cacheable:
                try:
                    json.loads(body)
                except (ValueError, UnicodeError):
                    cacheable = False
            with self._lock:
                self._drop(key)
                if cacheable and key[0] == self._epoch:
                    self._entries[key] = started, value
                    self._bytes += len(body)
                    while len(self._entries) > self._limits[0] or self._bytes > self._limits[1]:
                        self._drop(next(iter(self._entries)))
            return value
        except BaseException:
            with self._lock:
                self._drop(key)
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def get(self, peer, actor, path, loader):
        fresh, hard = self.POLICY[path]
        with self._lock:
            key = self._key(peer, actor, path)
            now = self._clock()
            # Expire idle actors too, rather than retaining them until eviction.
            for old_key, (stamp, _) in list(self._entries.items()):
                if now - stamp >= self.POLICY[old_key[3]][1]:
                    self._drop(old_key)
            entry = self._entries.get(key)
            age = max(0., now - entry[0]) if entry else 0.
            future = self._inflight.get(key)
            coalesced = future is not None
            if entry and age < fresh:
                self._entries.move_to_end(key)
                return (*entry[1], {'state': 'hit', 'age_seconds': age})
            if future is None:
                if len(self._inflight) >= self._limits[3]:
                    raise PeerCacheBusy('Peer monitoring refresh capacity reached')
                future = self._executor.submit(self._load, key, loader)
                self._inflight[key] = future
            if entry and age < hard:
                self._entries.move_to_end(key)
                return (*entry[1], {'state': 'refreshing', 'age_seconds': age})
        # Initial/expired reads wait for one shared fetch; they never use old data.
        value = future.result()
        return (*value, {'state': 'coalesced' if coalesced else 'miss', 'age_seconds': 0.})

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)
