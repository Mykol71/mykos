"""Root helper validation with no Podman, systemd, socket, or account mutations."""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import pytest

HELPERS=Path(__file__).resolve().parents[1]/'services/helpers'
sys.path.insert(0,str(HELPERS))
import mykos_runtime


@pytest.mark.parametrize('build_major,expected', [('8','RHEL 10 build host'), ('10','Image creation failed')])
def test_image_build_stops_before_unsafe_followup(tmp_path,build_major,expected):
    import subprocess
    source=(HELPERS.parents[1]/'pos/pos').read_text()
    preflight=source[source.index('check_image_builder() {'):source.index('\ncreate_ks() {')]
    build=source[source.index('create-image() {'):source.index('\nset_working_shopcode() {')]
    script=preflight+'\n'+build+'''
rpm() { echo "$TEST_BUILD_MAJOR"; }
ksvalidator() { return 0; }
podman() {
  if [ "$1 $2" = 'image exists' ]; then return 1; fi
  echo unexpected-podman-operation
  return 1
}
rm() { :; }
create_ks() { :; }
livemedia-creator() { return 1; }
mv() { echo unexpected-file-move; return 1; }
mkdir() { echo unexpected-directory-write; return 1; }
create_dockerfile() { echo unexpected-dockerfile-write; return 1; }
create-image
'''
    import os
    result=subprocess.run(['bash','-c',script],cwd=tmp_path,input='rh10\n',text=True,capture_output=True,
                          env={**os.environ,'TEST_BUILD_MAJOR':build_major})
    assert result.returncode!=0
    assert expected in result.stderr
    assert 'unexpected-' not in result.stdout+result.stderr
    assert not list(tmp_path.iterdir())

def load(name):
    loader=importlib.machinery.SourceFileLoader(name.replace('-','_'),str(HELPERS/name))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    return module


def test_host_actions_are_allowlisted_and_reboot_requires_hostname(monkeypatch):
    module = load('mykos-container-helper')
    calls = []
    monkeypatch.setattr(module, 'run', lambda args, **kw: calls.append(args) or 'host output')
    monkeypatch.setattr(module.socket, 'gethostname', lambda: 'host.example')
    for action in ('host-logs', 'host-top', 'host-ipsec'):
        assert module.dispatch([action])['host'] == 'host.example'
        assert 'podman' not in ' '.join(calls[-1])
        with pytest.raises(ValueError): module.dispatch([action, '01234567'])
    with pytest.raises(ValueError): module.dispatch(['host-reboot', 'wrong-host'])
    assert not any('systemd-run' in command[0] for command in calls)
    assert module.dispatch(['host-reboot', 'host.example'])['state'] == 'reboot-scheduled'
    assert calls[-1] == ['/usr/bin/systemd-run', '--unit=mykos-host-reboot', '--on-active=5s', '/usr/bin/systemctl', 'reboot']


def test_host_terminal_executes_host_su_without_podman(monkeypatch):
    module = load('mykos-terminal-helper')
    monkeypatch.setattr(module, 'connection', lambda: object())
    monkeypatch.setattr(module, 'config', lambda: {'terminal_enabled':True})
    monkeypatch.setattr(module, 'request', lambda conn: {'shop':'host'})
    monkeypatch.setattr(module, 'target', lambda shop: pytest.fail('Host must not target a container'))
    monkeypatch.setattr(module.pty, 'fork', lambda: (0,1))
    calls=[]
    def fake_exec(binary, args):
        calls.append((binary,args))
        raise RuntimeError('mock exec')
    monkeypatch.setattr(module.os,'execv',fake_exec)
    with pytest.raises(RuntimeError,match='mock exec'): module.main()
    assert calls == [('/bin/su',['/bin/su','-','root'])]
    monkeypatch.setattr(module, 'request', lambda conn: {'shop':'host','action':'add-vpn'})
    monkeypatch.setattr(module, 'containers', lambda: pytest.fail('Host VPN must not inspect containers'))
    with pytest.raises(RuntimeError,match='mock exec'): module.main()
    assert calls[-1] == ('/usr/bin/python3',['/usr/bin/python3','/usr/local/share/mykos/pos/host-vpn'])

def test_desktop_installer_firewall_rules_are_source_restricted():
    module=load('mykos-desktop-setup')
    for bad in ['0.0.0.0/0','8.8.8.0/24','::/0','192.168.222.5/24']:
        with pytest.raises(ValueError): module.rules(bad)
    rules=module.rules('192.168.222.0/24')
    assert len(rules)==3
    assert all('source address="192.168.222.0/24"' in rule for rule in rules)
    assert '18006-19005' in rules[0]
    assert 'protocol="udp"' in rules[2]

def test_host_vpn_writes_only_new_host_connection(tmp_path):
    import runpy
    workflow = runpy.run_path(str(HELPERS.parents[1]/'pos/host-vpn'))
    directory = tmp_path/'ipsec.d'; directory.mkdir()
    (tmp_path/'ipsec.conf').write_text('include '+str(directory/'*.conf')+'\n')
    (tmp_path/'ipsec.secrets').write_text('include '+str(directory/'*.secrets')+'\n')
    save = workflow['save_connection']
    assert save(tmp_path,'branch','vpn.example','192.168.222.0/24','10.1.0.0/24','test"\\secret') == 'mykos-branch'
    config = directory/'mykos-branch.conn.conf'
    assert 'leftsubnet=192.168.222.0/24' in config.read_text()
    assert 'rightsubnet=10.1.0.0/24' in config.read_text()
    secrets = directory/'mykos-branch.secrets'
    assert secrets.stat().st_mode & 0o777 == 0o600
    assert 'test\\"\\\\secret' in secrets.read_text()
    with pytest.raises(ValueError,match='already exists'):
        save(tmp_path,'branch','vpn.example','192.168.222.0/24','10.1.0.0/24','replacement')
    assert 'replacement' not in secrets.read_text()
    for name in ['../escape','bad\nname','--bad']:
        with pytest.raises(ValueError): save(tmp_path,name,'vpn.example','192.168.222.0/24','10.1.0.0/24','test')
    (tmp_path/'ipsec.secrets').write_text('')
    assert save(tmp_path,'second','vpn.example','192.168.222.0/24','10.1.0.0/24','test')=='mykos-second'
    assert (tmp_path/'ipsec.secrets').read_text()=='include '+str(directory/'mykos-*.secrets')+'\n'
    backups=list(tmp_path.glob('ipsec.secrets.mykos-before-*'))
    assert len(backups)==1 and backups[0].read_bytes()==b''
    assert backups[0].stat().st_mode & 0o777==0o600


def test_container_targeting_rejects_ambiguity_and_non_ascii(monkeypatch):
    monkeypatch.setattr(mykos_runtime,'containers',lambda:[{'shop':'01234567','name':'01234567.a'},{'shop':'01234567','name':'01234567.b'}])
    with pytest.raises(ValueError):mykos_runtime.target('01234567')
    for bad in ('1234','１２３４５６７８','01234567;id','../../etc','--all'):
        with pytest.raises(ValueError):mykos_runtime.shopcode(bad)

def test_helper_allowlist_and_stage_protocol(tmp_path,monkeypatch):
    module=load('mykos-container-helper')
    monkeypatch.setattr(module,'JOBS',tmp_path.resolve())
    monkeypatch.setattr(mykos_runtime,'JOBS',tmp_path.resolve())
    monkeypatch.setattr(module,'active',lambda shop:False)
    monkeypatch.setattr(module,'containers',lambda:[])
    monkeypatch.setattr(module,'config',lambda:{'stage_image':'localhost/rti:test','network_device':'eth0'})
    calls=[]
    monkeypatch.setattr(module,'run',lambda args,**kw:calls.append(args) or '')
    for args in (['exec','01234567','id'],['logs','01234567','extra'],['stage-start','01234567','$(id)'],['stop-pos','--all']):
        with pytest.raises(ValueError):module.dispatch(args)
    assert not calls
    result=module.dispatch(['stage-start','01234567','no'])
    assert result['state']=='queued' and result['restore'] is False
    assert calls==[['/usr/bin/systemctl','start','--no-block','mykos-stage@01234567.service']]
    assert (tmp_path/'01234567.json').exists()

def test_stage_launcher_rejects_bad_config_before_host_commands(monkeypatch):
    module=load('mykos-stage-pos')
    from contextlib import nullcontext
    monkeypatch.setattr(module.vm,'locked',nullcontext)
    monkeypatch.setattr(module,'config',lambda:{'stage_image':'--privileged','network_device':'eth0'})
    monkeypatch.setattr(module,'run',lambda *args,**kw:pytest.fail('Must reject before executing host commands'))
    with pytest.raises(ValueError):module.stage('01234567','no')


def test_terminal_starts_root_login_in_validated_container(monkeypatch):
    module = load('mykos-terminal-helper')
    monkeypatch.setattr(module, 'connection', lambda: object())
    monkeypatch.setattr(module, 'config', lambda: {'terminal_enabled': True})
    monkeypatch.setattr(module, 'request', lambda conn: {'shop': '01234567'})
    targets = []
    def target(shop):
        targets.append(shop)
        return '01234567.example'
    monkeypatch.setattr(module, 'target', target)
    monkeypatch.setattr(module.pty, 'fork', lambda: (0, 123))
    class Executed(Exception):
        pass
    calls = []
    def execute(binary, argv):
        calls.append((binary, argv))
        raise Executed
    monkeypatch.setattr(module.os, 'execv', execute)
    with pytest.raises(Executed):
        module.main()
    assert targets == ['01234567']
    assert calls == [(module.PODMAN, [module.PODMAN, 'exec', '-it',
                                    '01234567.example', '/bin/su', '-', 'root'])]
    monkeypatch.setattr(module, 'config', lambda: {'terminal_enabled': False})
    with pytest.raises(ValueError, match='Terminal disabled'):
        module.main()
    assert len(calls) == 1


def test_stage_images_only_running_rh_templates(monkeypatch):
    import json
    calls = []
    def run(args):
        calls.append(args)
        return json.dumps([
            {'Names': ['rh9'], 'ImageID': 'sha256:abc123'},
            {'Names': 'rh8', 'Image': 'localhost/rh8:latest'},
            {'Names': ['01234567.prod'], 'ImageID': 'sha256:def456'},
            {'Names': ['rh-bad'], 'Image': '--privileged'},
        ])
    monkeypatch.setattr(mykos_runtime, 'run', run)
    assert mykos_runtime.stage_images() == [
        {'os': 'rh8', 'image': 'localhost/rh8:latest'},
        {'os': 'rh9', 'image': 'sha256:abc123'}]
    assert calls == [[mykos_runtime.PODMAN, 'ps', '-a', '--format', 'json']]


def test_selected_os_is_saved_and_passed_to_worker(tmp_path, monkeypatch):
    module = load('mykos-container-helper')
    monkeypatch.setattr(module, 'JOBS', tmp_path)
    monkeypatch.setattr(mykos_runtime, 'JOBS', tmp_path)
    monkeypatch.setattr(module, 'active', lambda shop: False)
    monkeypatch.setattr(module, 'containers', lambda: [])
    monkeypatch.setattr(module, 'config', lambda: {'network_device': 'eth0'})
    monkeypatch.setattr(module, 'stage_images', lambda: [{'os': 'rh9', 'image': 'sha256:abc123'}])
    calls = []
    monkeypatch.setattr(module, 'run', lambda args, **kwargs: calls.append(args) or 'ready')
    for os_name in ('rh10', '--privileged', '../rh9', ''):
        with pytest.raises(ValueError, match='Select an OS'):
            module.dispatch(['stage-start', '01234567', 'no', os_name])
    assert calls == []
    result = module.dispatch(['stage-start', '01234567', 'no', 'rh9'])
    assert result['os'] == 'rh9' and result['image'] == 'sha256:abc123'
    module.worker('01234567')
    assert ['/usr/local/sbin/mykos-stage-pos', '01234567', 'no', 'sha256:abc123'] in calls
    assert calls[-3:] == [['/usr/bin/nice','-n','-5',sys.executable,'/opt/mykos-web/'+name,'01234567']
                          for name in ('collect-nodes.py','collect-details.py','collect-extended.py')]


@pytest.mark.parametrize('device', [None, 'eth0'])
def test_stage_uses_selected_image_instead_of_default(tmp_path, monkeypatch, device):
    module = load('mykos-stage-pos')
    from contextlib import nullcontext
    monkeypatch.setattr(module.vm, 'locked', nullcontext)
    monkeypatch.setattr(module.network, 'arguments', lambda cfg, name: ['--network', 'mykos-lan', '--label', 'io.mykos.network.interface=eth0'])
    monkeypatch.setattr(module, 'config', lambda: {'stage_image': 'localhost/old', 'network_device': device})
    monkeypatch.setattr(module, 'containers', lambda: [])
    monkeypatch.setattr(module.socket, 'gethostname', lambda: 'testhost')
    monkeypatch.setattr(module, 'Path', lambda path: tmp_path / path.lstrip('/'))
    calls = []
    monkeypatch.setattr(module, 'run', lambda args, **kwargs: calls.append(args) or 'new-container-id')
    monkeypatch.setattr(mykos_runtime, 'run', lambda *args, **kwargs: '8.8.8.8 dev enp2s0 src 192.168.222.5')
    module.stage('01234567', 'no', 'sha256:abc123')
    create = next(args for args in calls if args[:2] == [module.PODMAN, 'run'])
    assert create[-2:] == ['sha256:abc123', '/sbin/init']
    assert 'localhost/old' not in create
    assert create[create.index('--network')+1]=='mykos-lan'
    assert not any(args[0].endswith('/pipework') for args in calls)


def test_add_vpn_terminal_runs_only_fixed_host_workflow(monkeypatch):
    module = load('mykos-terminal-helper')
    monkeypatch.setattr(module, 'connection', lambda: object())
    monkeypatch.setattr(module, 'config', lambda: {'terminal_enabled': True})
    request = {'shop': '01234567', 'action': 'add-vpn'}
    monkeypatch.setattr(module, 'request', lambda conn: request)
    monkeypatch.setattr(module, 'target', lambda shop: '01234567.example')
    monkeypatch.setattr(module, 'containers', lambda: [{'name': '01234567.example', 'state': 'running'}])
    monkeypatch.setattr(module.pty, 'fork', lambda: (0, 123))
    directories, calls = [], []
    monkeypatch.setattr(module.os, 'chdir', directories.append)
    class Executed(Exception): pass
    def execute(binary, argv):
        calls.append((binary, argv))
        raise Executed
    monkeypatch.setattr(module.os, 'execv', execute)
    with pytest.raises(Executed): module.main()
    assert directories == ['/usr/local/share/mykos/pos']
    assert calls == [('/usr/bin/bash', ['/usr/bin/bash', './pos', '--add-vpn', '01234567', '01234567.example'])]
    request['action'] = 'add-vpn;id'
    with pytest.raises(ValueError, match='Unsupported terminal action'): module.main()
    request['action'] = 'add-vpn'
    monkeypatch.setattr(module, 'containers', lambda: [{'name': '01234567.example', 'state': 'exited'}])
    with pytest.raises(ValueError, match='Start the container'): module.main()
    assert len(calls) == 1


@pytest.mark.parametrize('name,public,network', [('../escape', 'vpn.example.com', '10.0.0.0'),
    ('valid', 'host\nconn evil', '10.0.0.0'), ('valid', 'vpn.example.com', '999.0.0.0')])
def test_add_vpn_rejects_invalid_inputs_before_commands(tmp_path, name, public, network):
    import subprocess
    source = (HELPERS.parents[1]/'pos/pos').read_text()
    function = source[source.index('add_vpn() {'):source.index('\nrm_vpn() {')]
    script = function + '\nVPN_PSK=test; SHOPCODE=01234567; CONTAINER=01234567.example\ncurl() { echo unexpected-command; }; podman() { echo unexpected-command; }; add_vpn\n'
    result = subprocess.run(['bash', '-c', script], cwd=tmp_path,
                            input=name+'\n'+public+'\n'+network+'\n', text=True, capture_output=True)
    assert result.returncode != 0
    assert 'unexpected-command' not in result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('secret', ['test-only-secret', 'quotes"and\\slashes\'and$(touch injected)'])
def test_add_vpn_function_creates_configuration_in_isolated_directory(tmp_path, secret):
    import subprocess
    source = (HELPERS.parents[1]/'pos/pos').read_text()
    function = source[source.index('add_vpn() {'):source.index('\nrm_vpn() {')]
    script = function + '''
VPN_PSK=test-only-secret; SHOPCODE=01234567; CONTAINER=01234567.example
curl() { echo 203.0.113.20; }
podman() { :; }
update_meraki() { echo unexpected-meraki; return 1; }
add_vpn
'''
    result = subprocess.run(['bash', '-c', script], cwd=tmp_path,
        input='test-peer\nvpn.example.com\n10.1.2.0\n'+secret+'\ny\nn\n', text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    conf = (tmp_path/'.01234567/test-peer.conn.conf').read_text()
    assert 'conn test-peer' in conf and 'right=vpn.example.com' in conf
    assert 'rightsubnet="10.1.2.0/24"' in conf
    assert (tmp_path/'.01234567/ipsec.secrets').stat().st_mode & 0o777 == 0o600
    assert 'unexpected-meraki' not in result.stdout
    escaped = secret.replace('\\', '\\\\').replace('"', '\\"')
    assert (tmp_path/'.01234567/ipsec.secrets').read_text() == '192.168.222.0 192.168.222.0 : PSK "'+escaped+'"\n'
    assert secret not in result.stdout
    assert not (tmp_path/'injected').exists()


def test_add_vpn_requires_entered_psk_before_commands(tmp_path):
    import subprocess
    source = (HELPERS.parents[1]/'pos/pos').read_text()
    function = source[source.index('add_vpn() {'):source.index('\nrm_vpn() {')]
    script = function + '\nVPN_PSK=old-environment-value; SHOPCODE=01234567; CONTAINER=01234567.example\ncurl() { echo unexpected-command; }; podman() { echo unexpected-command; }; add_vpn\n'
    result = subprocess.run(['bash', '-c', script], cwd=tmp_path,
        input='test-peer\nvpn.example.com\n10.1.2.0\n\n', text=True, capture_output=True)
    assert result.returncode != 0
    assert 'shared secret cannot be empty' in result.stdout
    assert 'unexpected-command' not in result.stdout
    assert not list(tmp_path.iterdir())


def test_meraki_psk_is_serialized_as_data(tmp_path):
    import json, os, subprocess
    source = (HELPERS.parents[1]/'pos/pos').read_text()
    function = source[source.index('update_meraki() {'):source.index('\nlist_vpns() {')]
    secret = '''quotes"slashes\\and'$(touch injected)'''
    script = function + '\ncurl() { cat > request.json; }; update_meraki\n'
    result = subprocess.run(['bash', '-c', script], cwd=tmp_path, text=True, capture_output=True,
        env={**os.environ, 'MYKOS_PYTHON': sys.executable, 'VPN_PSK': secret, 'MERAKI_API_KEY': 'test', 'MERAKI_ORG_ID': '123',
             'REMOTE_NAME': 'peer', 'PUBLIC': '203.0.113.20'})
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path/'request.json').read_text())['peers'][0]['secret'] == secret
    assert secret not in result.stdout
    assert not (tmp_path/'injected').exists()
    assert not (tmp_path/'update_meraki_peers').exists()


def test_network_device_detected_from_route_and_override(monkeypatch):
    calls = []
    def route(args, **kwargs):
        calls.append(args)
        return '8.8.8.8 via 192.168.222.1 dev enp2s0 src 192.168.222.5 uid 1000\n cache'
    monkeypatch.setattr(mykos_runtime, 'run', route)
    assert mykos_runtime.network_device({}) == 'enp2s0'
    assert mykos_runtime.network_device({'network_device': None}) == 'enp2s0'
    assert mykos_runtime.network_device({'network_device': 'br0'}) == 'br0'
    assert calls == [['ip', 'route', 'get', '8.8.8.8']] * 2
    with pytest.raises(ValueError, match='Invalid network_device'):
        mykos_runtime.network_device({'network_device': '--bad'})
    monkeypatch.setattr(mykos_runtime, 'run', lambda *args, **kwargs: 'unreachable 8.8.8.8')
    with pytest.raises(ValueError, match='Cannot detect network device'):
        mykos_runtime.network_device({})


def test_stage_check_accepts_automatic_network_device(monkeypatch):
    module = load('mykos-container-helper')
    monkeypatch.setattr(module, 'config', lambda: {})
    monkeypatch.setattr(module, 'containers', lambda: [])
    monkeypatch.setattr(module, 'active', lambda shop: False)
    monkeypatch.setattr(mykos_runtime, 'run', lambda *args, **kwargs: '8.8.8.8 dev enp2s0 src 192.168.222.5')
    result = module.stage_check('01234567')
    assert result['configured'] is True
    assert result['network_device'] == 'enp2s0'


def test_all_requested_os_templates_are_discovered(monkeypatch):
    import json
    names = ['rh7', 'rh8', 'rh9', 'rh10', 'win10', 'wns2025', 'wns2022', 'wns2019', 'wns2016', 'win11', 'win8e', 'win7u', 'winxp', 'winme', 'win2k', 'win98', 'mac15', 'mac14', 'mac13', 'mac12']
    rows = [{'Names': [name], 'ImageID': 'sha256:abc123'} for name in reversed(names)]
    rows += [{'Names': ['win-unknown'], 'ImageID': 'sha256:abc123'},
             {'Names': ['01234567.prod'], 'ImageID': 'sha256:abc123'}]
    monkeypatch.setattr(mykos_runtime, 'run', lambda *args, **kwargs: json.dumps(rows))
    assert [item['os'] for item in mykos_runtime.stage_images()] == names
    assert all(item['image'] == 'sha256:abc123' for item in mykos_runtime.stage_images())


def test_ubuntu_templates_are_discovered(monkeypatch):
    import json
    names = ['ubuntu24.04', 'ubuntu22', 'ubuntu', 'ubuntu-unsafe', 'ubuntu24;bad', 'ubuntu24.04-extra']
    rows = [{'Names': [name], 'ImageID': 'sha256:abc123'} for name in names]
    monkeypatch.setattr(mykos_runtime, 'run', lambda *args, **kwargs: json.dumps(rows))
    assert [item['os'] for item in mykos_runtime.stage_images()] == ['ubuntu', 'ubuntu22', 'ubuntu24.04']


def test_stopped_prepared_desktop_is_available_but_stopped_linux_is_not(monkeypatch):
    import json
    import mykos_vm
    monkeypatch.setattr(mykos_runtime, 'run', lambda args: json.dumps([
        {'Names':['mac15'], 'State':'exited', 'ImageID':'sha256:old', 'Labels':{'io.mykos.vm.os':'mac15'}},
        {'Names':['win11'], 'State':'exited', 'ImageID':'sha256:old', 'Labels':{'io.mykos.vm.os':'win11'}},
        {'Names':['rh9'], 'State':'exited', 'ImageID':'sha256:rh'}]))
    monkeypatch.setattr(mykos_vm, 'read', lambda name: {
        'ready':name=='mac15', 'role':'template', 'image':'sha256:new', 'image_name':'localhost/macos15:latest'})
    assert mykos_runtime.stage_images() == [{'os':'mac15','image':'localhost/macos15:latest'}]


def test_staging_includes_saved_desktop_without_container(monkeypatch):
    import mykos_vm
    monkeypatch.setattr(mykos_runtime,'run',lambda args:'[]')
    monkeypatch.setattr(mykos_vm,'prepared_templates',lambda:[{'os':'mac15','image':'localhost/macos15:latest'}])
    assert mykos_runtime.stage_images()==[{'os':'mac15','image':'localhost/macos15:latest'}]


def test_persistent_podman_launches_escape_worker_cleanup(monkeypatch):
    monkeypatch.setattr(mykos_runtime.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(mykos_runtime.Path, 'is_dir', lambda p: str(p) == '/run/systemd/system')
    commands = [[mykos_runtime.PODMAN, 'run', '-d', '--name', 'guest', 'local/image'],
                [mykos_runtime.PODMAN, 'start', 'guest'],
                [mykos_runtime.PODMAN, 'container', 'restore', '--file-locks', 'guest']]
    units = set()
    for command in commands:
        wrapped = mykos_runtime.podman_scope(command)
        assert wrapped[:5] == ['/usr/bin/systemd-run','--scope','--quiet','--collect','--property=Delegate=yes']
        units.add(wrapped[5])
        assert wrapped[6:] == ['--', *command]
    assert len(units) == 3
    for command in [[mykos_runtime.PODMAN, 'stop', 'guest'], [mykos_runtime.PODMAN,'inspect','guest'],
                    [mykos_runtime.PODMAN,'run','local/image','true'], ['echo','example']]:
        assert mykos_runtime.podman_scope(command) == command


def test_delete_container_queue_pins_identity_and_worker_dispatch(tmp_path,monkeypatch):
    import json
    import mykos_containers
    module=load('mykos-container-helper')
    monkeypatch.setattr(module,'JOBS',tmp_path)
    monkeypatch.setattr(mykos_runtime,'JOBS',tmp_path)
    monkeypatch.setattr(module,'active',lambda shop:False)
    monkeypatch.setattr(mykos_containers,'selection',lambda shop,confirmation:{'container_id':'a'*64})
    calls=[]
    monkeypatch.setattr(module,'run',lambda args,**kw:calls.append(args) or '')
    for bad in (['delete-pos'],['delete-pos','01234567'],['delete-pos','01234567','01234567','extra']):
        with pytest.raises(ValueError):module.dispatch(bad)
    queued=module.dispatch(['delete-pos','01234567','01234567'])
    assert queued['operation']=='delete' and queued['container_id']=='a'*64
    deleted=[]
    monkeypatch.setattr(mykos_containers,'delete',lambda *args:deleted.append(args) or {'state':'deleted'})
    module.worker('01234567')
    assert deleted==[('01234567','01234567','a'*64)]
    assert json.loads((tmp_path/'01234567.json').read_text())['state']=='done'
    assert calls[-1]==['/usr/bin/systemctl','start','mykos-collector.service']


@pytest.mark.parametrize('push',['y','n'])
def test_host_add_vpn_reuses_container_workflow_without_container_commands(tmp_path,push):
    import subprocess
    source=(HELPERS.parents[1]/'pos/pos').read_text()
    function=source[source.index('add_vpn() {'):source.index('\nrm_vpn() {')]
    function=function.replace('/usr/sbin/ipsec','ipsec')
    etc=tmp_path/'etc';directory=etc/'ipsec.d';directory.mkdir(parents=True)
    (etc/'ipsec.conf').write_text('include '+str(directory/'*.conf')+'\n')
    (etc/'ipsec.secrets').write_text('include '+str(directory/'*.secrets')+'\n')
    workflow=(HELPERS.parents[1]/'pos/host-vpn').read_text().replace("Path('/etc')",'Path('+repr(str(etc))+')')
    (tmp_path/'host-vpn').write_text(workflow)
    script=function+'''
MYKOS_VPN_HOST=1
curl() { echo 203.0.113.20; }
podman() { echo unexpected-container-command; return 1; }
ipsec() { printf '%s\\n' "$*" >> actions; }
update_meraki() { echo pushed-meraki; }
add_vpn
'''
    secret='test"and\\secret'
    result=subprocess.run(['bash','-c',script],cwd=tmp_path,
        input='branch\nvpn.example.com\n10.1.0.0\n'+secret+'\ny\n'+push+'\n',text=True,capture_output=True,
        env={**__import__('os').environ, 'MYKOS_PYTHON':sys.executable})
    assert result.returncode==0,result.stderr
    assert 'Enter REMOTE_NAME: ' in result.stdout and 'Enter REMOTE_NET: ' in result.stdout
    assert 'Push peer config to meraki?' in result.stdout and 'Remote-side VPN Info:' in result.stdout
    assert ('pushed-meraki' in result.stdout)==(push=='y')
    assert 'unexpected-container-command' not in result.stdout
    assert secret not in result.stdout+result.stderr
    assert (tmp_path/'actions').read_text().splitlines()==['auto --rereadsecrets','auto --add mykos-branch','auto --up mykos-branch']
    conf=(directory/'mykos-branch.conn.conf').read_text()
    assert 'leftid=192.168.222.0' in conf and 'rightid=192.168.222.0' in conf
    assert 'ike=aes128-sha1-dh21' in conf and 'esp=aes128-sha1' in conf
    assert 'leftsubnet=192.168.222.0/24' in conf and 'rightsubnet=10.1.0.0/24' in conf
    assert (directory/'mykos-branch.secrets').read_text()=='192.168.222.0 192.168.222.0 : PSK "test\\"and\\\\secret"\n'
    assert (directory/'mykos-branch.secrets').stat().st_mode & 0o777==0o600


def test_host_add_vpn_cancel_makes_no_configuration_or_command_calls(tmp_path):
    import subprocess
    source=(HELPERS.parents[1]/'pos/pos').read_text()
    function=source[source.index('add_vpn() {'):source.index('\nrm_vpn() {')]
    script=function+'''
MYKOS_VPN_HOST=1
curl() { echo 203.0.113.20; }
podman() { echo unexpected-container-command; }
python3() { echo unexpected-save; }
add_vpn
'''
    result=subprocess.run(['bash','-c',script],cwd=tmp_path,input='branch\nvpn.example.com\n10.1.0.0\ntest-secret\nn\n',text=True,capture_output=True)
    assert result.returncode==0 and 'unexpected-' not in result.stdout
    assert not list(tmp_path.iterdir())



def test_stage_prefers_readable_image_name_without_guest_calls(monkeypatch):
    import json
    calls=[]
    def run(args):
        calls.append(args)
        return json.dumps([{'Names':['rh8'],'State':'running','Image':'localhost/rh8:latest','ImageID':'a'*64}])
    monkeypatch.setattr(mykos_runtime,'run',run)
    assert mykos_runtime.stage_images()==[{'os':'rh8','image':'localhost/rh8:latest'}]
    assert calls==[[mykos_runtime.PODMAN,'ps','-a','--format','json']]
