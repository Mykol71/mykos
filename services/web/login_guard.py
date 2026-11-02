"""Persistent bounded login admission; stores hashed account/source buckets only."""
import hashlib
import math
import sqlite3
import time
from fastapi import HTTPException

LIMITS = (('source', 20, 60), ('account', 10, 300))
MAX_BUCKETS = 10000


def bucket(kind, value):
    return kind + ':' + hashlib.sha256(value.encode()).hexdigest()


def reserve(username, source, path, now=None):
    now = time.time() if now is None else now
    try:
        with sqlite3.connect(path, timeout=2) as db:
            db.execute('CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE expires<=?', (now,))
            entries = []
            retry = 0
            for kind, limit, window in LIMITS:
                key = bucket(kind, source if kind == 'source' else username)
                row = db.execute('SELECT count,expires FROM attempts WHERE key=?', (key,)).fetchone()
                count, expires = row if row and row[1] <= now + window else (0, now + window)
                if count >= limit: retry = max(retry, math.ceil(expires-now))
                entries.append((key, count+1, expires))
            if retry:
                raise HTTPException(429, 'Too many login attempts; try again later', headers={'Retry-After': str(max(1,retry))})
            total = db.execute('SELECT count(*) FROM attempts').fetchone()[0]
            missing = sum(not db.execute('SELECT 1 FROM attempts WHERE key=?', (key,)).fetchone() for key,_,_ in entries)
            if total + missing > MAX_BUCKETS:
                raise HTTPException(429, 'Login capacity temporarily exceeded', headers={'Retry-After':'60'})
            db.executemany('INSERT OR REPLACE INTO attempts VALUES (?,?,?)', entries)
    except sqlite3.Error as error:
        raise HTTPException(503, 'Login admission temporarily unavailable') from error


def success(username, path):
    # A valid login resets only its own account bucket, never a source-wide limit.
    try:
        with sqlite3.connect(path, timeout=2) as db:
            db.execute('DELETE FROM attempts WHERE key=?', (bucket('account',username),))
    except sqlite3.Error as error:
        raise HTTPException(503, 'Login admission temporarily unavailable') from error
