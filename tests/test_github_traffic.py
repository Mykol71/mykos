import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
stats=module('traffic_stats',ROOT/'services/helpers/mykos_download_stats.py')
view=module('traffic_view',ROOT/'services/web/rpm_download_stats_view.py')
def sample():
 return {'repository':'Mykol71/mykos','updated_at':1000,'views':{'count':20,'uniques':3,'views':[{'timestamp':'2026-11-13T00:00:00Z','count':10,'uniques':2},{'timestamp':'2026-11-12T00:00:00Z','count':10,'uniques':2}]},'clones':{'count':8,'uniques':2,'clones':[{'timestamp':'2026-11-13T00:00:00Z','count':8,'uniques':2}]},'paths':[{'path':'/<script>','count':4,'uniques':2}],'referrers':[{'referrer':'<img src=x>','count':5,'uniques':3}]}
def test_ingestion_staleness_and_last_good(tmp_path):
 p=tmp_path/'traffic.json';p.write_text(json.dumps(sample()));p.chmod(0o600)
 good=stats.traffic_report('Mykol71/mykos',1001,p);assert good['state']=='ok' and good['views']['uniques']==3
 assert stats.traffic_report('Mykol71/mykos',140000,p)['state']=='stale'
 p.write_text('bad');r=stats.traffic_report('Mykol71/mykos',1100,p,good);assert r['state']=='stale' and r['views']['count']==20 and r['updated_at']==1000
 r=stats.traffic_report('other/repo',1100,p,good);assert r['state']=='unavailable' and 'views' not in r
@pytest.mark.parametrize('mutation',[lambda v:v.update(repository='other/repo'),lambda v:v['views'].update(count=-1),lambda v:v['clones'].update(uniques=True),lambda v:v['clones'].update(uniques=999),lambda v:v['paths'][0].update(path='a\nb'),lambda v:v['views']['views'].append(v['views']['views'][0]),lambda v:v['views']['views'][0].update(timestamp='2026-02-31T00:00:00Z')])
def test_invalid_metrics(mutation):
 v=sample();mutation(v)
 with pytest.raises((ValueError,TypeError)):stats.validate_traffic(v,'Mykol71/mykos')
def test_only_allowlisted_aggregate_fields_retained():
 v=sample();v['token']='secret';v['paths'][0]['title']='secret';r=stats.validate_traffic(v,'Mykol71/mykos');assert 'secret' not in json.dumps(r)
def test_private_file_symlink_and_future(tmp_path):
 p=tmp_path/'traffic';v=sample();p.write_text(json.dumps(v));p.chmod(0o644);assert stats.traffic_report('Mykol71/mykos',1000,p)['state']=='unavailable'
 p.chmod(0o600);link=tmp_path/'link';link.symlink_to(p);assert stats.traffic_report('Mykol71/mykos',1000,link)['state']=='unavailable'
 assert stats.traffic_report('Mykol71/mykos',1,p)['state']=='unavailable'
def test_render_unique_totals_timezone_order_refresh_and_escape():
 v=sample();v['state']='ok';data={'configured':True,'updated_at':1700000000,'tracking_since':1700000000,'notice':'rules','repository':{'totals':[],'packages':[],'daily':[],'daily_timezone':'America/Chicago'},'github':{'state':'ok','assets':[]},'github_traffic':v};h=view.render(data)
 assert h.index('class="cards"')<h.index('GitHub traffic insights')<h.index('Repository activity by day')
 assert 'UTC' in h and 'CST/CDT' in h and 'not sums of daily uniques' in h and 'content="60"' in h
 assert '<script>' not in h and '&lt;script&gt;' in h and '<img src=x>' not in h
 assert 'Unique visitors' in h and '>3</strong>' in h
 data['github_traffic']={'state':'unavailable'};h=view.render(data);section=h.split('GitHub traffic insights')[1].split('Repository activity by day')[0];assert 'class="count"' not in section
@pytest.mark.parametrize('identity,allowed,success',[('mgreen',True,True),('admin',True,False),('rti',True,False),('mgreen',False,False),('peer:mgreen',True,False),('',True,False)])
def test_both_endpoints_authorize_before_loading(monkeypatch,identity,allowed,success):
 sys.path.insert(0,str(ROOT/'services/web'));api=module('traffic_api',ROOT/'services/web/mykos_admin_api.py');monkeypatch.setitem(sys.modules,'app',SimpleNamespace(mykos_terminal_permission=lambda *a,**k:{'allowed':allowed,'username':identity}))
 called=[];monkeypatch.setitem(sys.modules,'rpm_download_stats_view',SimpleNamespace(load=lambda:called.append(1) or {'format':1},render=lambda x:'private report'))
 for endpoint in (api.rpm_downloads,api.rpm_downloads_view):
  if success:assert endpoint(object()).headers['cache-control']=='no-store'
  else:
   with pytest.raises(api.HTTPException) as ex:endpoint(object())
   assert ex.value.status_code==403
 assert len(called)==(2 if success else 0)
def test_link_owner_only():
 assert "rpmStatsLink.hidden = data.role !== 'admin' || data.username !== 'mgreen'" in (ROOT/'services/web/index.html').read_text()
