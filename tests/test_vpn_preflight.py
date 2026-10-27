import json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_linux as native
import mykos_vpn_gateway as gateway


def test_vpn_preflight_rejects_wrong_mount_before_commands(monkeypatch,tmp_path):
    monkeypatch.setattr(native.vm,'POS',tmp_path)
    monkeypatch.setattr(native,'inspect',lambda shop:('container',{'State':{'Running':True,'Pid':123},'Mounts':[{'Source':str(tmp_path/'wrong'),'Destination':'/etc/ipsec.d','RW':True}]}))
    monkeypatch.setattr(native,'run',lambda *a,**kw:pytest.fail('A wrong mount must be rejected without commands'))
    with pytest.raises(ValueError,match='customer folder'):native.vpn_preflight('01234567')


def test_gateway_mode_preflight_preserves_independent_configs(monkeypatch,tmp_path):
    monkeypatch.setattr(gateway,'ROOT',tmp_path/'missing')
    monkeypatch.setattr(gateway,'HOST_IPSEC',tmp_path/'host')
    monkeypatch.setattr(gateway.vm,'POS',tmp_path)
    folder=tmp_path/'.01234567';folder.mkdir();cfg=folder/'office.conn.conf';cfg.write_text('preserve existing independent VPN')
    assert gateway.preflight_mode('01234567','second','direct')['state']=='ready'
    with pytest.raises(ValueError,match='cannot be combined'):gateway.preflight_mode('host','mykos-gateway','shared')
    assert cfg.read_text()=='preserve existing independent VPN' and not gateway.ROOT.exists()
    assert gateway.preflight_mode('01234567','office','shared')['state']=='ready'


def test_existing_shared_profile_rejects_independent_add_without_mutation(monkeypatch):
    monkeypatch.setattr(gateway,'profiles',lambda:[{'shop':'01234567','connection':'office'}])
    with pytest.raises(ValueError,match='current routing was left unchanged'):gateway.preflight_mode('87654321','branch','direct')


def test_shared_gateway_requires_complete_managed_connection(tmp_path):
    path=tmp_path/'office.conn.conf';path.write_text('conn office\n leftsubnet=192.168.22.0/24\n rightsubnet=10.77.0.0/24\n')
    with pytest.raises(ValueError,match='full-tunnel'):gateway.managed_connection(tmp_path,'office','192.168.22.0/24','10.77.0.0/24')
    assert len(list(tmp_path.iterdir()))==1


def test_host_prefix_supports_full_remote_name_length():
    assert gateway.key('host','mykos-'+'a'*64).endswith('a'*64)
    with pytest.raises(ValueError):gateway.key('01234567','a'*65)
