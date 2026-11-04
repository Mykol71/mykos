"""Explicit, expiring PXE jobs. No job is armed by installation or discovery."""
import hashlib
import ipaddress
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
try:
    from fastapi import HTTPException
except ImportError:
    # Root CLI uses the distribution interpreter; the web framework lives in
    # the web venv. Storage/validation do not require installing FastAPI globally.
    class HTTPException(ValueError):
        def __init__(self, status_code, detail):
            self.status_code, self.detail = status_code, detail
            super().__init__(detail)
from mykos_config import BASE

ROOT = BASE / 'data/pxe'
MAC = re.compile(r'(?:[0-9a-f]{2}-){5}[0-9a-f]{2}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z')
ALLOWED_ASSETS = {'boot.wim','install.wim','wimboot','curl.exe','curl-ca-bundle.crt','mykos-pe-guard.exe','chrome.msi'}
PROFILES = ['windows11']
STATES = ('armed', 'booting', 'applying', 'firstboot', 'complete', 'failed', 'revoked')


def mac(value):
    value = str(value).lower().replace(':', '-')
    if not MAC.fullmatch(value) or value in ('00-00-00-00-00-00', 'ff-ff-ff-ff-ff-ff'):
        raise ValueError('Use a unicast Ethernet MAC address')
    if int(value[:2], 16) & 1: raise ValueError('Multicast MAC is not a desktop')
    return value


def connect():
    connection = sqlite3.connect(ROOT / 'jobs.db', timeout=10)
    connection.row_factory = sqlite3.Row
    # Expired leases must not retain a recoverable desktop password or ticket.
    # Keep customer/MAC/history columns, but discard the consumed configuration.
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
        now=int(time.time())
        if connection.execute("SELECT 1 FROM jobs WHERE expires<=? AND state IN ('armed','booting','applying','firstboot') LIMIT 1",(now,)).fetchone():
            connection.execute("UPDATE jobs SET state='failed',ticket='',config='{}',detail='Staging lease expired' "
                               "WHERE expires<=? AND state IN ('armed','booting','applying','firstboot')",(now,))
            connection.commit()
    return connection


def initialize():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS jobs ('
                  'id TEXT PRIMARY KEY, mac TEXT NOT NULL, customer TEXT NOT NULL, profile TEXT NOT NULL, '
                  'state TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL, '
                  'ticket TEXT NOT NULL, client_ip TEXT, config TEXT NOT NULL, detail TEXT NOT NULL DEFAULT \'\')')
    (ROOT / 'jobs.db').chmod(0o600)


def settings():
    try: data = json.loads((ROOT / 'settings.json').read_text())
    except (OSError, ValueError): raise HTTPException(503, 'PXE staging is not configured')
    if not data.get('enabled'): raise HTTPException(503, 'PXE staging is disabled')
    return data


def source_allowed(address, cfg=None):
    cfg = cfg or settings()
    try: ip = ipaddress.ip_address(address)
    except ValueError: return False
    return any(ip in ipaddress.ip_network(net) for net in cfg['allowed_networks'])


def configure(origin, networks):
    url = urlsplit(origin)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('An HTTPS origin without credentials is required')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', url.hostname) or any(ord(ch) <= 32 or ch in (chr(34),chr(92),'%') for ch in origin): raise ValueError('Use a DNS hostname for HTTPS')
    if url.path not in ('', '/', '/mykos', '/mykos/'): raise ValueError('Use the MYKOS HTTPS origin')
    nets = [str(ipaddress.ip_network(v, strict=True)) for v in networks]
    if not nets or any(ipaddress.ip_network(n).prefixlen == 0 for n in nets): raise ValueError('Specify the LAN or customer VPN source subnet')
    initialize()
    cfg = {'enabled': True, 'origin': origin.rstrip('/').removesuffix('/mykos'), 'allowed_networks': nets}
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM jobs WHERE expires>? AND state IN ('armed','booting','applying','firstboot')",(int(time.time()),)).fetchone():
            if settings() != cfg: raise ValueError('Finish or revoke active staging jobs before changing the staging network')
        private_write(ROOT / 'settings.json', json.dumps(cfg, indent=2))
    return cfg


def private_write(path, data):
    temp = path.with_name(path.name + '.' + secrets.token_hex(6))
    with temp.open('x', encoding='utf-8') as f:
        temp.chmod(0o600); f.write(data)
    temp.replace(path)


def register_asset(path, name):
    if name not in ALLOWED_ASSETS: raise ValueError('Asset name is not an approved staging payload')
    path = Path(path)
    if path.is_symlink() or not path.is_file(): raise ValueError('Asset must be a regular file')
    digest = file_sha(path)
    index_path = ROOT / 'assets.json'
    item = {'path': str(path.resolve()), 'sha256': digest, 'size': path.stat().st_size}
    # Serialize media selection with enrollment. A running install pins the
    # current files; replacing them must wait until its lease has ended.
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        try: index = json.loads(index_path.read_text())
        except FileNotFoundError: index = {}
        if index.get(name) != item and c.execute(
            "SELECT 1 FROM jobs WHERE expires>? AND state IN ('armed','booting','applying','firstboot')",
            (int(time.time()),)).fetchone():
            raise ValueError('Finish or revoke active staging jobs before replacing media')
        index[name] = item
        private_write(index_path, json.dumps(index, indent=2))
    return item



def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()


def assets():
    try: return json.loads((ROOT / 'assets.json').read_text())
    except (OSError, ValueError): raise HTTPException(503, 'PXE media has not been imported')


def asset(name):
    if name not in ALLOWED_ASSETS: raise HTTPException(404, 'Asset not found')
    item = assets().get(name)
    if not item: raise HTTPException(404, 'Asset not found')
    p = Path(item['path'])
    if p.is_symlink() or not p.is_file() or p.stat().st_size != item['size']:
        raise HTTPException(503, 'Imported PXE asset changed; reimport it')
    return item


def enroll(target, customer, profile, password, *, hostname=None, minutes=120, allow_wipe=False, apps=False, image_index=1):
    target = mac(target); settings(); media = assets()
    required = ('boot.wim', 'install.wim', 'wimboot', 'curl.exe', 'curl-ca-bundle.crt', 'mykos-pe-guard.exe', 'chrome.msi')
    for name in required: asset(name)
    if not re.fullmatch(r'[0-9]{8}', customer): raise ValueError('Customer ID must be eight digits')
    if profile != 'windows11': raise ValueError('Choose windows11')
    if not password or len(password) > 128 or any(ord(ch) < 32 for ch in password): raise ValueError('Invalid local-account password')
    hostname = hostname or (('MYKOS' if profile=='windows11' else profile.upper()) + '-' + target.replace('-', '')[-6:])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,14}', hostname): raise ValueError('Windows hostname must be 1–15 letters, digits or hyphens')
    if not 5 <= minutes <= 1440 or not 1 <= image_index <= 100: raise ValueError('Invalid lifetime or WIM index')
    if apps:raise ValueError('Application installation requires a business extension')
    cfg = {'username':'mykos','password':password,'workgroup':'WORKGROUP','hostname':hostname,
           'allow_wipe':bool(allow_wipe),'apps':False,'image_index':image_index,'assets':media}
    now = int(time.time()); key = secrets.token_hex(16)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if assets() != media: raise ValueError('Media changed while arming; retry with the current image')
        active = c.execute("SELECT 1 FROM jobs WHERE mac=? AND expires>? AND state IN ('armed','booting','applying','firstboot')", (target, now)).fetchone()
        if active: raise ValueError('This MAC already has an active job; revoke it explicitly before staging again')
        c.execute('INSERT INTO jobs(id,mac,customer,profile,state,created,expires,ticket,config) VALUES(?,?,?,?,?,?,?,?,?)',
                  (key,target,customer,profile,'armed',now,now+minutes*60,secrets.token_urlsafe(32),json.dumps(cfg)))
    return {'id': key, 'mac': target, 'customer': customer, 'profile': profile, 'state': 'armed', 'expires': now+minutes*60}


def public_job(row):
    return {**{k: row[k] for k in ('id','mac','customer','profile','state','created','expires','detail')},'hostname':json.loads(row['config']).get('hostname')}


def jobs():
    with connect() as c: return [public_job(row) for row in c.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 200')]


def revoke(key):
    with connect() as c:
        c.execute("UPDATE jobs SET state='revoked',ticket='',config='{}' WHERE id=?", (key,))


def claim(target, address):
    try: target = mac(target)
    except ValueError: raise HTTPException(400, 'Invalid target MAC')
    if not source_allowed(address): raise HTTPException(403, 'Staging is not enabled for this source subnet')
    now = int(time.time())
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute("SELECT * FROM jobs WHERE mac=? AND expires>? AND state IN ('armed','booting') ORDER BY created DESC LIMIT 1", (target,now)).fetchone()
        if not row: raise HTTPException(404, 'No armed installation for this MAC')
        if row['client_ip'] and row['client_ip'] != address: raise HTTPException(409, 'This job is already claimed by another address')
        c.execute("UPDATE jobs SET state='booting',client_ip=? WHERE id=?", (address,row['id']))
    return dict(row)


def authorize(key, ticket, address):
    with connect() as c: row = c.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone()
    if not row or row['expires'] <= time.time() or row['state'] in ('complete','failed','revoked'):
        raise HTTPException(403, 'Staging job is expired or closed')
    if not row['ticket'] or not secrets.compare_digest(row['ticket'], ticket or '') or row['client_ip'] != address:
        raise HTTPException(403, 'Invalid staging lease')
    if not source_allowed(address): raise HTTPException(403, 'Source subnet is not allowed')
    return dict(row)


def status(row, phase):
    if phase == 'applying' and row['state'] != 'booting':
        raise HTTPException(409, 'The disk-erase phase was already consumed')
    transitions = {'booting': ('applying','failed'), 'applying': ('firstboot','failed'), 'firstboot': ('complete','failed')}
    if phase not in transitions.get(row['state'], ()) and phase != row['state']:
        raise HTTPException(409, 'Invalid staging state transition')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = c.execute('SELECT state FROM jobs WHERE id=?', (row['id'],)).fetchone()
        if not current or current['state'] != row['state']: raise HTTPException(409, 'Job changed; reload status')
        # Strip credentials as soon as the installed OS has consumed the answer file.
        config = json.loads(row['config'])
        if phase in ('firstboot','complete','failed'): config.pop('password', None)
        c.execute('UPDATE jobs SET state=?,config=?,ticket=? WHERE id=?', (phase,json.dumps(config),'' if phase in ('complete','failed') else row['ticket'],row['id']))


def answer(row):
    cfg = json.loads(row['config'])
    if 'password' not in cfg: raise HTTPException(410, 'Account provisioning was already consumed')
    ns = 'urn:schemas-microsoft-com:unattend'; wcm = 'http://schemas.microsoft.com/WMIConfig/2002/State'
    ET.register_namespace('',ns); ET.register_namespace('wcm',wcm)
    def sub(parent, tag, text=None, **attrs):
        node = ET.SubElement(parent,'{'+ns+'}'+tag,attrs)
        if text is not None: node.text = str(text)
        return node
    root = ET.Element('{'+ns+'}unattend')
    special = sub(root,'settings',pass_='specialize'); special.attrib={'pass':'specialize'}
    attrs={'processorArchitecture':'amd64','publicKeyToken':'31bf3856ad364e35','language':'neutral','versionScope':'nonSxS'}
    shell = sub(special,'component',name='Microsoft-Windows-Shell-Setup',**attrs)
    sub(shell,'ComputerName',cfg['hostname']); sub(shell,'TimeZone','Central Standard Time')
    join = sub(special,'component',name='Microsoft-Windows-UnattendedJoin',**attrs)
    sub(sub(join,'Identification'),'JoinWorkgroup',cfg['workgroup'])
    oobe = sub(root,'settings'); oobe.set('pass','oobeSystem')
    intl = sub(oobe,'component',name='Microsoft-Windows-International-Core',**attrs)
    for tag in ('InputLocale','SystemLocale','UILanguage','UserLocale'): sub(intl,tag,'en-US')
    shell = sub(oobe,'component',name='Microsoft-Windows-Shell-Setup',**attrs)
    user = sub(sub(sub(shell,'UserAccounts'),'LocalAccounts'),'LocalAccount',**{'{'+wcm+'}action':'add'})
    sub(user,'Name',cfg['username']); sub(user,'DisplayName',cfg['username']); sub(user,'Group','Administrators')
    import base64
    encoded = base64.b64encode((cfg['password']+'Password').encode('utf-16le')).decode()
    for parent in (user, sub(shell,'AutoLogon')):
        pw = sub(parent,'Password'); sub(pw,'Value',encoded); sub(pw,'PlainText','false')
        if parent.tag.endswith('AutoLogon'):
            sub(parent,'Username',cfg['username']); sub(parent,'Enabled','true'); sub(parent,'LogonCount',1)
    settings_node = sub(shell,'OOBE')
    for tag in ('HideEULAPage','HideOEMRegistrationScreen','HideOnlineAccountScreens','HideWirelessSetupInOOBE'): sub(settings_node,tag,'true')
    sub(settings_node,'ProtectYourPC',1)
    command = sub(sub(shell,'FirstLogonCommands'),'SynchronousCommand',**{'{'+wcm+'}action':'add'})
    sub(command,'Order',1); sub(command,'Description','MYKOS desktop staging')
    sub(command,'CommandLine',r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\MYKOS\Stage.ps1')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def expire():
    """Bound retention of expired leases without creating or enabling PXE state."""
    if not (ROOT/'jobs.db').is_file():return
    with connect():pass
