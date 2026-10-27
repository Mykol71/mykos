"""Hashed, scoped MYKOS API keys. Only stdlib dependencies."""
from contextlib import contextmanager
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from pathlib import Path

from mykos_config import BASE
DB = BASE / "data/api_keys.db"
PREFIX = 'mykos2_'

@contextmanager
def connect():
    conn = sqlite3.connect(str(DB), timeout=5)
    conn.execute('CREATE TABLE IF NOT EXISTS api_keys (id TEXT PRIMARY KEY, '
                 'label TEXT NOT NULL, scope TEXT NOT NULL, '
                 'digest TEXT NOT NULL, created_at INTEGER NOT NULL, '
                 'revoked_at INTEGER)')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def create(label, scope):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', label):
        raise ValueError('Label: 1-64 characters, letters/digits/._- only')
    if scope not in ('read', 'admin'):
        raise ValueError('Scope must be read or admin')
    key_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    key = f'{PREFIX}{key_id}_{secret}'
    hashed = hashlib.sha256(key.encode('ascii')).hexdigest()
    with connect() as conn:
        conn.execute('INSERT INTO api_keys VALUES (?,?,?,?,?,NULL)',
                     (key_id, label, scope, hashed, int(time.time())))
    return key

def authenticate(key):
    if not isinstance(key, str) or len(key) > 160:
        return None
    match = re.fullmatch(r'[a-z][a-z0-9]*2_([0-9a-f]{16})_([A-Za-z0-9_-]{40,64})', key)
    if not match:
        return None
    with connect() as conn:
        row = conn.execute('SELECT scope,digest FROM api_keys '
                           'WHERE id=? AND revoked_at IS NULL',
                           (match.group(1),)).fetchone()
    if not row:
        return None
    if not hmac.compare_digest(hashlib.sha256(key.encode('ascii')).hexdigest(), row[1]):
        return None
    return row[0]
