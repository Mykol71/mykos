"""Console target/Apache routing checks; no live guest or server is modified."""
import importlib.machinery,importlib.util
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_vm as vm


@pytest.mark.parametrize('selection,role,name', [('ubuntu24.04','template','ubuntu24.04'),('12345678','instance','12345678.prod.example')])
def test_console_target_pins_actual_managed_podman_port(monkeypatch,selection,role,name):
    value={'os':'ubuntu24.04','role':role,'name':name,'console_port':18008}
    monkeypatch.setattr(vm,'read',lambda key:value)
    monkeypatch.setattr(vm,'settings',lambda:('192.0.2.5','192.0.2.5'))
    state={'Id':'a'*64,'State':{'Running':True},'Config':{'Labels':{'io.mykos.vm.role':role,'io.mykos.vm.os':'ubuntu24.04'}},'NetworkSettings':{'Ports':{'8006/tcp':[{'HostIp':'192.0.2.5','HostPort':'18008'}]}}}
    monkeypatch.setattr(vm,'inspect',lambda key:state)
    assert vm.console_target(selection)=={'host':'192.0.2.5','port':18008,'container_id':'a'*64}
    state['NetworkSettings']['Ports']['8006/tcp'][0]['HostIp']='192.0.2.123'
    with pytest.raises(ValueError,match='does not match'):vm.console_target(selection)
    state['State']['Running']=False
    with pytest.raises(ValueError,match='not running'):vm.console_target(selection)
    with pytest.raises(ValueError):vm.console_target('../other')


def test_apache_console_rule_is_idempotent_tls_only_and_before_catchall():
    path=Path(__file__).resolve().parents[1]/'bin/mykos-console-proxy'
    loader=importlib.machinery.SourceFileLoader('console_apache_setup',str(path));spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    text='<VirtualHost *:80>\nProxyPass /mykos/ http://127.0.0.1:8000/\n</VirtualHost>\n<VirtualHost *:443>\nServerName prod.example\nProxyPass "/mykos/" "http://127.0.0.1:8000/"\n</VirtualHost>\n'
    result=module.transform(text)
    assert result.count(module.MARKER)==1 and module.transform(result)==result
    tls=result.split('<VirtualHost *:443>')[1]
    assert tls.index(module.MARKER)<tls.index('ProxyPass')
    assert 'ws://127.0.0.1:8000/$1' in tls and 'api/v2/' in tls
    assert text.split('<VirtualHost *:443>')[0]==result.split('<VirtualHost *:443>')[0]


def test_existing_console_websocket_rule_upgrades_for_peer_broker():
    path=Path(__file__).resolve().parents[1]/'bin/mykos-console-proxy'
    loader=importlib.machinery.SourceFileLoader('console_apache_peer_setup',str(path));spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    text='<VirtualHost *:443>\n'+module.MARKER+'\nRewriteEngine On\nRewriteCond %{HTTP:Upgrade} =websocket [NC]\nRewriteCond %{REQUEST_URI} ^/mykos/(api/v2/)?console/\nRewriteRule ^/mykos/(.*)$ ws://127.0.0.1:8000/$1 [P,L]\nProxyPass /mykos/ http://127.0.0.1:8000/\n</VirtualHost>'
    updated=module.transform(text)
    assert 'api/web/peers/[0-9a-f]{32}/proxy/' in updated
    assert module.transform(updated)==updated
    assert updated.count('RewriteRule')==1


def test_managed_bootstrap_include_upgrade_preserves_other_settings():
    path=Path(__file__).resolve().parents[1]/'bin/mykos-console-proxy'
    loader=importlib.machinery.SourceFileLoader('console_apache_include_setup',str(path));spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    text='# Managed by mykos-bootstrap\nProxyPreserveHost On\nRewriteEngine On\nRewriteCond %{HTTP:Upgrade} =websocket [NC]\nRewriteCond %{REQUEST_URI} ^/mykos/(api/v2/)?console/\nRewriteRule ^/mykos/(.*)$ ws://127.0.0.1:8000/$1 [P,L]\nProxyPass /mykos/ http://127.0.0.1:8000/\n'
    updated=module.transform(text)
    assert 'api/web/peers/[0-9a-f]{32}/proxy/' in updated
    assert module.transform(updated)==updated
    assert 'ProxyPreserveHost On' in updated
