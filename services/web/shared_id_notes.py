"""Shared customer notes keyed by ID, independent of private change requests."""
import re
import time
from fastapi import HTTPException
from mykos_users import connect


def validate(shop):
    if not re.fullmatch(r'[0-9]{8}', shop):
        raise HTTPException(422, 'Use an eight-digit customer ID')


def table(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS shared_id_notes ('
                       'shop TEXT PRIMARY KEY, text TEXT NOT NULL, revision INTEGER NOT NULL, '
                       'updated_at INTEGER NOT NULL, updated_by TEXT NOT NULL)')


def row(connection, shop):
    value = connection.execute('SELECT text,revision,updated_at,updated_by FROM shared_id_notes WHERE shop=?', (shop,)).fetchone()
    return {'id': shop, 'text': value[0] if value else '', 'revision': value[1] if value else 0,
            'updated_at': value[2] if value else None, 'updated_by': value[3] if value else None}


def read(shop):
    validate(shop)
    with connect() as connection:
        table(connection)
        return row(connection, shop)


def save(shop, update, author):
    validate(shop)
    with connect() as connection:
        table(connection)
        connection.execute('BEGIN IMMEDIATE')
        current = row(connection, shop)
        if update.revision != current['revision']:
            raise HTTPException(409, 'Another user changed these notes. Your edits are still here; copy them before reloading.')
        connection.execute('INSERT OR REPLACE INTO shared_id_notes(shop,text,revision,updated_at,updated_by) VALUES(?,?,?,?,?)',
                           (shop, update.text, update.revision+1, int(time.time()), author))
        return row(connection, shop)
