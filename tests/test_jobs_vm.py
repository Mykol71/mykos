"""VM recovery uses only fixed status fields and the builder queue CAS."""
import json,subprocess
from pathlib import Path
import pytest
import mykos_jobs as jobs
import mykos_image_build as image

@pytest.fixture
def vm(tmp_path,monkeypatch):
 monkeypatch.setattr(image,'STATE',tmp_path/'vm');image.STATE.mkdir(mode=0o700)
 monkeypatch.setattr(jobs,'ROOT',tmp_path/'jobs');monkeypatch.setattr(jobs,'vm_unit_states',lambda:{})
 folder=image.STATE/'win11';folder.mkdir(mode=0o700)
 p=folder/'status.json';p.write_text(json.dumps({'os':'win11','state':'failed','updated_at':1}));p.chmod(0o600)
 (folder/'credentials.json').write_text('must-never-read')
 return p

def test_failed_vm_is_only_resumable_when_unit_settled(vm,monkeypatch):
 assert jobs.vm_jobs()[0]['retry_allowed']
 for state in ('active','activating','deactivating','unknown'):
  monkeypatch.setattr(jobs,'vm_unit_states',lambda:{'win11':state})
  assert not jobs.vm_jobs()[0]['retry_allowed']
  with pytest.raises(ValueError,match='active or unavailable'):jobs.retry_vm('win11',jobs.vm_revision(jobs.vm_status('win11')))

def test_stale_vm_resume_never_queues(vm,monkeypatch):
 monkeypatch.setattr(image,'queue',lambda *a,**k:pytest.fail('must not queue'))
 with pytest.raises(ValueError,match='changed'):jobs.retry_vm('win11','outdated')

def test_vm_resume_passes_atomic_status_revision_and_no_credentials(vm,monkeypatch):
 calls=[];monkeypatch.setattr(image,'queue',lambda *a,**k:calls.append((a,k)) or {'state':'queued'})
 revision=jobs.vm_revision(jobs.vm_status('win11'));jobs.retry_vm('win11',revision)
 assert calls==[(('win11',),{'expected_job_revision':revision})]
 assert 'must-never-read' not in json.dumps(jobs.vm_jobs())

def test_unproven_vm_worker_is_unknown_not_running(vm):
 vm.write_text(json.dumps({'os':'win11','state':'building','updated_at':1}))
 result=jobs.vm_jobs()[0];assert result['state']=='unknown' and not result['retry_allowed']

def test_vm_status_symlink_rejected(vm,tmp_path):
 target=tmp_path/'other';target.write_text('{}');vm.unlink();vm.symlink_to(target)
 with pytest.raises(OSError):jobs.vm_status('win11')


def test_ready_vm_is_complete_and_never_labeled_queued(vm):
 vm.write_text(json.dumps({'os':'win11','state':'ready','updated_at':1}))
 result=jobs.vm_jobs()[0]
 assert result['state']=='succeeded' and result['phase']=='complete' and result['message']=='Image ready'
 assert not result['retry_allowed']

def test_stale_vm_queue_without_worker_is_unknown(vm):
 vm.write_text(json.dumps({'os':'win11','state':'queued','updated_at':1}))
 result=jobs.vm_jobs()[0];assert result['state']=='unknown' and not result['retry_allowed']
