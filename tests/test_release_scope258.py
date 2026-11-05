"""Public creation limits never mutate existing network state."""
from pathlib import Path
import sys,runpy
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/helpers'))
import mykos_release_scope as scope

@pytest.mark.parametrize('kind',['shared','host-shared','host-site','native-full','vm-vpn'])
def test_experimental_modes_are_private_only(kind):
 if scope.EXPERIMENTAL_NETWORK_CREATION:scope.require_network_creation(kind)
 else:
  with pytest.raises(ValueError,match='outside the initial public release'):scope.require_network_creation(kind)


def test_verified_modes_remain_available():
 scope.require_network_creation('native-site');scope.require_network_creation('peer')
 with pytest.raises(ValueError,match='Unknown'):scope.require_network_creation('invalid')


def test_public_gateway_creation_rejected_before_lock_or_network_changes(monkeypatch):
 if scope.EXPERIMENTAL_NETWORK_CREATION:pytest.skip('Private package retains experimental creation')
 import mykos_vpn_gateway as gateway
 monkeypatch.setattr(gateway.vm,'locked',lambda:pytest.fail('Rejected creation reached mutable state'))
 for function,args in [(gateway.configure,('12345678','office','10.77.0.0/24')),(gateway.configure_host,('office','10.77.0.0/24')),(gateway.preflight_mode,('12345678','office','shared'))]:
  with pytest.raises(ValueError,match='outside the initial public release'):function(*args)


def test_public_host_and_VM_creation_rejected_before_mutation(tmp_path,monkeypatch):
 if scope.EXPERIMENTAL_NETWORK_CREATION:pytest.skip('Private package retains experimental creation')
 writer=runpy.run_path(str(ROOT/'pos/host-vpn'))
 with pytest.raises(ValueError,match='outside the initial public release'):
  writer['save_connection'](tmp_path,'office','127.0.0.1','192.0.2.1/32','10.77.0.0/24','synthetic-only')
 assert not list(tmp_path.iterdir())
 import mykos_ubuntu_vpn as ubuntu
 monkeypatch.setattr(ubuntu,'selection',lambda _:pytest.fail('Rejected creation contacted a guest'))
 with pytest.raises(ValueError,match='outside the initial public release'):ubuntu.add('12345678','office','127.0.0.1','10.77.0.0/24','synthetic-only')


def test_native_site_writer_retains_direct_site_only_path(tmp_path):
 writer=runpy.run_path(str(ROOT/'pos/host-vpn'));directory=tmp_path/'ipsec.d';directory.mkdir()
 name=writer['save_connection'](tmp_path,'office','127.0.0.1','192.0.2.1/32','10.77.0.0/24','synthetic-only',native_directory=directory,site_only=True)
 assert name=='office'
 assert 'rightsubnet=10.77.0.0/24' in (directory/'office.conn.conf').read_text()


def test_public_new_macvlan_network_is_rejected_without_network_creation(monkeypatch):
 if scope.EXPERIMENTAL_NETWORK_CREATION:pytest.skip('Private package retains experimental creation')
 import mykos_network as network
 calls=[]
 monkeypatch.setattr(network,'network_device',lambda _: 'pilot0')
 def run(args,**kwargs):
  calls.append(args)
  if args[1]=='info':return 'netavark'
  if args[1:3]==['network','ls']:return ''
  return ''
 monkeypatch.setattr(network,'run',run)
 with pytest.raises(ValueError,match='outside the initial public release'):network.ensure({'native_network_driver':'macvlan'})
 assert not any(call[1:3]==['network','create'] or call[:2]==['systemctl','enable'] for call in calls)
