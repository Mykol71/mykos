"""Job recovery failures use private local fixtures; never systemd or user images."""
import copy,json,os,subprocess,sys
from pathlib import Path
import pytest
import mykos_jobs as jobs

@pytest.fixture
def env(tmp_path,monkeypatch):
 monkeypatch.setattr(jobs,'ROOT',tmp_path/'jobs')
 monkeypatch.setattr(jobs,'boot_id',lambda:'boot-one')
 monkeypatch.setattr(jobs,'process_start',lambda pid:None)
 monkeypatch.setattr(jobs,'unit_state',lambda unit:'inactive')
 monkeypatch.setattr(jobs,'admission',lambda *a:None)
 monkeypatch.setattr(jobs,'vm_jobs',lambda:[])
 monkeypatch.setattr(jobs,'observed_jobs',lambda values:[])
 monkeypatch.setattr(jobs,'saved_downloads',lambda:[])
 calls=[]
 def run(args,**kwargs):
  calls.append(args)
  return subprocess.CompletedProcess(args,1 if args[:3]==['podman','image','exists'] else 0,'','')
 monkeypatch.setattr(jobs.subprocess,'run',run)
 return calls

def failed(kind='repository-sync',target='6'):
 with jobs.locked():
  value=jobs.new(kind,target);value.update(state='failed',phase='downloading',finished_at=value['created_at']+1);jobs.save(value)
 return value

def test_stale_retry_never_launches(env):
 value=failed()
 with pytest.raises(ValueError,match='changed'):jobs.retry(value['id'],99)
 assert not env

def test_duplicate_retry_returns_same_attempt_once(env):
 value=failed();a=jobs.retry(value['id'],value['revision']);b=jobs.retry(value['id'],value['revision'])
 assert a['id']==b['id'] and a['retry_of']==value['id']
 launches=[x for x in env if x[0]=='systemd-run'];assert len(launches)==1
 assert launches[0][-3:]==[jobs.WORKER,'--job',a['id']]
 assert '--slice=mykosreposync.slice' in launches[0]

@pytest.mark.parametrize('state',['queued','running','succeeded'])
def test_cannot_retry_nonfailed_job(env,state,monkeypatch):
 value=failed();value['state']=state
 if state=='running':
  value['unit']='mykos-jobs-'+value['id']+'.service';monkeypatch.setattr(jobs,'unit_state',lambda unit:'active')
 jobs.save(value)
 with pytest.raises(ValueError,match='Only failed'):jobs.retry(value['id'],value['revision'])
 assert not env

@pytest.mark.parametrize('field,token',[('pid','process_start'),('child_pid','child_start')])
def test_still_stopping_process_blocks_retry(env,monkeypatch,field,token):
 value=failed();value.update({field:123,token:'111'});jobs.save(value);monkeypatch.setattr(jobs,'process_start',lambda pid:'111' if pid==123 else None)
 with pytest.raises(ValueError,match='still running'):jobs.retry(value['id'],value['revision'])
 assert not env

def test_missing_identity_never_proves_live_or_dead(env,monkeypatch):
 value=failed();value.update(pid=123,process_start=None);monkeypatch.setattr(jobs,'process_start',lambda pid:'222')
 assert jobs.alive(value) is None and not jobs.settled(value)

def test_reused_pid_is_not_old_worker(env,monkeypatch):
 value=failed();value.update(pid=123,process_start='111');jobs.save(value);monkeypatch.setattr(jobs,'process_start',lambda pid:'222')
 assert jobs.alive(value) is False and jobs.settled(value)

def test_old_boot_never_adopts_same_pid(env,monkeypatch):
 value=failed();value.update(pid=123,process_start='111',boot_id='old-boot');monkeypatch.setattr(jobs,'process_start',lambda pid:'111')
 assert jobs.alive(value) is False and jobs.settled(value)

@pytest.mark.parametrize('kind,target',[('shell','all'),('native-build','all'),('native-build','../../etc'),('repository-sync','6;reboot'),('repository-sync','../6')])
def test_fixed_operations_reject_arbitrary_commands(env,kind,target):
 with pytest.raises(ValueError):jobs.selection(kind,target)
 assert not env

def test_existing_native_image_never_replaced_by_retry(env,monkeypatch):
 value=failed('native-build','centos6');monkeypatch.setattr(jobs.subprocess,'run',lambda args,**kw:subprocess.CompletedProcess(args,0,'',''))
 with pytest.raises(ValueError,match='image already exists'):jobs.retry(value['id'],value['revision'])
 assert not jobs.read(value['id']).get('retry_child')

def test_private_state_rejects_symlink(env,tmp_path):
 value=failed();p=jobs.ROOT/(value['id']+'.json');p.unlink();p.symlink_to(tmp_path/'unrelated')
 with pytest.raises(OSError):jobs.read(value['id'])
 assert not (tmp_path/'unrelated').exists()

def test_world_readable_history_rejected(env):
 value=failed();p=jobs.ROOT/(value['id']+'.json');p.chmod(0o644)
 with pytest.raises(ValueError,match='Unsafe'):jobs.read(value['id'])

def test_oversized_history_rejected(env):
 value=failed();p=jobs.ROOT/(value['id']+'.json');p.write_text('x'*65537)
 with pytest.raises(ValueError,match='Unsafe'):jobs.read(value['id'])

def test_crashed_worker_becomes_interrupted_not_success(env):
 value=failed();value.update(state='running',pid=44,process_start='1');jobs.save(value)
 result=jobs.history()['jobs'][0];assert result['state']=='interrupted' and result['retry_allowed']

def test_service_uncertainty_blocks_recovery(env,monkeypatch):
 value=failed();value['unit']='mykos-jobs-'+value['id']+'.service';jobs.save(value);monkeypatch.setattr(jobs,'unit_state',lambda unit:'unknown')
 assert not jobs.public(value)['retry_allowed']
 with pytest.raises(ValueError,match='unavailable|still running'):jobs.retry(value['id'],value['revision'])
 assert not env

def test_response_excludes_internal_and_sensitive_fields(env):
 value=failed();value.update(pid=123,process_start='1',boot_id='boot',password='never-export',command='secret-command',raw_log='private log')
 result=jobs.public(value);assert not any(x in json.dumps(result) for x in ('never-export','secret-command','private log','process_start','boot_id'))

def test_unknown_percentage_is_not_fabricated(env):
 value=failed();jobs.progress('MYKOS_JOB_PHASE validating',value);assert value['phase']=='validating' and value['progress'] is None
 jobs.progress('(9/10): file.rpm',value);assert value['progress']=={'completed':9,'total':10,'unit':'packages'}
 jobs.progress('MYKOS_JOB_PHASE publishing',value);assert value['phase']=='publishing' and value['progress'] is None
 jobs.progress('password=should-not-become-message',value);assert 'password' not in value['message']

def test_record_history_bound_keeps_active_attempts(env):
 with jobs.locked():
  for index in range(105):
   value=jobs.new('repository-sync','6');value.update(state='succeeded',created_at=index);jobs.save(value)
  active=jobs.new('native-build','centos6');jobs.prune()
 assert len(list(jobs.ROOT.glob('*.json')))==101
 assert jobs.read(active['id'])['state']=='queued'
