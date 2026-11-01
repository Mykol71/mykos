"""Private, reciprocal MYKOS peer registry and pinned HTTPS transport.

Peer credentials never enter browser requests. Browser sessions authorize each
operation at the local broker; machine identities cannot read private notes or
manage browser accounts, API keys, or another peer.
"""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import ssl
import time
from urllib.parse import urlsplit

from fastapi import HTTPException
from mykos_config import BASE

DB = BASE / 'data/peers.db'
MAX_BODY = 32 * 1024 * 1024
UUID = re.compile(r'[0-9a-f]{32}\Z')
SECRET = re.compile(r'[0-9a-f]{64}\Z')


@contextmanager
def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids a window with permissive default file modes.
    try:
        fd = os.open(DB, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        pass
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS identity (id TEXT PRIMARY KEY)')
        conn.execute('CREATE TABLE IF NOT EXISTS peers (id TEXT PRIMARY KEY, node TEXT, name TEXT NOT NULL, '
                     'ip TEXT UNIQUE NOT NULL, local_ip TEXT NOT NULL, origin TEXT, certificate TEXT, '
                     'incoming TEXT, outgoing TEXT, state TEXT NOT NULL, expires INTEGER NOT NULL, created INTEGER NOT NULL)')
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def node_id():
    with connect() as conn:
        row = conn.execute('SELECT id FROM identity WHERE rowid=1').fetchone()
        if row:
            return row[0]
        identifier = secrets.token_hex(16)
        conn.execute('INSERT OR IGNORE INTO identity(rowid,id) VALUES (1,?)', (identifier,))
        return conn.execute('SELECT id FROM identity WHERE rowid=1').fetchone()[0]


def validate_ip(value):
    try:
        value = ipaddress.IPv4Address(value)
        if value.is_loopback or value.is_multicast or value.is_unspecified or value.is_link_local or int(value) == 0xffffffff:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(400, 'A reachable unicast IPv4 address is required')
    return str(value)


def public_origin(value=None):
    value = value or os.environ.get('MYKOS_PUBLIC_ORIGIN', '')
    parsed = urlsplit(value)
    try:
        valid = (parsed.scheme == 'https' and parsed.hostname and parsed.port in (None, 443)
                 and not parsed.username and not parsed.password and parsed.path in ('', '/')
                 and not parsed.query and not parsed.fragment and re.fullmatch(r'[A-Za-z0-9.-]+', parsed.hostname))
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(400, 'Peer origin must be an HTTPS origin on port 443')
    return value.rstrip('/')


def certificate_context(encoded):
    try:
        der = base64.b64decode(encoded, validate=True)
        if not 128 <= len(der) <= 16384:
            raise ValueError()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False  # The invitation explicitly trusts this exact certificate.
        context.verify_mode = ssl.CERT_REQUIRED
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(der))
    except (ValueError, TypeError, ssl.SSLError):
        raise HTTPException(400, 'Invalid peer TLS certificate')
    return context, hashlib.sha256(der).digest()


def identity(local_ip):
    local_ip = validate_ip(local_ip)
    origin = public_origin()
    # Read the public certificate from the local TLS listener, never a private key.
    with socket.create_connection(('127.0.0.1', 443), timeout=5) as raw:
        with ssl._create_unverified_context().wrap_socket(raw, server_hostname=urlsplit(origin).hostname) as tls:
            cert = base64.b64encode(tls.getpeercert(binary_form=True)).decode('ascii')
    certificate_context(cert)
    return {'node': node_id(), 'name': socket.gethostname(), 'ip': local_ip, 'origin': origin, 'certificate': cert}


def validate_identity(data):
    if not isinstance(data, dict) or set(data) != {'node', 'name', 'ip', 'origin', 'certificate'}:
        raise HTTPException(400, 'Invalid peer identity')
    if not isinstance(data['node'], str) or not UUID.fullmatch(data['node']):
        raise HTTPException(400, 'Invalid node identity')
    if not isinstance(data['name'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', data['name']):
        raise HTTPException(400, 'Invalid node name')
    certificate_context(data['certificate'])
    return {**data, 'ip': validate_ip(data['ip']), 'origin': public_origin(data['origin'])}


def vpn(action, identifier, settings=None):
    from container_admin import invoke
    args = ['peer-vpn-' + action, identifier]
    if settings is not None:
        args.append(json.dumps(settings, separators=(',', ':')))
    return invoke(*args)


def get(identifier, active=True):
    if not UUID.fullmatch(identifier):
        raise HTTPException(404, 'Unknown peer')
    with connect() as conn:
        row = conn.execute('SELECT * FROM peers WHERE id=?', (identifier,)).fetchone()
    if not row or row['state'] == 'revoked' or (active and row['state'] != 'active'):
        raise HTTPException(404, 'Unknown peer')
    return dict(row)


def listing():
    with connect() as conn:
        rows = conn.execute("SELECT id,node,name,ip,state,expires FROM peers WHERE state != 'revoked' ORDER BY created").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item.update(vpn('status', item['id']))
        except HTTPException:
            item.update(configured=False, connected=False)
        item['expired'] = item['state'] != 'active' and item['expires'] <= time.time()
        result.append(item)
    return {'node': node_id(), 'name': socket.gethostname(), 'peers': result}


def invite(local_ip, remote_ip):
    local = identity(local_ip)
    remote_ip = validate_ip(remote_ip)
    if remote_ip == local['ip']:
        raise HTTPException(400, 'Select another host')
    identifier, incoming, outgoing, psk = (secrets.token_hex(n) for n in (16, 32, 32, 32))
    expires = int(time.time()) + 900
    try:
        with connect() as conn:
            conn.execute('INSERT INTO peers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (identifier, None, remote_ip, remote_ip, local['ip'], None, None,
                          hashlib.sha256(incoming.encode()).hexdigest(), outgoing, 'pending', expires, int(time.time())))
        vpn('create', identifier, {'local': local['ip'], 'remote': remote_ip, 'secret': psk})
    except BaseException:
        with connect() as conn:
            conn.execute("UPDATE peers SET state='revoked',incoming=NULL,outgoing=NULL WHERE id=?", (identifier,))
        raise
    value = {'version': 1, 'id': identifier, 'host': local, 'expected_ip': remote_ip,
             'incoming': incoming, 'outgoing': outgoing, 'psk': psk, 'expires': expires}
    encoded = base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode()
    return {'invitation': encoded, 'expires': expires, 'id': identifier}


def parse_invitation(encoded):
    try:
        if not isinstance(encoded, str) or len(encoded) > 32768:
            raise ValueError()
        value = json.loads(base64.b64decode(encoded, altchars=b'-_', validate=True))
        if not isinstance(value, dict) or set(value) != {'version','id','host','expected_ip','incoming','outgoing','psk','expires'}:
            raise ValueError()
        if value['version'] != 1 or not UUID.fullmatch(value['id']):
            raise ValueError()
        for key in ('incoming', 'outgoing', 'psk'):
            if not SECRET.fullmatch(value[key]):
                raise ValueError()
        if type(value['expires']) is not int or not time.time() < value['expires'] <= time.time() + 900:
            raise ValueError()
        value['host'] = validate_identity(value['host'])
        value['expected_ip'] = validate_ip(value['expected_ip'])
        return value
    except (ValueError, TypeError, KeyError):
        raise HTTPException(400, 'Invalid or expired pairing invitation')


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, peer):
        self.peer = peer
        context, self.pin = certificate_context(peer['certificate'])
        super().__init__(urlsplit(peer['origin']).hostname, 443, timeout=30, context=context)

    def connect(self):
        raw = socket.create_connection((self.peer['ip'], 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            if not hmac.compare_digest(hashlib.sha256(self.sock.getpeercert(binary_form=True)).digest(), self.pin):
                raise ssl.SSLError('Peer certificate changed; pairing must be renewed')
        except BaseException:
            raw.close()
            raise


def headers(peer, actor=''):
    return {'X-MYKOS-Peer': peer['id'], 'X-MYKOS-Peer-Key': peer['outgoing'],
            'X-MYKOS-Node': node_id(), 'X-MYKOS-Actor': actor,
            'Origin': peer['origin']}


def allowed_path(path, confirming=False):
    if not isinstance(path, str) or not path.startswith('/') or any(x in path for x in ('\\', '%', '\x00', '//')) or '..' in path.split('/'):
        return False
    if path in ('/api/peers/confirm', '/api/peers/disconnect'):
        return confirming
    if path.startswith(('/api/web/peers', '/api/web/notes', '/api/web/personal-notes', '/api/web/id-notes')):
        return False
    return bool(re.fullmatch(r'/api/(?:web(?:/[^?]*)?|nodes(?:/[0-9]{8}/details)?|admin/containers/[^?]+|terminal/(?:permission|window/[A-Za-z0-9][A-Za-z0-9._-]{0,63}|[A-Za-z0-9][A-Za-z0-9._-]{0,63}))', path)
                or re.fullmatch(r'/console/(?:preview-[0-9a-f]{32}|[0-9]{8}|(?:win|wns|mac|ubuntu)[A-Za-z0-9.]+)/[^?]*', path))


def exchange(peer, method, path, body=b'', content_type='application/json', actor='', confirming=False):
    plain_path = path.partition('?')[0]
    if not allowed_path(plain_path, confirming):
        raise HTTPException(403, 'This endpoint is not shared with peers')
    if len(body) > MAX_BODY:
        raise HTTPException(413, 'Peer request too large')
    if not vpn('status', peer['id']).get('connected'):
        raise HTTPException(503, 'Peer VPN is offline')
    connection = PinnedConnection(peer)
    try:
        connection.request(method, '/mykos' + path, body=body,
                           headers={**headers(peer, actor), 'Content-Type': content_type})
        response = connection.getresponse()
        data = response.read(MAX_BODY + 1)
        if len(data) > MAX_BODY or 300 <= response.status < 400:
            raise HTTPException(502, 'Unexpected peer response')
        return response.status, response.getheader('Content-Type', 'application/octet-stream'), data
    except (OSError, http.client.HTTPException):
        raise HTTPException(502, 'Peer HTTPS connection failed; verify its certificate and VPN')
    finally:
        connection.close()


def accept(encoded):
    value = parse_invitation(encoded)
    remote, identifier = value['host'], value['id']
    local = identity(value['expected_ip'])
    if remote['node'] == local['node'] or remote['ip'] == local['ip']:
        raise HTTPException(400, 'Cannot pair a node with itself')
    inserted = False
    try:
        with connect() as conn:
            conn.execute('INSERT INTO peers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (identifier, remote['node'], remote['name'], remote['ip'], local['ip'], remote['origin'], remote['certificate'],
                          hashlib.sha256(value['outgoing'].encode()).hexdigest(), value['incoming'], 'pending', value['expires'], int(time.time())))
        inserted = True
        vpn('create', identifier, {'local': local['ip'], 'remote': remote['ip'], 'secret': value['psk']})
        vpn('connect', identifier)
        status, _, body = exchange(get(identifier, False), 'POST', '/api/peers/confirm', json.dumps(local).encode(), confirming=True)
        if status != 200 or json.loads(body).get('node') != remote['node']:
            raise HTTPException(502, 'Peer did not confirm reciprocal pairing')
        with connect() as conn:
            conn.execute("UPDATE peers SET state='active',expires=0 WHERE id=?", (identifier,))
    except BaseException:
        if inserted:
            # If confirmation reached the remote but its reply was lost, revoke there
            # before local cleanup; an unreachable remote remains visibly offline.
            try:
                exchange(get(identifier, False), 'POST', '/api/peers/disconnect', confirming=True)
            except Exception:
                pass
            try:
                vpn('remove', identifier)
            finally:
                with connect() as conn:
                    conn.execute('DELETE FROM peers WHERE id=?', (identifier,))
        raise
    return {'id': identifier, 'node': remote['node'], 'name': remote['name'], 'state': 'active'}


def authenticate(scope):
    supplied = dict(scope.get('headers', []))
    identifier = supplied.get(b'x-mykos-peer', b'').decode('ascii', 'ignore')
    key = supplied.get(b'x-mykos-peer-key', b'').decode('ascii', 'ignore')
    remote_node = supplied.get(b'x-mykos-node', b'').decode('ascii', 'ignore')
    actor = supplied.get(b'x-mykos-actor', b'').decode('ascii', 'ignore')
    if not SECRET.fullmatch(key) or not UUID.fullmatch(remote_node) or (actor and not SECRET.fullmatch(actor)):
        raise HTTPException(401, 'Invalid peer credential')
    peer = get(identifier, False)
    if not peer['incoming'] or not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), peer['incoming']):
        raise HTTPException(401, 'Invalid peer credential')
    path = scope['path']
    if path.startswith('/mykos/'):
        path = path[6:]
    if not allowed_path(path, confirming=True):
        raise HTTPException(403, 'This endpoint is not shared with peers')
    if peer['state'] == 'pending':
        if path != '/api/peers/confirm' or peer['expires'] <= time.time():
            raise HTTPException(403, 'Pairing is not active')
    elif peer['node'] != remote_node:
        raise HTTPException(403, 'Peer node identity mismatch')
    if not vpn('status', identifier).get('connected'):
        raise HTTPException(503, 'Peer VPN is offline')
    return {'id': identifier, 'node': remote_node, 'name': peer['name'], 'actor': actor}


def principal(request):
    return getattr(request, 'scope', {}).get('state', {}).get('mykos_peer')


class PeerAuthentication:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket') and any(k.lower() == b'x-mykos-peer' for k, _ in scope.get('headers', [])):
            from starlette.concurrency import run_in_threadpool
            try:
                actor = await run_in_threadpool(authenticate, scope)
                scope.setdefault('state', {})['mykos_peer'] = actor
            except HTTPException as error:
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1008})
                else:
                    from starlette.responses import JSONResponse
                    await JSONResponse({'detail': error.detail}, status_code=error.status_code)(scope, receive, send)
                return
        await self.app(scope, receive, send)
