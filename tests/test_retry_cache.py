"""Failed-download cache tests use isolated private trees only."""
import importlib.util,json,os,shutil,time
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cache284',ROOT/'repo/bin/retry_cache.py');cache=importlib.util.module_from_spec(spec);spec.loader.exec_module(cache)
@pytest.fixture
def env(tmp_path,monkeypatch):
 monkeypatch.setattr(cache,'HEADROOM',0);monkeypatch.setattr(cache,'MAX_BYTES',1000000)
 root=tmp_path/'repos';root.mkdir();return root

def work(root,selected='6',payload=b'candidate'):
 p=root/('.mykos-sync-'+str(len(list(root.iterdir()))));p.mkdir(mode=0o700);(p/'stage').mkdir();(p/'stage'/'repo').mkdir();(p/'stage'/'repo'/'a.rpm').write_bytes(payload)
 cache.write_owner(p,{'format':1,'run_id':'a'*32,'selection':selected});return p

def test_reuse_is_atomic_and_never_publication(env):
 p=work(env);assert cache.keep(env,'6',p,'b'*32);assert not p.exists();assert not (env/'repo').exists()
 assert cache.status(env)[0]['saved_packages']==1
 dest=env/'.mykos-sync-next';dest.mkdir(mode=0o700);calls=[]
 value=cache.take(env,'6',dest,lambda *a:calls.append(a));assert value['job_id']=='b'*32
 assert calls and (dest/'stage/repo/a.rpm').read_bytes()==b'candidate';assert not (env/cache.NAMESPACE).exists()

def test_other_selection_not_reused(env):
 cache.keep(env,'6',work(env),'b'*32);p=env/'new';p.mkdir(mode=0o700)
 assert cache.take(env,'7',p,lambda *a:pytest.fail()) is None
 assert cache.status(env)[0]['selection']=='6'

def test_corrupt_cid_is_rejected_before_cleanup_or_move(env):
 p=work(env);(p/'worker.cid').write_text('not-a-container');cache.keep(env,'6',p,'b'*32)
 dest=env/'dest';dest.mkdir(mode=0o700)
 with pytest.raises(ValueError,match='identity'):cache.take(env,'6',dest,lambda *a:pytest.fail('must not call worker cleanup'))
 assert (env/cache.NAMESPACE/'6/stage/repo/a.rpm').exists() and not any(dest.iterdir())

def test_expiry_and_owner_scoped_discard(env):
 cache.keep(env,'6',work(env),'b'*32);cache.keep(env,'7',work(env,'7'),'c'*32)
 assert cache.discard(env,'b'*32)==['6'];assert cache.status(env)[0]['job_id']=='c'*32
 cache.prune(env,now=time.time()+cache.TTL+1);assert not (env/cache.NAMESPACE).exists()

@pytest.mark.parametrize('key,value',[('saved_bytes',-1),('saved_packages','5'),('dropped_packages',None),('updated_at',float('nan'))])
def test_malformed_owner_blocks_destructive_actions(env,key,value):
 cache.keep(env,'6',work(env),'b'*32);p=env/cache.NAMESPACE/'6';v=cache.owner(p);v[key]=value;cache.write_owner(p,v)
 with pytest.raises(ValueError):cache.discard(env,'b'*32)
 assert (p/'stage/repo/a.rpm').exists()

def test_symlink_never_followed_or_removed(env,tmp_path):
 p=work(env);outside=tmp_path/'user';outside.write_text('preserve');(p/'stage/repo/link.rpm').symlink_to(outside)
 with pytest.raises(ValueError):cache.keep(env,'6',p,'b'*32)
 assert outside.read_text()=='preserve'

def test_unsafe_input_not_retained(env):
 p=work(env);(p/'credentials').write_text('private')
 with pytest.raises(ValueError,match='Unexpected'):cache.keep(env,'6',p,'b'*32)
 assert not (env/cache.NAMESPACE).exists()

def test_headroom_refuses_retention(env,monkeypatch):
 monkeypatch.setattr(cache,'HEADROOM',shutil.disk_usage(env).free+1)
 p=work(env);assert not cache.keep(env,'6',p,'b'*32);assert p.exists()

def test_total_budget_counts_existing_selection(env,monkeypatch):
 monkeypatch.setattr(cache,'MAX_BYTES',25000)
 cache.keep(env,'6',work(env,payload=b'a'*6000),'b'*32)
 cache.keep(env,'7',work(env,'7',b'b'*6000),'c'*32)
 assert sum(v['saved_bytes'] for v in cache.status(env))<=cache.MAX_BYTES
 assert sum(v['saved_packages'] for v in cache.status(env))==1

def test_worker_stop_failure_leaves_cache_and_destination_untouched(env):
 cache.keep(env,'6',work(env),'b'*32);dest=env/'dest';dest.mkdir(mode=0o700)
 def fail(*a):raise RuntimeError('owned worker still active')
 with pytest.raises(RuntimeError):cache.take(env,'6',dest,fail)
 assert not any(dest.iterdir()) and (env/cache.NAMESPACE/'6').exists()
