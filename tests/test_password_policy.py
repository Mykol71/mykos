import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest


def module(tmp_path,monkeypatch):
    (tmp_path/'data').mkdir()
    monkeypatch.setitem(sys.modules,'mykos_config',SimpleNamespace(BASE=tmp_path))
    spec=importlib.util.spec_from_file_location('isolated_password_policy',Path(__file__).resolve().parents[1]/'services/web/mykos_users.py')
    users=importlib.util.module_from_spec(spec);spec.loader.exec_module(users)
    policy=tmp_path/'policy.json';monkeypatch.setattr(users,'Path',lambda _:policy)
    return users,policy


def test_account_specific_exception_and_session_revocation(tmp_path,monkeypatch):
    users,policy=module(tmp_path,monkeypatch)
    assert users.password_minimum('mgreen')==12
    policy.write_text(json.dumps({'minimum_length_by_user':{'mgreen':1}}))
    users.set_password('mgreen','short',role='admin',create=True)
    for name in ('other','MGREEN','mgreen2'):
        assert users.password_minimum(name)==12
        with pytest.raises(ValueError):users.set_password(name,'short',create=True)
    with pytest.raises(ValueError):users.set_password('mgreen','')
    with users.connect() as db:db.execute('INSERT INTO sessions VALUES(?,?,?)',('example','mgreen',9999999999))
    users.set_password('mgreen','new')
    with users.connect() as db:
        assert db.execute('SELECT role FROM users WHERE name=?',('mgreen',)).fetchone()[0]=='admin'
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0


@pytest.mark.parametrize('value',[0,13,True,'1'])
def test_invalid_account_policy_fails_closed(tmp_path,monkeypatch,value):
    users,policy=module(tmp_path,monkeypatch)
    policy.write_text(json.dumps({'minimum_length_by_user':{'mgreen':value}}))
    with pytest.raises(ValueError):users.password_minimum('mgreen')
