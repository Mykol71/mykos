"""Terminal workers are observed without acquiring their locks or reading secrets."""
import io,json,os,stat,time
from pathlib import Path
import pytest
import mykos_build_observation as observation
import mykos_image_build as image
import mykos_jobs as jobs
import mykos_vm as vm

@pytest.fixture
def build(tmp_path,monkeypatch):
    root=tmp_path/'builds';root.mkdir(mode=0o700);folder=root/'rh8vm';folder.mkdir(mode=0o700)
    monkeypatch.setattr(image,'STATE',root)
    (folder/'status.json').write_text(json.dumps({'os':'rh8vm','state':'building','message':'assembling','updated_at':int(time.time())}));(folder/'status.json').chmod(0o600)
    (folder/'build.lock').touch(mode=0o644)
    (folder/'credentials.json').write_text('must-never-be-read-or-serialized')
    monkeypatch.setattr(jobs,'vm_unit_states',lambda:{})
    return folder

def kernel_row(path,kind='FLOCK',mode='WRITE'):
    s=path.stat();return f'1: {kind} ADVISORY {mode} 1234 {os.major(s.st_dev):02x}:{os.minor(s.st_dev):02x}:{s.st_ino} 0 EOF\n'

def kernel(monkeypatch,text):
    original=Path.open
    def opened(path,*args,**kwargs):
        if str(path)=='/proc/locks':return io.StringIO(text)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',opened)

def test_terminal_worker_has_running_job_without_systemd(build,monkeypatch):
    kernel(monkeypatch,kernel_row(build/'build.lock'))
    import fcntl
    monkeypatch.setattr(fcntl,'flock',lambda *a:pytest.fail('Observation must not contend with a real build'))
    assert observation.worker_lock_held('rh8vm') is True
    job=next(x for x in jobs.vm_jobs() if x['target']=='rh8vm')
    assert job['state']=='running' and job['phase']=='building' and not job['retry_allowed']
    assert 'must-never' not in json.dumps(job)

def test_unheld_or_wrong_inode_does_not_invent_live_worker(build,monkeypatch):
    kernel(monkeypatch,'1: FLOCK ADVISORY WRITE 1234 00:00:999999999 0 EOF\n')
    assert observation.worker_lock_held('rh8vm') is False
    assert jobs.vm_jobs()[0]['state']=='unknown'

def test_failed_terminal_worker_remains_non_retryable_while_cleaning(build,monkeypatch):
    kernel(monkeypatch,kernel_row(build/'build.lock'))
    (build/'status.json').write_text(json.dumps({'os':'rh8vm','state':'failed','updated_at':1}))
    job=jobs.vm_jobs()[0];assert not job['retry_allowed']
    monkeypatch.setattr(jobs,'ROOT',build.parent/'jobs')
    monkeypatch.setattr(image,'queue',lambda *a,**k:pytest.fail('Must not queue another worker'))
    with pytest.raises(ValueError,match='active or unavailable'):jobs.retry_vm('rh8vm',job['revision'])

def test_pre_container_build_is_listed_with_no_console_or_startable_image(build,monkeypatch):
    kernel(monkeypatch,kernel_row(build/'build.lock'))
    monkeypatch.setattr(vm,'VERSIONS',{'rh8vm':('el-linux','8')})
    monkeypatch.setattr(vm,'read',lambda *a:(_ for _ in ()).throw(RuntimeError('No VM registry yet')))
    monkeypatch.setattr(vm,'inspect',lambda *a:pytest.fail('Do not inspect a nonexistent container'))
    result=vm.installing_images();assert len(result)==1
    assert result[0]['os']=='rh8vm' and result[0]['build_state']=='building'
    assert result[0]['console_url'] is None and result[0]['direct_console_url'] is None
    assert 'must-never' not in json.dumps(result)

def test_settled_pre_container_status_is_not_a_fake_active_build(build,monkeypatch):
    kernel(monkeypatch,'')
    monkeypatch.setattr(vm,'VERSIONS',{'rh8vm':('el-linux','8')})
    monkeypatch.setattr(vm,'read',lambda *a:(_ for _ in ()).throw(RuntimeError('Absent')))
    assert vm.installing_images()==[]

def test_unsafe_lock_is_not_trusted(build,monkeypatch):
    (build/'build.lock').chmod(0o666)
    assert observation.worker_lock_held('rh8vm') is None
    (build/'build.lock').unlink();(build/'build.lock').symlink_to(build/'credentials.json')
    assert observation.worker_lock_held('rh8vm') is None

def test_reads_create_no_status_folders(tmp_path,monkeypatch):
    monkeypatch.setattr(image,'STATE',tmp_path/'absent')
    assert observation.worker_lock_held('rh8vm') is False
    with pytest.raises(FileNotFoundError):observation.progress('rh8vm')
    assert not (tmp_path/'absent').exists()
