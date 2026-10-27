"""Routing ownership and failure behavior; no real host network mutations."""
import contextlib,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_vpn_gateway as g
import mykos_vpn_topology as topology

@pytest.fixture
def profile():
    return dict(shop='12345678',connection='office',remote='0.0.0.0/0',site_remote='10.77.0.0/24',local='192.168.22.0/24',address='192.168.22.2',bridge='mykos0',table=23001,container_id='a'*64)

@pytest.mark.parametrize('field,value',[('bridge','br;reboot'),('table',True),('table',32768+10000),('container_id','replacement'),('address','10.0.0.1'),('local','192.168.22.3/24')])
def test_profiles_reject_unsafe_or_inconsistent_metadata(profile,field,value):
    profile[field]=value
    with pytest.raises(ValueError):g.validate_profile(profile)


def test_profile_reader_does_not_create_state_directory(monkeypatch,tmp_path):
    monkeypatch.setattr(g,'ROOT',tmp_path/'missing')
    assert g.profiles()==[]
    assert not (tmp_path/'missing').exists()


def test_rule_normalization_handles_numeric_tables_and_bare_host_source(monkeypatch,profile):
    rows=[dict(priority=13001,src='192.168.22.0',srclen=24,dst='all',iif='mykos0',table=23001),dict(priority=13000,src='192.168.22.2',iif='mykos0',table=254)]
    calls=[]
    monkeypatch.setattr(g,'run',lambda args,**kw:calls.append(args) or json.dumps(rows))
    g.policy(profile)
    assert len(calls)==1,'Existing owned policies must not be duplicated'


def test_policy_collision_never_overwrites_unrelated_rule(monkeypatch,profile):
    calls=[]
    monkeypatch.setattr(g,'run',lambda args,**kw:calls.append(args) or json.dumps([dict(priority=13001,src='all',table=999)]))
    with pytest.raises(ValueError,match='already used'):g.policy(profile)
    assert all('add' not in args and 'del' not in args for args in calls)


def test_route_collision_never_flushes_foreign_table(monkeypatch,profile):
    calls=[]
    monkeypatch.setattr(g,'run',lambda args,**kw:calls.append(args) or json.dumps([dict(table=23001,dst='default',protocol=99)]))
    with pytest.raises(ValueError,match='non-MYKOS'):g.route_state(profile)
    assert len(calls)==1


def test_new_gateway_is_blocked_before_namespace_failure(monkeypatch,profile):
    events=[]
    state=dict(Id='a'*64,State=dict(Running=True,Pid=999),NetworkSettings=dict(Networks={'mykos-lan':dict(IPAddress=profile['address'])}))
    monkeypatch.setattr(g,'inspect_gateway',lambda *a:state)
    monkeypatch.setattr(g,'config',lambda:{'native_network':'mykos-lan'})
    monkeypatch.setattr(g,'route_state',lambda v:[])
    monkeypatch.setattr(g,'run',lambda args,**kw:events.append(args))
    monkeypatch.setattr(g,'policy',lambda v:events.append('policy'))
    @contextlib.contextmanager
    def unavailable(state):
        raise ValueError('Gateway exited')
        yield
    monkeypatch.setattr(g,'pinned_namespace',unavailable)
    with pytest.raises(ValueError,match='exited'):g.apply(profile)
    assert 'unreachable' in events[0] and events[1]=='policy'
    assert not any(isinstance(x,list) and 'via' in x for x in events)


def test_firewall_exemption_is_bridge_scoped_and_guest_forwarding_requires_ipsec(profile):
    host,guest=g.rules(profile,1234)
    exempt=next(args for table,chain,args in host[1] if table=='nat' and chain=='POSTROUTING' and args[-1]=='ACCEPT')
    assert exempt[:2]==['-o','mykos0']
    items=guest[1]
    assert '--pol' in items[0][2] and 'ipsec' in items[0][2]
    assert items[1][2][-1]=='REJECT'
    assert all('FLUSH' not in args and '-F' not in args for _,_,args in host[1]+items)


def test_topology_uses_saved_metadata_and_ignores_symlinks_and_secrets(monkeypatch,tmp_path,profile):
    monkeypatch.setattr(topology.vm,'POS',tmp_path)
    directory=tmp_path/'.12345678';directory.mkdir()
    (directory/'office.conn.conf').write_text('conn office\n leftsubnet=192.168.22.0/24\n right=203.0.113.2\n rightsubnet=10.77.0.0/24\n')
    (directory/'office.secrets').write_text('PRIVATE-PSK')
    (directory/'skip.conn.conf').symlink_to(directory/'office.conn.conf')
    result=topology.topology('12345678',[profile])
    assert result['links']==[dict(name='office',local='192.168.22.0/24',remote='10.77.0.0/24',peer='203.0.113.2',mode='gateway')]
    assert 'PRIVATE-PSK' not in json.dumps(result)
    assert topology.topology('87654321',[profile])['via_gateways'][0]['gateway']=='12345678'


def test_default_stanza_does_not_replace_specific_site_in_topology(monkeypatch,tmp_path):
    monkeypatch.setattr(topology.vm,'POS',tmp_path)
    directory=tmp_path/'.12345678';directory.mkdir()
    (directory/'office.conn.conf').write_text('conn office\n leftsubnet=192.168.22.2/32\n right=203.0.113.2\n rightsubnet=10.77.0.0/24\n\nconn office-mykos-default\n also=office\n rightsubnet=0.0.0.0/0\n')
    assert topology.topology('12345678',[])['links'][0]['remote']=='10.77.0.0/24'


@pytest.mark.parametrize('mode',['guest','host'])
def test_shared_guest_dns_is_captured_before_host_resolver_and_without_sa(profile,mode):
    profile['mode']=mode
    if mode=='host':profile.update(shop='host',container_id='host',address='192.168.22.1')
    # No gateway PID and no SA: persisted profile must still protect guest DNS.
    host=g.rules(profile,None)[0][1]
    dns=[args for table,chain,args in host if table=='nat' and chain=='PREROUTING']
    assert len(dns)==2
    assert {r[r.index('-p')+1] for r in dns}=={'udp','tcp'}
    for args in dns:
        assert args[:4]==['-i','mykos0','-s','192.168.22.0/24']
        assert args[-2:]==['--to-destination','8.8.8.8']
        assert '--dport' in args and args[args.index('--dport')+1]=='53'


def test_shared_dns_cleanup_removes_only_exact_owned_rules(monkeypatch,profile):
    calls=[]
    monkeypatch.setattr(g,'checked_command',lambda args:True)
    monkeypatch.setattr(g,'run',lambda args,**kwargs:calls.append(args) or '')
    g.firewall(profile,None,remove=True)
    dns=[args for args in calls if '--dport' in args and args[args.index('--dport')+1]=='53']
    assert len(dns)==6 and all('-D' in args for args in dns)
    assert all(any(x.startswith('mykos-vpn-12345678-office-dns') for x in args) for args in dns)
    assert not any('-F' in args or '--flush' in args for args in calls)


def test_local_input_exception_is_marked_only_on_guest_bridge(monkeypatch,profile):
    profile.update(mode='host',shop='host',container_id='host',address='192.168.22.1')
    calls=[]
    monkeypatch.setattr(g,'run',lambda args,**kwargs:calls.append(args) or '')
    monkeypatch.setattr(g,'checked_command',lambda args:False)
    g.local_input_policy(profile)
    policies=[args for args in calls if 'add' in args]
    assert len(policies)==3
    assert {a[a.index('dir')+1] for a in policies}=={'in','fwd','out'}
    assert all(a[a.index('mark')+1]=='0x4d580000' and a[a.index('mask')+1]=='0xffff0000' and a[a.index('src')+1] in ('192.168.22.0/24','0.0.0.0/0') for a in policies)
    marker=next(a for a in calls if '--set-xmark' in a)
    assert marker[marker.index('-i')+1]=='mykos0'
    assert marker[marker.index('-s')+1]==marker[marker.index('-d')+1]=='192.168.22.0/24'


def test_foreign_local_mark_policy_never_overwritten(monkeypatch,profile):
    profile.update(mode='host',shop='host',container_id='host')
    calls=[]
    foreign='src 10.0.0.0/8 dst 0.0.0.0/0\n dir in priority 1\n mark 0x4d580000/0xffff0000\n'
    monkeypatch.setattr(g,'run',lambda args,**kw:calls.append(args) or foreign)
    with pytest.raises(ValueError,match='already in use'):g.local_input_policy(profile)
    assert len(calls)==1 and 'add' not in calls[0] and 'delete' not in calls[0]


def test_local_input_exception_idempotence_and_exact_cleanup(monkeypatch,profile):
    profile.update(mode='host',shop='host',container_id='host')
    calls=[]
    text=''.join('src 192.168.22.0/24 dst 0.0.0.0/0\n dir '+direction+' priority 1\n mark 0x4d580000/0xffff0000\n' for direction in ('in','fwd'))+'src 0.0.0.0/0 dst 192.168.22.0/24\n dir out priority 1\n mark 0x4d580000/0xffff0000\n'
    monkeypatch.setattr(g,'run',lambda args,**kw:calls.append(args) or text)
    monkeypatch.setattr(g,'checked_command',lambda args:True)
    g.local_input_policy(profile)
    assert not any('add' in a or '-I' in a for a in calls)
    calls.clear();g.local_input_policy(profile,remove=True)
    deleted=[a for a in calls if 'delete' in a]
    assert len(deleted)==3 and all('0x4d580000' in a and '0xffff0000' in a for a in deleted)
    assert not any('flush' in a or '-F' in a for a in calls)


def test_uninstall_deactivation_keeps_saved_profiles_under_lock(monkeypatch,profile):
    events=[]
    @contextlib.contextmanager
    def lock():
        events.append('locked');yield;events.append('unlocked')
    monkeypatch.setattr(g.vm,'locked',lock)
    monkeypatch.setattr(g,'profiles',lambda:[profile])
    monkeypatch.setattr(g,'remove_profile',lambda shop,connection,keep_profile=False:events.append((shop,connection,keep_profile)) or {'state':'deactivated'})
    assert g.deactivate_all()==[{'state':'deactivated'}]
    assert events==['locked',('12345678','office',True),'unlocked']
