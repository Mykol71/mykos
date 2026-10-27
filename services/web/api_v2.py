"""MYKOS API v2: authenticated read model over existing collectors and actions.
Includes a private admin-only scheduled command queue; inventory/collectors remain read models.
"""
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict, StrictInt, SecretStr
from mykos_admin_api import BackupAction, RepoAction

from generic_monitoring import details as generic_details, extended as generic_extended
from mykos_config import BASE as ROOT
DATA = ROOT / 'data'
SHOP_RE = re.compile(r'[0-9]{8}\Z')
router = APIRouter(prefix='/api/v2', tags=['MYKOS API v2'])

from pane_notes import Pane, NoteUpdate, read as read_pane_note, save as save_pane_note, authorize as authorize_pane_notes
from pane_notes import read_personal as read_personal_note, save_personal as save_personal_note



@router.get('/personal-notes', summary='Read personal notes (mgreen only; excluded from change requests)')
def personal_note(request: Request):
    authorize_pane_notes(request)
    return read_personal_note()


@router.put('/personal-notes', summary='Save personal notes (preserved through updates)')
def update_personal_note(data: NoteUpdate, request: Request):
    authorize_pane_notes(request, write=True)
    return save_personal_note(data)

@router.get('/nodes/{shop}/desktop', summary='Desktop browser console and native login endpoints')
def desktop_connection(shop: str, request: Request):
    administrator(request)
    if not SHOP_RE.fullmatch(shop): raise HTTPException(400, 'Invalid ID')
    from container_admin import invoke
    return invoke('desktop-info', shop)


@router.get('/nodes/{shop}/desktop/rdp', summary='Download the Windows RDP connection file')
def desktop_rdp(shop: str, request: Request):
    from container_admin import rdp_response
    return rdp_response(desktop_connection(shop, request))


@router.get('/notes/{pane}', summary='Read private pane notes (mgreen session only)')
def pane_note(pane: Pane, request: Request):
    authorize_pane_notes(request)
    return read_pane_note(pane)


@router.put('/notes/{pane}', summary='Save private pane notes (mgreen session only)')
def update_pane_note(pane: Pane, data: NoteUpdate, request: Request):
    authorize_pane_notes(request, write=True)
    return save_pane_note(pane, data)


from mykos_api_keys import authenticate as mykos_authenticate_key


def mykos_key_scope(request: Request):
    supplied = request.headers.get('X-MYKOS-API-Key', '')
    scope = mykos_authenticate_key(supplied) if supplied else None
    if scope is None:
        raise HTTPException(401, 'Missing, invalid or revoked API key',
                            headers={'WWW-Authenticate': 'ApiKey'})
    return scope


def authenticated(request: Request):
    mykos_key_scope(request)


def administrator(request: Request):
    if mykos_key_scope(request) != 'admin':
        raise HTTPException(403, 'Admin API key required')


def validate_shop(shop: str):
    if not SHOP_RE.fullmatch(shop):
        raise HTTPException(400, 'ID must be exactly eight digits')


def json_file(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return None


def snapshot():
    inventory = json_file(ROOT / 'inventory.json') or {}
    status = json_file(DATA / 'status.json') or {}
    details = generic_details(json_file(DATA / 'details.json') or {})
    extended = generic_extended(json_file(DATA / 'extended.json') or {})
    now = int(time.time())
    def age_of(d):
        t = d.get('collected_at') if isinstance(d, dict) else None
        return now - t if isinstance(t, (int, float)) else None
    status_age = age_of(status)
    ext_age = age_of(extended)
    detail_age = age_of(details)
    status_fresh = status_age is not None and 0 <= status_age <= 120
    ext_fresh = ext_age is not None and 0 <= ext_age <= 120
    detail_fresh = detail_age is not None and 0 <= detail_age <= 120
    observations = status.get('observations', {}) if status_fresh else {}
    extended_nodes = extended.get('nodes', {}) if ext_fresh else {}
    detail_nodes = details.get('details', {}) if detail_fresh else {}
    nodes=[]
    for entry in inventory.get('nodes', []):
        if not isinstance(entry, dict): continue
        shop = str(entry.get('id', ''))
        if not SHOP_RE.fullmatch(shop): continue
        obs = observations.get(shop, {}) or {}
        ext = extended_nodes.get(shop, {}) or {}
        det = detail_nodes.get(shop, {}) or {}
        node = dict(entry)
        node['shopcode'] = shop
        node['status'] = obs.get('status', 'unknown')
        node['vpn_status'] = obs.get('vpn_status', 'unknown')
        node['observation'] = obs
        node['details'] = det
        node['extended'] = ext
        node['network'] = {'ipv4': ext.get('ipv4', ext.get('eth1_ip', 'Unknown')), 'interface': ext.get('interface'), 'eth1_ipv4': ext.get('eth1_ip', 'Unknown'), 'addresses': det.get('ips', [])}
        node['resources'] = ext.get('resources', {})
        node['vpn'] = ext.get('vpn', {})
        node['running'] = ext.get('running', det.get('running'))
        node['image'] = det.get('image', 'Unknown')
        node['os_type'] = det.get('os_type', 'Unknown')
        node['os_version'] = det.get('os_version', 'Unknown')
        node['os_codename'] = det.get('os_codename')
        nodes.append(node)
    return {'collected_at': {'status': status.get('collected_at'),
                             'details': details.get('collected_at'),
                             'extended': extended.get('collected_at')},
            'fresh': {'status': status_fresh, 'details': detail_fresh, 'extended': ext_fresh},
            'age_seconds': {'status': status_age, 'details': detail_age, 'extended': ext_age},
            'nodes': nodes}


def node_by_shop(shop: str):
    validate_shop(shop)
    for n in snapshot()['nodes']:
        if n['shopcode'] == shop: return n
    raise HTTPException(404, 'ID not in inventory')


def safe_helper(*args):
    from container_admin import invoke
    return invoke(*args)


def select_payload(node, include):
    if not include or include == 'all': return node
    requested = {x.strip() for x in include.split(',') if x.strip()}
    available = {'observation','details','extended','network','resources','vpn'}
    invalid = requested - available
    if invalid: raise HTTPException(400, 'Unknown include fields: '+', '.join(sorted(invalid)))
    base = {k: v for k, v in node.items() if k not in available}
    base.update({k: node[k] for k in requested})
    return base


@router.get('/overview', summary='Complete monitoring snapshot')
def overview(request: Request):
    authenticated(request)
    data = snapshot()
    nodes = data['nodes']
    return {'host': host(request), 'counts': {'total': len(nodes),
             'online': sum(n['status']=='online' for n in nodes),
             'vpn_up': sum(n['vpn_status']=='up' for n in nodes)}, **data}


@router.get('/host', summary='Host OS, kernel, uptime and backup storage')
def host(request: Request):
    authenticated(request)
    import os
    import platform
    from container_admin import invoke
    # /backups access is intentionally delegated to existing privileged helper.
    try:
        storage = invoke('diskfree')
    except Exception:
        storage = {'free': 'Unknown'}
    try:
        os_name = next((line.split('=',1)[1].strip().strip('"')
            for line in Path('/etc/os-release').read_text().splitlines()
            if line.startswith('PRETTY_NAME=')), 'Linux')
    except OSError:
        os_name = 'Linux'
    try:
        uptime = float(Path('/proc/uptime').read_text().split()[0])
    except (OSError, ValueError, IndexError):
        uptime = None
    from host_metrics import internal_ip
    return {'hostname': platform.node(), 'os': os_name, 'internal_ip': internal_ip(),
            'server_time': time.strftime('%m/%d/%Y %I:%M:%S %p %Z'),
            'kernel': platform.release(), 'uptime_seconds': uptime,
            'storage': {'backups': storage}, 'metrics': host_metrics(request)}


@router.get('/host/metrics', summary='Live host CPU, memory, load, disk and uptime')
def host_metrics(request: Request):
    authenticated(request)
    from host_metrics import snapshot as metrics_snapshot
    from host_history import latest
    return latest() or metrics_snapshot()


@router.get('/host/history', summary='Retained host performance history, up to 30 minutes')
def host_history(request: Request, since: float = 0):
    authenticated(request)
    from host_history import read
    try: return read(since)
    except ValueError as error: raise HTTPException(400, str(error))


@router.get('/host/status', summary='Host status row including OS version and VPN state')
def host_status(request: Request):
    return host_metrics(request)


@router.get('/host/diagnostics/{action}', summary='Host logs, processes or IPsec status')
def host_diagnostic(action: str, request: Request):
    authenticated(request)
    if action not in ('logs', 'top', 'ipsec'):
        raise HTTPException(404, 'Unknown host action')
    return safe_helper('host-' + action)


from container_admin import HostReboot


@router.post('/host/reboot', summary='Schedule host reboot after exact-hostname confirmation')
def host_reboot(data: HostReboot, request: Request):
    administrator(request)
    return safe_helper('host-reboot', data.hostname)


@router.websocket('/terminal/host')
async def host_terminal_websocket(ws: WebSocket):
    try:
        administrator(ws)
    except HTTPException:
        await ws.close(code=1008)
        return
    action = ws.query_params.get('action', 'shell')
    if action not in ('shell', 'add-vpn', 'remove-vpn'):
        await ws.close(code=1008)
        return
    from app import mykos_terminal_bridge
    if action == 'shell':
        await mykos_terminal_bridge(ws, 'host')
    else:
        await mykos_terminal_bridge(ws, 'host', action=action)


@router.get('/backups', summary='Backup accounts, subscriber status and storage')
def backups(request: Request, refresh: bool = False):
    authenticated(request)
    from mykos_admin_api import listing
    return listing('backup', refresh)


@router.get('/backups/{code}', summary='Inspect one backup account')
def backup_account(request: Request, code: str, refresh: bool = False):
    authenticated(request)
    validate_shop(code)
    for account in backups(request, refresh).get('accounts', []):
        if account.get('code') == code:
            return account
    raise HTTPException(404, 'Backup account not found')


@router.post('/backups', summary='Manage backup accounts, keys and subscribers (admin only)')
def backup_action(request: Request, data: BackupAction):
    administrator(request)
    from mykos_admin_api import perform
    return perform('backup', data.action, data.code, data.model_dump(exclude={'action', 'code'}))


@router.get('/backups/{code}/keys', summary='Read backed-up public/private client keys (admin only)')
def backup_keys(request: Request, code: str):
    administrator(request)
    validate_shop(code)
    from mykos_admin_api import perform
    return perform('backup', 'keys', code, {})


@router.get('/repos', summary='Repository inventory, storage, Apache and sync state')
def repos(request: Request, refresh: bool = False):
    authenticated(request)
    from mykos_admin_api import listing
    return listing('repo', refresh)


@router.get('/repos/{name}', summary='Inspect one repository')
def repository(request: Request, name: str, refresh: bool = False):
    authenticated(request)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name):
        raise HTTPException(400, 'Invalid repository name')
    for item in repos(request, refresh).get('repos', []):
        if item.get('name') == name:
            return item
    raise HTTPException(404, 'Repository not found')


@router.post('/repos', summary='Delete, enable, disable or sync repositories (admin only)')
def repo_action(request: Request, data: RepoAction):
    administrator(request)
    from mykos_admin_api import perform
    return perform('repo', data.action, data.name, {})


@router.websocket('/terminal/{shop}')
async def terminal_websocket(ws: WebSocket, shop: str):
    # Non-browser WebSocket clients can supply the API key in handshake headers.
    action = ws.query_params.get('action', 'shell')
    try:
        administrator(ws)
        if action == 'image-shell':
            from image_build_api import prepared_os
            prepared_os(shop)
        else: validate_shop(shop)
    except HTTPException:
        await ws.close(code=1008)
        return
    action = ws.query_params.get('action', 'shell')
    if action not in ('shell', 'add-vpn', 'remove-vpn', 'image-shell'):
        await ws.close(code=1008)
        return
    from app import mykos_terminal_bridge
    if action == 'shell':
        await mykos_terminal_bridge(ws, shop)
    else:
        await mykos_terminal_bridge(ws, shop, action=action)


@router.get('/host/os')
def host_os(request: Request):
    data=host(request)
    return {k:data[k] for k in ('hostname','os','kernel')}


@router.get('/host/kernel')
def host_kernel(request: Request):
    return {'kernel': host(request)['kernel']}


@router.get('/host/storage')
def host_storage(request: Request):
    return host(request)['storage']


@router.get('/nodes', summary='List all inventory nodes with optional filters')
def nodes(request: Request, status: Optional[str]=None,
          vpn: Optional[str]=None, running: Optional[bool]=None,
          include: str='all'):
    authenticated(request)
    data=snapshot()
    result=data['nodes']
    if status is not None: result=[n for n in result if n['status']==status]
    if vpn is not None: result=[n for n in result if n['vpn_status']==vpn]
    if running is not None: result=[n for n in result if n['running'] is running]
    return {'count':len(result), 'fresh':data['fresh'],
            'nodes':[select_payload(n, include) for n in result]}


@router.get('/nodes/{shop}', summary='All collected data for one node')
def node(request: Request, shop: str):
    authenticated(request)
    return node_by_shop(shop)


@router.get('/nodes/{shop}/status')
def node_status(request: Request, shop: str):
    authenticated(request)
    n=node_by_shop(shop)
    return {'shopcode':shop, 'status':n['status'], 'running':n['running'],
            'vpn_status':n['vpn_status'], 'started_at_epoch':n['extended'].get('started_at_epoch'),
            'started_at': n['details'].get('started_at'),
            'image': n['image'], 'os_type': n['os_type'], 'os_version': n['os_version'], 'os_codename':n.get('os_codename')}


@router.get('/nodes/{shop}/network')
def node_network(request: Request, shop: str):
    authenticated(request)
    return {'shopcode':shop, **node_by_shop(shop)['network']}


@router.get('/nodes/{shop}/resources')
def node_resources(request: Request, shop: str):
    authenticated(request)
    return {'shopcode':shop, **node_by_shop(shop)['resources']}


@router.get('/nodes/{shop}/vpn')
def node_vpn(request: Request, shop: str):
    authenticated(request)
    n=node_by_shop(shop)
    return {'shopcode':shop, **n['vpn'], 'status':n['vpn_status']}


@router.get('/nodes/{shop}/details')
def node_details(request: Request, shop: str):
    authenticated(request)
    return {'shopcode':shop, **node_by_shop(shop)['details']}


@router.get('/nodes/{shop}/ipsec', summary='Live IPsec status from the existing helper')
def node_ipsec(request: Request, shop: str):
    authenticated(request)
    node_by_shop(shop)
    return safe_helper('ipsec',shop)


@router.get('/nodes/{shop}/logs', summary='Live console logs')
def node_logs(request: Request, shop: str):
    authenticated(request)
    node_by_shop(shop)
    return safe_helper('logs',shop)


@router.get('/nodes/{shop}/processes', summary='Live container processes and CPU/memory')
def node_processes(request: Request, shop: str):
    authenticated(request)
    node_by_shop(shop)
    return safe_helper('top',shop)


@router.get('/nodes/{shop}/checkit', summary='Execute the existing checkit diagnostic (admin only)')
def node_checkit(request: Request, shop: str):
    administrator(request)
    node_by_shop(shop)
    return safe_helper('checkit',shop)


@router.get('/containers', summary='Managed container inventory from helper')
def containers(request: Request):
    authenticated(request)
    return {'containers': safe_helper('list')}


@router.get('/actions', summary='Discover actions and their permissions')
def actions(request: Request):
    authenticated(request)
    return {'actions': {'purge_plan': 'GET /api/v2/system/purge',
                        'purge': 'POST /api/v2/system/purge',
                        'purge_status': 'GET /api/v2/system/purge/status',
                        'stage': 'POST /api/v2/actions/stage',
                        'images': 'GET /api/v2/images',
                        'stop': 'POST /api/v2/nodes/{shop}/stop',
                        'start': 'POST /api/v2/nodes/{shop}/start',
                        'snapshot': 'POST /api/v2/nodes/{shop}/snapshot',
                        'snapshots': 'GET /api/v2/snapshots',
                        'restore_snapshot': 'POST /api/v2/nodes/{shop}/snapshots/{identifier}/restore',
                        'delete_snapshot': 'DELETE /api/v2/nodes/{shop}/snapshots/{identifier}',
                        'checkit': 'GET /api/v2/nodes/{shop}/checkit',
                        'backups': 'POST /api/v2/backups',
                        'repos': 'POST /api/v2/repos',
                        'terminal': 'WS /api/v2/terminal/{shop}',
                        'add_vpn': 'WS /api/v2/terminal/{shop}?action=add-vpn',
                        'remove_vpn': 'WS /api/v2/terminal/{shop}?action=remove-vpn'},
            'action_options': {'backups': ['create', 'key', 'subscriber', 'delete', 'keys'],
                               'repos': ['delete', 'enable', 'disable', 'sync']},
            'admin_required': True}


@router.get('/images', summary='Startable OS images and active installation consoles')
def staging_images(request: Request):
    authenticated(request)
    return safe_helper('stage-images')


@router.get('/actions/stage/{shop}/check', summary='Check eligibility and available backup')
def stage_check(request: Request, shop: str):
    administrator(request)
    validate_shop(shop)
    return safe_helper('stage-check',shop)


@router.get('/jobs/{shop}', summary='Read staging job state')
def stage_job(request: Request, shop: str):
    administrator(request)
    validate_shop(shop)
    return safe_helper('stage-status',shop)


class StageRequest(BaseModel):
    shop: str
    restore: bool=False
    os: Optional[str]=None


@router.post('/actions/stage', summary='Start a container staging job (admin only)')
def start_stage(request: Request, data: StageRequest):
    administrator(request)
    validate_shop(data.shop)
    # Existing staging helper enforces exclusive lock, container checks, and backup logic.
    args = ['stage-start', data.shop, 'yes' if data.restore else 'no']
    if data.os is not None: args.append(data.os)
    return safe_helper(*args)


from container_admin import ContainerDelete

@router.delete('/nodes/{shop}', summary='Delete a container and its private saved data (admin only)')
def delete_node(request: Request, shop: str, data: ContainerDelete):
    administrator(request)
    validate_shop(shop)
    if data.confirmation != shop:
        raise HTTPException(400, 'Confirm the exact container ID before deletion')
    return safe_helper('delete-pos', shop, data.confirmation)


@router.post('/nodes/{shop}/start', summary='Start an existing persistent container (admin only)')
def start_node(request: Request, shop: str):
    administrator(request)
    validate_shop(shop)
    return safe_helper('start-pos',shop)


class MemoryTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_mib: StrictInt


@router.get('/nodes/{shop}/memory', summary='Inspect VM balloon driver support (admin only)')
def guest_memory(request: Request, shop: str):
    administrator(request)
    validate_shop(shop)
    return safe_helper('memory-info', shop)


@router.put('/nodes/{shop}/memory', summary='Request a guarded VM balloon target (admin only)')
def set_guest_memory(request: Request, shop: str, data: MemoryTarget):
    administrator(request)
    validate_shop(shop)
    if data.target_mib != 2048:
        raise HTTPException(400, 'Memory target must be 2048 MiB')
    return safe_helper('memory-set', shop, str(data.target_mib))


class MemoryAllocation(MemoryTarget):
    confirmation: str


@router.put('/nodes/{shop}/memory/allocation', summary='Cold-restart a VM with a new RAM allocation (admin only)')
def configure_guest_memory(request: Request, shop: str, data: MemoryAllocation):
    administrator(request)
    validate_shop(shop)
    if data.confirmation != shop or data.target_mib != 2048:
        raise HTTPException(400, 'Confirm the exact instance ID and select 2048 MiB')
    return safe_helper('memory-configure', shop, str(data.target_mib), data.confirmation)


@router.post('/nodes/{shop}/snapshot', summary='Snapshot live container state and leave it running (admin only)')
def snapshot_node(request: Request, shop: str):
    administrator(request)
    validate_shop(shop)
    return safe_helper('snapshot-pos',shop)


@router.get('/nodes/{shop}/snapshots', summary='List saved snapshots for a container')
def list_node_snapshots(request: Request, shop: str):
    authenticated(request)
    validate_shop(shop)
    return safe_helper('list-snapshots',shop)


@router.post('/nodes/{shop}/stop', summary='Stop selected managed POS (admin only)')
def stop_node(request: Request, shop: str):
    administrator(request)
    validate_shop(shop)
    return safe_helper('stop-pos',shop)


@router.get('/openapi.json', include_in_schema=False)
def openapi_v2(request: Request):
    schema = get_openapi(title='MYKOS Infrastructure API v2', version='2.0.0',
                       description='Authenticated monitoring, lifecycle operations, and admin-only scheduled commands',
                       servers=[{'url': '/mykos'}],
                       routes=[r for r in router.routes if getattr(r,'path','').startswith('/api/v2/')
                               and getattr(r,'include_in_schema',False)])
    schema.setdefault('components', {}).setdefault('securitySchemes', {})['MYKOSApiKey'] = {
        'type': 'apiKey', 'in': 'header', 'name': 'X-MYKOS-API-Key',
        'description': 'Create keys on the server using mykos-api-keys.'
    }
    schema['components']['securitySchemes']['MYKOSNotesSession'] = {'type':'apiKey','in':'cookie','name':'mykos_session','description':'Authenticated mgreen session required; API keys do not identify the note owner.'}
    for path, entry in schema.get('paths', {}).items():
        for operation in entry.values():
            if isinstance(operation, dict) and 'responses' in operation:
                operation['security'] = [{'MYKOSNotesSession' if '/notes/' in path else 'MYKOSApiKey': []}]
    return schema


# Keep the docs theme self-contained so deployments cannot omit its stylesheet.
DOCS_THEME = """
/* Match the dashboard while retaining Swagger's method colors. */
:root { color-scheme: dark; }
html, body { margin: 0; background: #0b1120; color: #e2e8f0; }
.swagger-ui, .swagger-ui :is(.info, .info p, .info li, .info table, .opblock, .opblock-description-wrapper p, .opblock-external-docs-wrapper p, .opblock-title_normal, .model, .model-title, .parameter__name, .parameter__type, .parameter__in, .response-col_status, .response-col_links, .tab li, label, select, input, textarea, button) { font-family: monospace; }
.swagger-ui { font-size: 12px; color: #e2e8f0; }
.swagger-ui .wrapper { max-width: 1500px; padding: 0 28px; }
.swagger-ui .info { margin: 28px 0; }
.swagger-ui .info .title { font-size: 24px; color: #e2e8f0; }
.swagger-ui :is(.info p, .info li, .info table, .info h1, .info h2, .info h3, .info h4, .info h5, .opblock-tag, .opblock-summary-description, .opblock-description-wrapper p, .opblock-external-docs-wrapper p, .opblock-title_normal, .model, .model-title, .parameter__name, .parameter__type, .response-col_status, .response-col_links, .responses-inner h4, .responses-inner h5, .tab li, label, table thead tr td, table thead tr th) { color: #e2e8f0; }
.swagger-ui :is(.parameter__in, .parameter__deprecated, .prop-format, .model-hint, .opblock-summary-operation-id, .opblock-summary-path, .opblock-summary-path__deprecated, .model .property.primitive) { color: #94a3b8; }
.swagger-ui a, .swagger-ui .info a { color: #38bdf8; }
.swagger-ui .scheme-container { background: #111c30; box-shadow: none; border-block: 1px solid #334155; padding: 16px 0; }
.swagger-ui :is(select, input[type=text], input[type=password], input[type=email], input[type=file], textarea) { background: #0b1120; color: #e2e8f0; border: 1px solid #334155; border-radius: 6px; }
.swagger-ui select { color-scheme: dark; }
.swagger-ui input::placeholder, .swagger-ui textarea::placeholder { color: #94a3b8; opacity: 1; }
.swagger-ui .btn { color: #e2e8f0; background: #1e293b; border-color: #475569; box-shadow: none; border-radius: 7px; }
.swagger-ui .btn.authorize { color: #4ade80; border-color: #4ade80; }
.swagger-ui .btn.authorize svg { fill: #4ade80; }
.swagger-ui .btn.execute { background: #075985; border-color: #38bdf8; color: #e2e8f0; }
.swagger-ui :is(.opblock, section.models) { border-radius: 10px; box-shadow: none; }
.swagger-ui .opblock .opblock-section-header { background: #1e293b; box-shadow: none; }
.swagger-ui .opblock .opblock-section-header :is(h4, label) { color: #e2e8f0; }
.swagger-ui :is(.opblock-tag, section.models, section.models h4, table thead tr td, table thead tr th) { border-color: #334155; }
.swagger-ui section.models { background: #121d30; }
.swagger-ui section.models h4 { color: #e2e8f0; }
.swagger-ui section.models .model-container { background: #1e293b; }
.swagger-ui section.models .model-container:hover { background: #29384d; }
.swagger-ui .model-box { background: #121d30; }
.swagger-ui :is(.model-toggle::after, .arrow, .expand-operation svg, .opblock-control-arrow svg, .authorization__btn svg, .copy-to-clipboard svg, .models-control svg, .model-box-control svg, .expand-methods svg) { filter: brightness(0) invert(.8); }
.swagger-ui .highlight-code, .swagger-ui .highlight-code > .microlight, .swagger-ui .microlight { background: #0b1120 !important; color: #e2e8f0 !important; border-radius: 6px; }
.swagger-ui .markdown code, .swagger-ui .renderedMarkdown code { background: #1e293b; color: #7dd3fc; }
.swagger-ui .dialog-ux .backdrop-ux { background: #000b; }
.swagger-ui .dialog-ux .modal-ux { background: #121d30; border: 1px solid #334155; border-radius: 10px; color: #e2e8f0; }
.swagger-ui .dialog-ux .modal-ux :is(h3, h4, p, label) { color: #e2e8f0; }
.swagger-ui .dialog-ux .modal-ux-header { border-color: #334155; }
.swagger-ui .dialog-ux .modal-ux-header .close-modal svg { fill: #e2e8f0; }
.swagger-ui .auth-container { border-color: #334155; }
.swagger-ui :is(button, a, input, select, textarea):focus-visible { outline: 2px solid #38bdf8; outline-offset: 3px; }
.swagger-ui .info .title, .swagger-ui .opblock-tag, .swagger-ui section.models h4 { font-family: monospace; }
.swagger-ui .opblock .opblock-summary-description { color: #94a3b8; font-family: monospace; }
.swagger-ui .opblock .opblock-summary-path, .swagger-ui .opblock .opblock-summary-path__deprecated { color: #e2e8f0; }
.swagger-ui .opblock .opblock-summary-method { color: #0b1120; text-shadow: none; }
.swagger-ui .response-control-media-type__accept-message { color: #4ade80; }
"""


@router.get('/docs', include_in_schema=False)
def docs_v2(request: Request):
    response = get_swagger_ui_html(
        openapi_url='/mykos/api/v2/openapi.json',
        title='MYKOS API v2 Docs',
        swagger_js_url='https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js',
        swagger_css_url='https://unpkg.com/swagger-ui-dist@5/swagger-ui.css'
    )

    html = response.body.decode('utf-8').replace(
        '</head>', '<style id="mykos-api-theme">' + DOCS_THEME + '</style></head>')
    return HTMLResponse(html)


@router.get('/docs/theme.css', include_in_schema=False)
def docs_theme():
    return Response(DOCS_THEME, media_type='text/css')


@router.get('/snapshots', summary='List available container snapshots')
def all_snapshots(request: Request):
    authenticated(request)
    return safe_helper('list-snapshots')

@router.post('/nodes/{shop}/snapshots/{identifier}/restore', summary='Restore original container state from a snapshot (admin only)')
def restore_node_snapshot(request: Request, shop: str, identifier: str):
    administrator(request)
    validate_shop(shop)
    if not re.fullmatch(r'[0-9]{10,}-[0-9a-f]{12}',identifier):raise HTTPException(400,'Invalid snapshot selection')
    return safe_helper('restore-snapshot',shop,identifier)


@router.delete('/nodes/{shop}/snapshots/{identifier}', summary='Permanently delete one saved snapshot (admin only)')
def delete_node_snapshot(request: Request, shop: str, identifier: str):
    administrator(request)
    validate_shop(shop)
    if not re.fullmatch(r'[0-9]{10,}-[0-9a-f]{12}',identifier):raise HTTPException(400,'Invalid snapshot selection')
    return safe_helper('delete-snapshot',shop,identifier)


class ScheduledCommand(BaseModel):
    target: str = 'host'
    command: str
    run_at: Optional[str] = None
    timeout_seconds: StrictInt = 300
    model_config = ConfigDict(extra='forbid')


def command_operation(*args):
    result = safe_helper(*args)
    if 'queue_error' in result:
        raise HTTPException(result.get('status_code',400),result['queue_error'])
    from fastapi.responses import JSONResponse
    return JSONResponse(result,headers={'Cache-Control':'no-store'})


@router.put('/commands/{command_id}', summary='Queue a scheduled host/Linux shell or Windows PowerShell command (admin only)')
def queue_command(command_id: str, data: ScheduledCommand, request: Request):
    administrator(request)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',command_id):
        raise HTTPException(400,'Invalid command ID')
    if len(data.command.encode()) > 16000:
        raise HTTPException(400,'Command exceeds 16000 UTF-8 bytes')
    return command_operation('command-put',command_id,json.dumps(data.model_dump()))


@router.get('/commands', summary='List recent scheduled commands (admin only; excludes command text/output)')
def list_commands(request: Request):
    administrator(request)
    return command_operation('command-list')


@router.get('/commands/{command_id}', summary='Read a scheduled command and private output (admin only)')
def scheduled_command(command_id: str, request: Request):
    administrator(request)
    return command_operation('command-get',command_id)


@router.post('/commands/{command_id}/cancel', summary='Cancel a pending scheduled command (admin only)')
def cancel_command(command_id: str, request: Request):
    administrator(request)
    return command_operation('command-cancel',command_id)


from container_admin import HostPurge


@router.get('/system/purge', summary='Preview irreversible container-state purge (admin only)')
def purge_plan(request: Request):
    administrator(request)
    return safe_helper('purge-plan')


@router.post('/system/purge', summary='Queue confirmed deletion of all rootful container state (admin only)')
def purge_start(data: HostPurge, request: Request):
    administrator(request)
    return safe_helper('purge-start', data.confirmation)


@router.get('/system/purge/status', summary='Read the last container-state purge result (admin only)')
def purge_status(request: Request):
    administrator(request)
    return safe_helper('purge-status')


from desktop_proxy import routes as desktop_proxy_routes
router.include_router(desktop_proxy_routes(api=True))


class UbuntuVPNRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    peer: str
    remote: str
    secret: SecretStr
    local: Optional[str] = None


@router.put('/nodes/{shop}/vpn',summary='Configure an Ubuntu VM IPsec connection through its private guest agent')
def configure_ubuntu_vpn(shop: str, data: UbuntuVPNRequest, request: Request):
    administrator(request)
    validate_shop(shop)
    payload=data.model_dump(exclude={'secret'})
    payload['secret']=data.secret.get_secret_value()
    return safe_helper('ubuntu-vpn-add',shop,json.dumps(payload))

from image_build_api import api_router as image_build_api_router
router.include_router(image_build_api_router)
