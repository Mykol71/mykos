import importlib.machinery
import importlib.util
from pathlib import Path
import sqlite3


def test_logins_read_only_and_no_credentials(tmp_path):
    source = Path(__file__).parents[1] / 'services/helpers/mykos-who'
    loader = importlib.machinery.SourceFileLoader('test_who', str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    path = tmp_path / 'auth.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sessions(token TEXT, name TEXT, expires INTEGER)')
        db.executemany('INSERT INTO sessions VALUES(?,?,?)', [('never-show-a', 'alice', 101), ('never-show-b', 'alice', 200), ('never-show-c', 'bob', 100)])
    before = path.read_bytes()
    rows = module.logins(path, now=100)
    assert len(rows) == 1 and rows[0]['username'] == 'alice' and rows[0]['browser_logins'] == 2
    assert 'never-show' not in repr(rows)
    assert path.read_bytes() == before
    assert module.logins(path, now=201) == []
