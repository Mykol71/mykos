import json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_vpn_host as host
import mykos_vpn_gateway as gateway


def test_host_rules_preserve_inbound_replies_outer_transport_and_local_destinations():
    v={'shop':'12345678','connection':'office','local':'192.168.22.0/24','table':23001}
    chain,items,hook,nat=host.desired_rules(v,'192.168.22.1',['192.168.22.0/24','192.168.222.0/24'])
    assert items[0]==['-m','conntrack','--ctdir','REPLY','-j','RETURN']
    assert items[1][-1]=='RETURN' and '500,4500' in items[1]
    assert items[2]==['-p','esp','-j','RETURN']
    assert items[-1]==['-j','MARK','--set-xmark',host.MARK]
    assert ['-d','192.168.222.0/24','-j','RETURN'] in items
    assert nat[-2:]==['--to-source','192.168.22.1'] and nat[nat.index('--mark')+1]==host.MARK


def test_unrelated_host_policy_is_never_removed(monkeypatch):
    calls=[]
    monkeypatch.setattr(gateway,'run',lambda args,**kw:calls.append(args) or json.dumps([{'priority':12999,'table':111,'fwmark':'0x1234'}]))
    with pytest.raises(ValueError,match='in use'):host.rule({'table':23001},remove=True)
    assert len(calls)==1


def test_site_only_sa_never_activates_host_diversion(monkeypatch,tmp_path):
    monkeypatch.setattr(gateway.vm,'POS',tmp_path)
    folder=tmp_path/'.12345678';folder.mkdir();(folder/'office.vpn-peer').write_text('203.0.113.2\n')
    template={'proto':'esp','dst':'203.0.113.2','reqid':1,'mode':'tunnel','level':'required'}
    policy={'dir':'out','src':'192.168.22.0/24','dst':'10.77.0.0/24','tmpl':[template]}
    state={'proto':'esp','mode':'tunnel','dst':'203.0.113.2','reqid':1}
    monkeypatch.setattr(gateway,'run',lambda args,**kw:json.dumps([policy] if 'policy' in args else [state]))
    v={'shop':'12345678','connection':'office','local':'192.168.22.0/24'}
    assert host.full_tunnel_active(v) is False
    policy['dst']='0.0.0.0/0';assert host.full_tunnel_active(v) is True
    state['reqid']=2;assert host.full_tunnel_active(v) is False


def test_el8_el9_text_metadata_requires_default_selector_and_matching_reqid():
    text='src 192.168.22.0/24 dst 0.0.0.0/0\n dir out priority 1\n tmpl src 198.18.0.1 dst 198.18.0.2\n proto esp reqid 7 mode tunnel\n'
    rows=host.parse_xfrm(text,'policy')
    assert rows[0]['dst']=='0.0.0.0/0' and rows[0]['dir']=='out'
    assert rows[0]['tmpl'][0]==dict(src='198.18.0.1',dst='198.18.0.2',proto='esp',reqid=7,mode='tunnel')


def test_full_tunnel_dns_is_marked_before_local_network_bypass():
    v={'shop':'12345678','connection':'office','local':'192.168.22.0/24','table':23001}
    _,items,_,_=host.desired_rules(v,'192.168.22.1',['192.168.222.0/24'])
    local=items.index(['-d','192.168.222.0/24','-j','RETURN'])
    for protocol in ('udp','tcp'):
        dns=['!','-d','127.0.0.0/8','-p',protocol,'-m',protocol,'--dport','53','-j','MARK','--set-xmark',host.MARK]
        assert items.index(dns)<local
    # Replies to incoming sessions and outer IKE/ESP still bypass first.
    assert items[0][-1]=='RETURN' and 'REPLY' in items[0]
    assert '500,4500' in items[1] and 'esp' in items[2]


def test_dns_redirect_rules_are_scoped_to_owned_mark_and_reversible(monkeypatch):
    calls=[]
    monkeypatch.setattr(gateway,'checked_command',lambda args:False)
    monkeypatch.setattr(gateway,'run',lambda args,**kwargs:calls.append(args) or '')
    v={'shop':'12345678','connection':'office','local':'192.168.22.0/24','table':23001}
    host.firewall(v,'192.168.22.1',['192.168.222.0/24'])
    redirects=[args for args in calls if 'DNAT' in args]
    assert len(redirects)==2
    assert {args[args.index('-p')+1] for args in redirects}=={'udp','tcp'}
    for args in redirects:
        assert args[-2:]==['--to-destination','8.8.8.8']
        assert args[args.index('--mark')+1]==host.MARK
        assert args[args.index('--comment')+1]=='mykos-host-vpn-dns-12345678-office'
        assert ['!','-d','127.0.0.0/8']==args[args.index('!'):args.index('!')+3]
    calls.clear()
    monkeypatch.setattr(gateway,'checked_command',lambda args:'-C' in args)
    host.firewall(v,'192.168.22.1',['192.168.222.0/24'],remove=True)
    removed=[args for args in calls if 'DNAT' in args]
    assert len(removed)==2 and all('-D' in args for args in removed)
