"""Private pane notes, preserved with the dashboard's existing database."""
import time
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, Field
from mykos_users import connect

Pane = Literal['terminal', 'host', 'linux', 'rocky', 'alma', 'oracle', 'centos', 'fedora', 'ubuntu', 'windows', 'windows-server', 'macos', 'mssql', 'backups', 'repos', 'snapshots']


class NoteUpdate(BaseModel):
    text: str = Field(max_length=20000)
    revision: int = Field(ge=0)


def authorize(request, write=False):
    # API keys identify a scope, not a user, and cannot prove mgreen ownership.
    from app import mykos_current_user, origin
    if mykos_current_user(request).get('username') != 'mgreen':
        raise HTTPException(403, 'Pane notes are private to mgreen')
    if write: origin(request)


def table(connection, personal=False):
    name = 'personal_notes' if personal else 'pane_notes'
    connection.execute('CREATE TABLE IF NOT EXISTS '+name+' ('
                       'pane TEXT PRIMARY KEY, text TEXT NOT NULL, '
                       'revision INTEGER NOT NULL, updated_at INTEGER NOT NULL)')


def read_row(connection, pane, personal=False):
    name = 'personal_notes' if personal else 'pane_notes'
    row = connection.execute('SELECT text, revision, updated_at FROM '+name+' WHERE pane=?', (pane,)).fetchone()
    return {'pane': pane, 'text': row[0] if row else '',
            'revision': row[1] if row else 0, 'updated_at': row[2] if row else None}


def read(pane: Pane, personal=False):
    with connect() as connection:
        table(connection, personal)
        return read_row(connection, pane, personal)


def save(pane: Pane, update: NoteUpdate, personal=False):
    name = 'personal_notes' if personal else 'pane_notes'
    with connect() as connection:
        table(connection, personal)
        connection.execute('BEGIN IMMEDIATE')
        current = read_row(connection, pane, personal)
        if update.revision != current['revision']:
            raise HTTPException(409, 'Notes changed in another window. Copy your edits before reloading the latest notes.')
        connection.execute('INSERT OR REPLACE INTO '+name+'(pane,text,revision,updated_at) VALUES(?,?,?,?)',
                           (pane, update.text, update.revision + 1, int(time.time())))
        return read_row(connection, pane, personal)


def read_personal():
    return read('host', personal=True)


def save_personal(update: NoteUpdate):
    return save('host', update, personal=True)
