import gzip
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
stats=module('download_stats',ROOT/'services/helpers/mykos_download_stats.py')
view=module('stats_view',ROOT/'services/web/rpm_download_stats_view.py')
URL='/mykos-1.5-for-x86_64-el9/mykos-1.0-1.el9.noarch.rpm'
def line(uid='abcdefgh1',path=URL,status=200,size=100,connection='+',length='100',local='-',test='-',epoch=1700000000):
 return f'{uid}|{epoch}|GET|{path}|{status}|{size}|{connection}|{length}|{local}|{test}|v1\n'
@pytest.mark.parametrize('status,size,connection,length,category',[(200,100,'+','100','full'),(200,100,'-','100','full'),(206,50,'+','50','partial'),(200,100,'X','100','aborted'),(404,30,'+','30','failed'),(200,50,'+','100','other'),(200,100,'+','-','other'),(302,100,'+','100','other')])
def test_transfer_classification(status,size,connection,length,category):
 assert stats.parse(line(status=status,size=size,connection=connection,length=length))[5]==category
@pytest.mark.parametrize('value',[line(path='/repos/'+URL.lstrip('/')),line()])
def test_aliases_normalize(value):assert stats.parse(value)[2]=='mykos-1.0-1.el9.noarch.rpm'
@pytest.mark.parametrize('value',[line(test='1'),line(path='/repos/support/private.rpm'),line(path=URL+'?secret=1'),line().replace('|GET|','|HEAD|')])
def test_excluded_requests(value):assert stats.parse(value) is None

def test_ingestion_rotation_gzip_repeated_and_incomplete(tmp_path):
 c=stats.database(tmp_path/'stats.db');log=tmp_path/'mykos-rpm-downloads.log';log.write_text(line()+line(uid='abcdefgh2',local='1'));assert stats.ingest(c,tmp_path,0)['new_requests']==2;assert stats.ingest(c,tmp_path,0)['new_requests']==0
 rotated=log.with_name(log.name+'.1');log.rename(rotated);log.write_text(line(uid='abcdefgh3',status=206,size=50,length='50'));assert stats.ingest(c,tmp_path,0)['new_requests']==1
 with gzip.open(tmp_path/'mykos-rpm-downloads.log.2.gz','wb') as f:f.write(rotated.read_bytes())
 assert stats.ingest(c,tmp_path,0)['new_requests']==0
 with log.open('a') as f:f.write(line(uid='abcdefgh4')[:-1])
 assert stats.ingest(c,tmp_path,0)['new_requests']==0
 with log.open('a') as f:f.write('\n')
 assert stats.ingest(c,tmp_path,0)['new_requests']==1
 log.write_text(line(uid='abcdefgh5'));assert stats.ingest(c,tmp_path,0)['new_requests']==1
 assert c.execute('SELECT count(*) FROM events').fetchone()[0]==5;report=stats.repository_report(c);assert {p['audience'] for p in report['packages']}=={'internal','external'};assert 'abcdefgh' not in json.dumps(report);c.close()

def test_initial_cutoff_and_invalid_record(tmp_path):
 c=stats.database(tmp_path/'stats.db');(tmp_path/'mykos-rpm-downloads.log').write_text(line(epoch=1)+line(uid='abcdefgh2')+'bad record\n');r=stats.ingest(c,tmp_path,100);assert r=={'new_requests':1,'invalid_records':1};c.close()

class Response:
 def __init__(self,value):self.body=json.dumps(value).encode()
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self,*args):return self.body

def test_github_rpms_only_and_draft_excluded():
 releases=[{'tag_name':'v1','assets':[{'id':1,'name':'mykos.el9.rpm','download_count':3},{'id':2,'name':'SOURCE.tar.gz','download_count':99}]},{'tag_name':'v2','draft':True,'assets':[{'id':3,'name':'hidden.rpm','download_count':100}]}]
 g=stats.github_report('owner/repo',100,{},lambda *a,**kw:Response(releases));assert g['total']==3 and len(g['assets'])==1

def test_github_outage_keeps_last_good_not_zero():
 def fail(*args,**kwargs):raise OSError('do not leak arbitrary exception text')
 old={'state':'ok','updated_at':50,'assets':[{'downloads':17}],'total':17};g=stats.github_report('owner/repo',100,old,fail);assert g['state']=='stale' and g['total']==17 and g['updated_at']==50;assert 'do not leak' not in g['error']

def test_github_invalid_owner_cannot_choose_url():
 with pytest.raises(ValueError):stats.github_report('https://evil.invalid',100,{})

def test_view_escapes_and_unconfigured(tmp_path):
 assert 'not configured' in view.render(view.load(tmp_path/'absent'))
 data={'configured':True,'updated_at':1700000000,'tracking_since':1700000000,'notice':'<script>','repository':{'totals':[],'packages':[],'daily':[]},'github':{'state':'unavailable','assets':[]}}
 html=view.render(data);assert '<script>' not in html and '&lt;script&gt;' in html and 'unique installations' in html

def test_admin_authentication_boundary(monkeypatch):
 sys.path.insert(0,str(ROOT/'services/web'));api=module('stats_api',ROOT/'services/web/mykos_admin_api.py')
 for allowed,name,expected in [(True,'admin',True),(False,'viewer',False),(True,'peer:abc',False)]:
  monkeypatch.setitem(sys.modules,'app',SimpleNamespace(mykos_terminal_permission=lambda *a,**k:{'allowed':allowed,'username':name}))
  if expected:api.statistics_authorize(object())
  else:
   with pytest.raises(api.HTTPException) as error:api.statistics_authorize(object())
   assert error.value.status_code==403


def test_same_inode_replacement_equal_size_is_not_lost(tmp_path):
    c=stats.database(tmp_path/'stats.db');p=tmp_path/'mykos-rpm-downloads.log';p.write_text(line(uid='abcdefgh1'));assert stats.ingest(c,tmp_path,0)['new_requests']==1
    p.write_text(line(uid='abcdefgh2'));assert stats.ingest(c,tmp_path,0)['new_requests']==1;c.close()


def test_private_rti_boundary_if_installed():
    p=ROOT/'services/web/rti_access.py'
    if not p.exists():return
    rti=module('stats_rti_access',p)
    with pytest.raises(Exception) as error:rti.authorize_path({'type':'http','method':'GET','path':'/api/web/mykos/rpm-downloads'})
    assert error.value.status_code==403


# Central-time midnight/DST and compatible historical migration.
import datetime as dt
def _central_log(uid,epoch):return f'{uid}|{epoch}|GET|/mykos-1.5-for-x86_64-el9/mykos-test.el9.rpm|200|100|+|100|-|-|v1\n'
def _central_timestamp(text):return int(dt.datetime.fromisoformat(text).timestamp())
@pytest.mark.parametrize('utc,day,stamp',[('2026-11-13T01:00:00+00:00','2026-11-12','2026-11-12 19:00 CST'),('2026-07-01T01:00:00+00:00','2026-06-30','2026-06-30 20:00 CDT'),('2026-11-01T06:30:00+00:00','2026-11-01','2026-11-01 01:30 CDT'),('2026-11-01T07:30:00+00:00','2026-11-01','2026-11-01 01:30 CST'),('2026-03-08T07:30:00+00:00','2026-03-08','2026-03-08 01:30 CST'),('2026-03-08T08:30:00+00:00','2026-03-08','2026-03-08 03:30 CDT')])
def test_central_day_and_display(tmp_path,utc,day,stamp):
 epoch=_central_timestamp(utc);(tmp_path/'mykos-rpm-downloads.log').write_text(_central_log('central281id',epoch));c=stats.database(tmp_path/'stats.db');stats.ingest(c,tmp_path,0);r=stats.repository_report(c);assert r['daily'][0]['day']==day and r['daily_timezone']=='America/Chicago' and r['untimed_requests']==0
 html=view.render({'configured':True,'updated_at':epoch,'tracking_since':epoch,'notice':'counter rules','repository':r,'github':{'state':'ok','assets':[]}});assert stamp in html;assert 'http-equiv="refresh" content="60"' in html;assert html.index('class="cards"')<html.index('Repository activity by day');c.close()
def test_existing_events_backfill_without_recounting(tmp_path):
 text=_central_log('historical281',_central_timestamp('2026-11-13T01:00:00+00:00'));p=tmp_path/'mykos-rpm-downloads.log';p.write_text(text);c=stats.database(tmp_path/'stats.db');c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',stats.parse(text));st=p.stat();c.execute('INSERT INTO cursors VALUES(?,?,?,?)',(st.st_dev,st.st_ino,len(text),text.encode()[:64]));before=stats.repository_report(c)['totals'];assert stats.ingest(c,tmp_path,0)['new_requests']==0;r=stats.repository_report(c);assert r['totals']==before and r['untimed_requests']==0 and r['daily'][0]['day']=='2026-11-12';assert stats.ingest(c,tmp_path,0)['new_requests']==0;assert c.execute('SELECT count(*) FROM events').fetchone()[0]==1;c.close()
def test_unrecoverable_history_retains_honest_utc_rows(tmp_path):
 c=stats.database(tmp_path/'stats.db');c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',stats.parse(_central_log('missingtime281',_central_timestamp('2026-11-13T01:00:00+00:00'))));stats.ingest(c,tmp_path,0);r=stats.repository_report(c);assert r['daily']==[] and r['daily_legacy_utc'][0]['day']=='2026-11-13' and r['untimed_requests']==1 and r['totals'][0]['requests']==1
 html=view.render({'configured':True,'updated_at':1,'tracking_since':1,'notice':'rules','repository':r,'github':{'state':'ok','assets':[]}});assert 'original UTC daily rows' in html;c.close()
