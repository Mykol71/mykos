"""Browser identity storage, also used by the local account manager."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
from mykos_config import BASE

DB = BASE / 'data/auth.db'

@contextmanager
def connect():
    c = sqlite3.connect(DB, timeout=10)
    try:
        c.execute('CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY,salt TEXT,hash TEXT)')
        columns = {r[1] for r in c.execute('PRAGMA table_info(users)')}
        if 'role' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        c.execute('CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,name TEXT,expires INTEGER)')
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def validate_name(name):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
        raise ValueError('Username: 1-64 ASCII letters, digits, underscore or hyphen')

def is_admin(name):
    with connect() as c:
        row = c.execute('SELECT role FROM users WHERE name=?', (name,)).fetchone()
    return bool(row and row[0] == 'admin')

def password_minimum(name):
    """Root-managed per-account exceptions; other accounts retain 12 characters."""
    policy = Path('/etc/mykos/password-policy.json')
    if not policy.exists():
        return 12
    minimum = json.loads(policy.read_text()).get('minimum_length_by_user', {}).get(name, 12)
    if type(minimum) is not int or not 1 <= minimum <= 12:
        raise ValueError('Invalid account password minimum')
    return minimum

def set_password(name, password, role=None, create=False):
    validate_name(name)
    minimum = password_minimum(name)
    if len(password) < minimum:
        raise ValueError(f'Use a password of at least {minimum} characters')
    if role is not None and role not in ('user', 'admin'):
        raise ValueError('Role must be user or admin')
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    with connect() as c:
        exists = c.execute('SELECT role FROM users WHERE name=?', (name,)).fetchone()
        if create and exists:
            raise ValueError('User exists; use passwd or role')
        if not create and not exists:
            raise ValueError('User does not exist; use create')
        c.execute('INSERT INTO users(name,salt,hash,role) VALUES(?,?,?,?) '
                  'ON CONFLICT(name) DO UPDATE SET salt=excluded.salt,hash=excluded.hash,role=excluded.role',
                  (name, salt, digest, role or (exists[0] if exists else 'user')))
        c.execute('DELETE FROM sessions WHERE name=?', (name,))
