import json
from pathlib import Path
import sys
import subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_host_monitoring as host


def native():
    return {'Id':'abc','Name':'12345678.host','State':{'Pid':123,'Running':True,'StartedAt':'2026-10-25T03:00:00.123456789Z'}}


def test_vm_probes_do_not_run_commands(monkeypatch):
    info=native(); info['Config']={'Labels':{'io.mykos.vm.os':'win11'}}
    monkeypatch.setattr(host,'run',lambda *args,**kw: (_ for _ in ()).throw(AssertionError('VM probe')))
    assert host.network(info)=={}
    assert host.query_vpn(info)['status']=='unknown'


def test_network_uses_host_binary_and_rejects_reused_pid(monkeypatch):
    calls=[]
    monkeypatch.setattr(host,'process_token',lambda pid: ('token',1))
    def run(args,timeout):
        calls.append(args)
        return '[{"ifname":"eth1","address":"aa:bb:cc:dd:ee:ff","addr_info":[{"family":"inet","scope":"global","local":"192.168.222.196"}]}]'
    monkeypatch.setattr(host,'run',run)
    assert host.network(native())['ipv4']=='192.168.222.196'
    assert calls[0][:5]==['/usr/bin/nsenter','-t','123','-n','/usr/sbin/ip']
    tokens=iter([('old',1),('new',1)])
    monkeypatch.setattr(host,'process_token',lambda pid: next(tokens))
    assert host.network(native())=={}


def test_shared_vpn_rejects_stale_and_restart(tmp_path):
    info=native();path=tmp_path/'status.json'
    value={'collected_at':100,'vpn_samples':{host.name(info):{'identity':host.identity(info),'vpn':{'status':'up'}}}}
    path.write_text(json.dumps(value))
    assert host.cached_vpn(info,path,120)=={'status':'up'}
    assert host.cached_vpn(info,path,161)['status']=='unknown'
    info['State']['StartedAt']='new'
    assert host.cached_vpn(info,path,120)['status']=='unknown'


def test_stats_batch_and_started_timestamp(monkeypatch):
    calls=[]
    monkeypatch.setattr(host,'run',lambda args,timeout: calls.append(args) or '12345678.host|2%|1GiB / 4GiB|25%\n87654321.host|1%|2GiB / 4GiB|50%')
    other=native();other['Name']='87654321.host'
    assert len(host.statistics([native(),other]))==2
    assert len(calls)==1 and calls[0][-2:]==['12345678.host','87654321.host']
    assert host.started_epoch(native())==1761361200+31536000 # 2026-10-25 03:00 UTC


def test_vpn_parser_connection_names():
    output='000 Total IPsec connections: loaded 2, active 1\n000 IPsec SAs: total(1)\n000 "office": 1\n'
    assert host.parse_vpn(output)['status']=='up'
    assert host.parse_vpn(output)['connections']==['office']


def test_vm_network_uses_podman_metadata_without_commands(monkeypatch):
    info=native()
    info['Config']={'Labels':{'io.mykos.vm.os':'ubuntu22.04','io.mykos.network.name':'mykos-lan'}}
    info['NetworkSettings']={'Networks':{'mykos-lan':{'IPAddress':'192.168.22.232','MacAddress':'aa:bb:cc:dd:ee:ff'}}}
    monkeypatch.setattr(host,'run',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Guest command')))
    assert host.network(info)=={'interface':'eth0','ipv4':'192.168.22.232','mac':'aa:bb:cc:dd:ee:ff'}


def test_no_daemon_with_empty_config_is_not_configured(tmp_path):
    directory=tmp_path/'etc/ipsec.d';directory.mkdir(parents=True)
    main=tmp_path/'etc/ipsec.conf';main.write_text('config setup\ninclude /etc/ipsec.d/*.conf\n')
    assert host.vpn_without_daemon(tmp_path)['status']=='not_configured'
    (directory/'mykos-test.conn.conf').write_text('conn mykos-test\n')
    assert host.vpn_without_daemon(tmp_path)['status']=='down'


def test_targeted_container_query_filters_podman_on_host(monkeypatch):
    calls=[]
    monkeypatch.setattr(host,'run',lambda args,**kw:calls.append(args) or '[]')
    assert host.containers('12345678')==[]
    assert calls==[['podman','ps','-a','--format','json','--filter','name=^12345678\\.']]


def test_established_names_stay_on_the_same_status_line():
    # Real Libreswan4.12 shape from production: IKE and CHILD lines are adjacent.
    output = '\n'.join([
        '000 Total IPsec connections: loaded 2, active 1',
        '000 IPsec SAs: total(1), authenticated(1)',
        '000 "PG-QA": configured',
        '000 "waiting": configured',
        '000 #235: "PG-QA":4500 STATE_V2_ESTABLISHED_IKE_SA (established IKE SA); idle;',
        '000 #236: "PG-QA":4500 STATE_V2_ESTABLISHED_CHILD_SA (established Child SA); eroute owner;',
        '000 #237: "waiting":4500 STATE_V2_PARENT_I1; idle;',
        '000 #236: "PG-QA" Traffic: ESPin=336B ESPout=336B',
    ])
    result = host.parse_vpn(output)
    assert result['status'] == 'up'
    assert result['established_connections'] == ['PG-QA']
    ike_only = output.replace('STATE_V2_ESTABLISHED_CHILD_SA', 'STATE_V2_PARENT_I1')
    assert host.parse_vpn(ike_only)['established_connections'] == []


def test_established_names_include_only_established_child_states():
    output = '\n'.join([
        '000 #1: "ikev1-a": STATE_QUICK_I2 (IPsec SA established)',
        '000 #2: "ikev1-b": STATE_QUICK_R2 (IPsec SA established)',
        '000 #3: "ikev1-a": STATE_QUICK_I2 (IPsec SA established)',
        '000 #4: "pending": STATE_MAIN_I1',
    ])
    assert host.parse_vpn(output)['established_connections'] == ['ikev1-a','ikev1-b']
