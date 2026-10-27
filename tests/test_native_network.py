import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_network as network

@pytest.mark.parametrize('backend,unit',[('cni','cni-dhcp.socket'),('netavark','netavark-dhcp-proxy.socket')])
def test_network_setup_and_interface(monkeypatch,backend,unit):
    calls=[]
    monkeypatch.setattr(network,'network_device',lambda cfg:'enp2s0')
    def run(args,**kw):
        calls.append(args)
        if args[0]=='ip':return '[]'
        if args[1]=='info':return backend
        if args[1:3]==['network','ls']:return ''
        if args[1:3]==['network','inspect']:return json.dumps([{'driver':'macvlan','network_interface':'enp2s0','ipam_options':{'driver':'dhcp'}}])
        return ''
    monkeypatch.setattr(network,'run',run)
    args=network.arguments({'native_network_driver':'macvlan'},'12345678.host')
    assert args[1]==('mykos-lan:interface_name=eth0' if backend=='netavark' else 'mykos-lan')
    assert ['systemctl','enable','--now',unit] in calls
    assert args[3].startswith('02:')
    assert args==network.arguments({'native_network_driver':'macvlan'},'12345678.host')

def test_existing_mismatched_network_not_modified(monkeypatch):
    calls=[]
    monkeypatch.setattr(network,'network_device',lambda cfg:'enp2s0')
    def run(args,**kw):
        calls.append(args)
        if args[1]=='info':return 'netavark'
        if args[1:3]==['network','ls']:return 'mykos-lan'
        if args[1:3]==['network','inspect']:return '[{"driver":"bridge"}]'
        return ''
    monkeypatch.setattr(network,'run',run)
    with pytest.raises(ValueError,match='left unchanged'):network.ensure({'native_network_driver':'macvlan'})
    assert not any('create' in c or 'enable' in c for c in calls)

@pytest.mark.parametrize('value',['host','podman','x:interface_name=eth1','../escape',''])
def test_network_names(value):
    with pytest.raises(ValueError):network.name({'native_network':value})

@pytest.mark.parametrize('backend',['cni','netavark'])
def test_bridge_wifi_uses_no_parent_or_dhcp_socket(monkeypatch,backend):
    calls=[]
    monkeypatch.setattr(network,'network_device',lambda cfg:pytest.fail('Wi-Fi NAT must not require a parent'))
    def run(args,**kw):
        calls.append(args)
        if args[0]=='ip':return '[]'
        if args[1]=='info':return backend
        if args[1:3]==['network','ls']:return ''
        if args[1:3]==['network','inspect']:return '[{"driver":"bridge","ipam_options":{"driver":"host-local"},"internal":false,"subnets":[{"subnet":"192.168.22.0/24","gateway":"192.168.22.1"}]}]'
        return ''
    monkeypatch.setattr(network,'run',run)
    monkeypatch.setattr(network,'allocate_ip',lambda settings,name:'192.168.22.2')
    args=network.arguments({'native_network_driver':'bridge'},'12345678.dev')
    assert args[1].startswith('mykos-lan')
    assert not any(c[0]=='systemctl' for c in calls)
    create=next(c for c in calls if c[1:3]==['network','create'])
    assert 'bridge' in create and 'host-local' in create and '--opt' not in create


def test_invalid_driver_does_not_run_commands(monkeypatch):
    monkeypatch.setattr(network,'run',lambda *a,**k:pytest.fail('Must validate driver first'))
    with pytest.raises(ValueError,match='must be macvlan or bridge'):
        network.ensure({'native_network_driver':'host'})


def test_static_address_respects_stopped_container_claim(monkeypatch):
    settings={'native_network_driver':'bridge'}
    monkeypatch.setattr(network,'run',lambda args,**kw:'' if args[1:3]==['ps','-a'] else '[]')
    initial=network.allocate_ip(settings,'12345678.dev')
    claimed={'Name':'87654321.dev','Config':{'Labels':{'io.mykos.network.ip':initial}},
             'NetworkSettings':{'Networks':{'mykos-lan':{'IPAddress':''}}}}
    def run(args,**kw):
        return 'abc' if args[1:3]==['ps','-a'] else json.dumps([claimed])
    monkeypatch.setattr(network,'run',run)
    assert network.allocate_ip(settings,'12345678.dev')!=initial
    assert network.allocate_ip(settings,'87654321.dev')==initial


def test_static_pool_excludes_gateway_and_broadcast(monkeypatch):
    monkeypatch.setattr(network,'run',lambda *a,**k:'')
    pool=network.address_pool({})
    for i in range(300):
        addr=network.ipaddress.ip_address(network.allocate_ip({},str(i)))
        assert pool.network_address+2 <= addr < pool.broadcast_address


def test_subnet_route_overlap_rejected(monkeypatch):
    monkeypatch.setattr(network,'run',lambda *a,**k:'[{"dst":"192.168.22.0/24"}]')
    with pytest.raises(ValueError,match='overlaps'):network.check_routes(network.address_pool({}))


@pytest.mark.parametrize('driver',['bridge','macvlan'])
def test_native_interface_owned_by_podman_before_guest_boot(monkeypatch,driver):
    monkeypatch.setattr(network,'arguments',lambda settings,name:['--network','mykos-lan'])
    args=network.native_arguments({'native_network_driver':driver},'12345678.host')
    source=Path(network.__file__).with_name('mykos-podman-nm.conf')
    assert args[:2]==['--network','mykos-lan']
    mount=args[args.index('--mount')+1]
    assert 'source='+str(source) in mount
    assert 'destination=/etc/NetworkManager/conf.d/90-mykos-podman.conf' in mount
    assert mount.endswith(',ro=true')
    import configparser
    config=configparser.ConfigParser();config.read(source)
    assert config['device-mykos-podman']['match-device']=='interface-name:eth0'
    assert config['device-mykos-podman']['managed']=='0'
    # A VM's outer container must not inherit a guest NetworkManager policy.
    assert '--mount' not in network.arguments({},'ubuntu22.04')


def test_native_launch_paths_use_ownership_guard():
    helper=Path(network.__file__).parent
    assert 'network.native_arguments(' in (helper/'mykos-stage-pos').read_text()
    assert 'native_arguments as arguments' in (helper/'mykos-native-image-start').read_text()
    assert 'network.arguments(' in (helper/'mykos_vm.py').read_text()
