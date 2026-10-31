import json
from pathlib import Path
import sys
from urllib.error import HTTPError
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/web'))
import meraki_sites as m


def fixture_rows():
    return ([{'id':'N_1','name':'Example Flowers'}],
            [{'networkId':'N_1','serial':'MX1','name':'Shop router','address':'123 Main St','lat':0,'lng':-90,'model':'MX85','secret':'never-cache'}],
            [{'networkId':'N_1','serial':'MX1','uplinks':[{'publicIp':'203.0.113.5','ip':'192.168.1.2'}]}])


def config(tmp_path):
    p=tmp_path/'pos.env';p.write_text("export MERAKI_API_KEY='private-key'\nMERAKI_ORG_ID=123 # org\nOTHER=unchanged\n");p.chmod(0o600);return p


def test_sanitized_device_match_and_coordinates():
    rows=m.inventory(*fixture_rows());site=m.match_site('203.0.113.5',rows)
    assert site['shop_name']=='Example Flowers' and site['lat']==0
    assert site['model']=='MX85' and 'secret' not in site
    assert m.match_site('203.0.113.99',rows) is None
    assert m.coordinates({'lat':True,'lng':-90})=={}
    assert m.coordinates({'lat':float('nan'),'lng':-90})=={}


def test_shared_public_ip_never_guesses_site_or_router():
    rows=m.inventory(*fixture_rows())
    assert m.match_site('203.0.113.5',rows+[dict(rows[0],network_id='N_2')]) is None
    site=m.match_site('203.0.113.5',rows+[dict(rows[0],serial='MX2')])
    assert site['shop_name']=='Example Flowers' and 'lat' not in site and 'serial' not in site


def test_up_down_and_unconfirmed_connections_all_enriched():
    rows=m.inventory(*fixture_rows());calls=[]
    node={'vpn':{'status':'up','established_connections':['mykos-A']},'vpn_topology':{'links':[
        {'name':'A','peer':'203.0.113.5'},{'name':'B','peer':'203.0.113.5'}], 'via_gateways':[{'peer':'203.0.113.5','full_tunnel_sa':False}]}}
    m.enrich({'1':node},lambda:(calls.append(1) or rows,'ready'))
    assert len(calls)==1
    assert node['vpn_topology']['links'][0]['meraki']['shop_name']=='Example Flowers'
    assert node['vpn_topology']['links'][0]['meraki']['vpn_status']=='up'
    assert node['vpn_topology']['links'][1]['meraki']['vpn_status']=='down'
    assert node['vpn_topology']['via_gateways'][0]['meraki']['vpn_status']=='unknown'
    down={'vpn':{'status':'down'},'vpn_topology':{'links':[{'name':'A','peer':'203.0.113.5'}]}}
    m.enrich({'2':down},lambda:(rows,'ready'))
    assert down['vpn_topology']['links'][0]['meraki']['vpn_status']=='down'


def test_gateway_enrichment_and_cloud_failure_does_not_break_monitoring():
    link={'peer':'203.0.113.5','full_tunnel_sa':True}
    nodes={'1':{'vpn_topology':{'via_gateways':[link]}}}
    m.enrich(nodes,lambda:(m.inventory(*fixture_rows()),'unavailable'))
    assert link['meraki']['stale'] is True
    def fail():raise OSError('network unavailable')
    m.enrich(nodes,fail)


def test_credentials_and_cache_without_secret_persistence(tmp_path):
    p=config(tmp_path);cache=tmp_path/'cache.json';calls=[]
    rows=fixture_rows();responses=dict(zip(('/networks','/devices','/appliance/uplink/statuses'),rows))
    def fetch(org,path,key,**kwargs):
        assert org=='123' and key=='private-key';calls.append(path);return responses[path]
    sites,status=m.load_sites(p,cache,fetch,now=2000)
    assert status=='ready' and len(calls)==3
    assert 'private-key' not in cache.read_text() and 'never-cache' not in cache.read_text()
    assert cache.stat().st_mode & 0o777==0o600
    assert m.load_sites(p,cache,lambda *a,**k:pytest.fail('Cache ignored'),now=2001)==(sites,status)
    assert len(calls)==3


def test_permission_denied_backoff_and_stale_expiration(tmp_path):
    p=config(tmp_path);cache=tmp_path/'cache.json'
    cache.write_text(json.dumps({'organization':'123','attempted_at':1,'updated_at':1,'sites':[{'peer':'203.0.113.5'}]}))
    def fail(*a,**k):raise HTTPError('https://api.meraki.com',403,'Forbidden',{},None)
    sites,status=m.load_sites(p,cache,fail,now=2000)
    assert sites and status=='permission_denied'
    assert m.load_sites(p,cache,lambda *a,**k:pytest.fail('Failure backoff ignored'),now=2001)[1]==status
    assert m.load_sites(p,cache,fail,now=m.MAX_STALE+10)[0]==[]


def test_missing_credentials_does_not_query_api(tmp_path):
    assert m.load_sites(tmp_path/'missing',tmp_path/'cache',lambda *a,**k:pytest.fail('No credentials'))==([], 'not_configured')
    p=config(tmp_path);p.chmod(0o666)
    with pytest.raises(ValueError):m.credentials(p)


class Response:
    def __init__(self,rows,link=''):self.rows=rows;self.headers={'Link':link}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,limit):return json.dumps(self.rows).encode()


def test_paginated_get_and_foreign_pagination_rejected():
    requests=[]
    class Opener:
        def open(self,request,timeout):
            requests.append(request)
            assert request.get_method()=='GET'
            return Response([{'id':'1'}],'<https://attacker.example/steal>; rel="next"')
    with pytest.raises(ValueError,match='pagination'):m.fetch_pages('123','/networks','test',Opener())
    assert len(requests)==1
    class Good:
        def open(self,request,timeout):
            requests.append(request)
            return Response([{'id':'2'}]) if 'startingAfter' in request.full_url else Response([{'id':'1'}],'<https://api.meraki.com/api/v1/organizations/123/networks?startingAfter=1>; rel="next"')
    assert len(m.fetch_pages('123','/networks','test',Good()))==2


def test_all_org_sites_visible_without_configured_vpn_and_unique_routers():
    sites=m.inventory(*fixture_rows())
    result=m.organization_sites(lambda:(sites,'ready'))
    assert len(result)==1
    assert result[0]['shop_name']=='Example Flowers'
    assert result[0]['vpn_status']=='unknown'
    assert m.organization_sites(lambda:([],'not_configured'))==[]
