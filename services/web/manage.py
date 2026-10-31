#!/usr/bin/env python3
"""Local identity manager. Run as mykosweb; never pass passwords as arguments."""
import argparse
import getpass
import json
import os
import time
from mykos_config import BASE
import mykos_users
import mykos_api_keys

def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='kind', required=True)
    users = sub.add_parser('users').add_subparsers(dest='action', required=True)
    for action in ('create', 'passwd', 'role', 'delete', 'revoke-sessions'):
        cmd = users.add_parser(action)
        cmd.add_argument('name')
        if action == 'create': cmd.add_argument('--role', choices=('user','admin'), default='user')
        if action == 'role': cmd.add_argument('role', choices=('user','admin'))
    users.add_parser('list')
    keys = sub.add_parser('keys').add_subparsers(dest='action', required=True)
    cmd = keys.add_parser('create');cmd.add_argument('label');cmd.add_argument('scope', choices=('read','admin'))
    keys.add_parser('list')
    cmd = keys.add_parser('revoke');cmd.add_argument('id')
    a = p.parse_args()
    (BASE / 'data').mkdir(parents=True, exist_ok=True)
    if a.kind == 'keys':
        if a.action == 'create':
            print(mykos_api_keys.create(a.label, a.scope))
        else:
            with mykos_api_keys.connect() as c:
                if a.action == 'list':
                    print(json.dumps([dict(zip(('id','label','scope','created_at','revoked_at'), row)) for row in
                        c.execute('SELECT id,label,scope,created_at,revoked_at FROM api_keys ORDER BY created_at')], indent=2))
                else:
                    result = c.execute('UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL', (int(time.time()), a.id))
                    if not result.rowcount: raise ValueError('No active key with that ID')
                    print('Key revoked:', a.id)
    elif a.action in ('create','passwd'):
        password = getpass.getpass(f'New password ({mykos_users.password_minimum(a.name)}+ characters): ')
        if password != getpass.getpass('Confirm password: '): raise ValueError('Passwords do not match')
        mykos_users.set_password(a.name, password, getattr(a, 'role', None), a.action == 'create')
        print('Account ready:', a.name)
    else:
        with mykos_users.connect() as c:
            if a.action == 'list':
                print(json.dumps([{'name':r[0], 'role':r[1]} for r in c.execute('SELECT name,role FROM users ORDER BY name')], indent=2))
            else:
                mykos_users.validate_name(a.name)
                if not c.execute('SELECT name FROM users WHERE name=?', (a.name,)).fetchone(): raise ValueError('Unknown user')
                if a.action == 'role': c.execute('UPDATE users SET role=? WHERE name=?', (a.role,a.name))
                if a.action == 'delete': c.execute('DELETE FROM users WHERE name=?', (a.name,))
                c.execute('DELETE FROM sessions WHERE name=?', (a.name,))
                print('User updated; sessions revoked:', a.name)

if __name__ == '__main__':
    try: main()
    except (ValueError, OSError) as e: raise SystemExit(str(e))
