"""Private, bounded failed-sync candidates; never a published repository tree."""
import json,math,os,re,shutil,stat,tempfile,time
from pathlib import Path
NAMESPACE='.mykos-retry-cache'
MAX_BYTES=256*1024**3
TTL=7*86400
HEADROOM=5*1024**3
ALLOWED=frozenset(('6','7','8','9','10','centos6','centos7','oracle6','oracle7','scientific6','scientific7','springdale6','springdale7'))

def selector(value):
 value=str(value)
 if value not in ALLOWED:raise ValueError('Invalid repository cache selection')
 return value

def directory(path,private=False):
 st=path.lstat()
 if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o022 or (private and stat.S_IMODE(st.st_mode)!=0o700):raise ValueError('Unsafe repository retry directory')
 return path

def cache_root(root,create=False):
 root=Path(root).resolve(strict=True);path=root/NAMESPACE
 if not path.exists() and not path.is_symlink():
  if not create:return None
  path.mkdir(mode=0o700)
 return directory(path,True)

def owner(path):
 directory(path,True)
 with os.fdopen(os.open(path/'owner.json',os.O_RDONLY|os.O_NOFOLLOW)) as stream:
  st=os.fstat(stream.fileno())
  if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o022 or st.st_size>16384:raise ValueError('Unsafe retry ownership record')
  value=json.load(stream)
 if value.get('format')!=1 or not re.fullmatch(r'[0-9a-f]{32}',str(value.get('run_id',''))):raise ValueError('Invalid retry ownership record')
 return value

def files(path):
 directory(path,True);out=[]
 for base,dirs,names in os.walk(path,followlinks=False):
  for name in dirs:
   p=Path(base)/name;directory(p)
  for name in names:
   p=Path(base)/name;st=p.lstat()
   if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o022:raise ValueError('Unsafe retry candidate file')
   out.append(p)
 return out

def write_owner(path,value):
 fd,name=tempfile.mkstemp(prefix='.owner-',dir=path)
 try:
  with os.fdopen(fd,'w') as out:json.dump(value,out);out.flush();os.fsync(out.fileno())
  os.replace(name,path/'owner.json')
 finally:
  if os.path.exists(name):os.unlink(name)

def candidates(root):
 parent=cache_root(root)
 if parent is None:return []
 out=[]
 for p in sorted(parent.iterdir()):
  selected=selector(p.name);value=owner(p)
  if value.get('cache_schema')!=1 or value.get('selection')!=selected or value.get('worker_stopped') is not True:raise ValueError('Invalid retry cache state')
  if any(type(value.get(k)) is not int or value[k]<0 for k in ('saved_bytes','saved_packages','dropped_packages')):raise ValueError('Invalid retry cache counts')
  if not isinstance(value.get('updated_at'),(int,float)) or not math.isfinite(value['updated_at']):raise ValueError('Invalid retry cache timestamp')
  out.append((p,value))
 return out

def remove_empty_parent(root):
 p=cache_root(root)
 if p is not None and not any(p.iterdir()):p.rmdir()

def prune(root,now=None):
 now=time.time() if now is None else now;values=candidates(root);total=sum(v.get('saved_bytes',0) for p,v in values)
 for path,value in sorted(values,key=lambda item:item[1]['updated_at']):
  if now-value['updated_at']>TTL or total>MAX_BYTES or shutil.disk_usage(root).free<HEADROOM:
   files(path);shutil.rmtree(path);total-=value.get('saved_bytes',0)
 remove_empty_parent(root)

def keep(root,selected,work,job_id=None):
 """Called only after exact owned-worker shutdown under the shared sync lock."""
 selected=selector(selected);value=owner(work)
 if not (work/'stage').is_dir():return False
 all_files=files(work);packages=[p for p in all_files if p.suffix=='.rpm']
 if not packages:return False
 # Caches contain downloaded candidate files, not entitlements or secret inputs.
 if set(p.name for p in work.iterdir())-{'owner.json','stage','input','worker.cid'}:raise ValueError('Unexpected private sync input; retry cache refused')
 prune(root);parent=cache_root(root,True);target=parent/selected
 if target.exists() or target.is_symlink():raise ValueError('Retry cache appeared concurrently; preserve both candidates')
 other=sum(v['saved_bytes'] for p,v in candidates(root));budget=max(0,MAX_BYTES-other)
 size=sum(p.stat().st_size for p in all_files);dropped=0
 # Remove only owned RPM candidates when space is constrained; missing files
 # are downloaded on retry and fresh complete metadata is still mandatory.
 for p in sorted(packages,key=lambda p:p.stat().st_size,reverse=True):
  if size<=budget-16384 and shutil.disk_usage(root).free>=HEADROOM:break
  size-=p.stat().st_size;p.unlink();dropped+=1
 if size>budget-16384 or shutil.disk_usage(root).free<HEADROOM:return False
 if job_id is not None and not re.fullmatch(r'[0-9a-f]{32}',job_id):raise ValueError('Invalid cache job identity')
 value.update(cache_schema=1,selection=selected,job_id=job_id,worker_stopped=True,updated_at=time.time(),saved_bytes=size,saved_packages=len(packages)-dropped,dropped_packages=dropped)
 write_owner(work,value);os.replace(work,target)
 prune(root)
 if target.exists():print('MYKOS_JOB_CACHE_SAVED %s %s' % (size,len(packages)-dropped),flush=True)
 else:print('MYKOS_JOB_CACHE_NOT_RETAINED capacity',flush=True)
 return True

def take(root,selected,work,cleanup):
 selected=selector(selected);prune(root);parent=cache_root(root)
 if parent is None:return None
 path=parent/selected
 if not path.exists() and not path.is_symlink():return None
 value=owner(path)
 if value.get('cache_schema')!=1 or value.get('selection')!=selected:raise ValueError('Wrong retry cache selection')
 files(path)
 cid=path/'worker.cid'
 if cid.exists() and (cid.is_symlink() or not re.fullmatch(r'[0-9a-f]{64}',cid.read_text().strip())):raise ValueError('Unsafe cached worker identity')
 cleanup(path,value['run_id'])
 # Moving the whole private tree keeps owner/stage atomic across interruption.
 if any(work.iterdir()):raise ValueError('Retry destination is not empty')
 os.replace(path,work);remove_empty_parent(root)
 cid=work/'worker.cid'
 if cid.exists():
  if cid.is_symlink() or not re.fullmatch(r'[0-9a-f]{64}',cid.read_text().strip()):raise ValueError('Unsafe cached worker identity')
  cid.unlink()
 print('MYKOS_JOB_CACHE_RESTORED %s %s' % (value['saved_bytes'],value['saved_packages']),flush=True)
 return value

def status(root):
 return [{k:v[k] for k in ('selection','job_id','saved_bytes','saved_packages','updated_at','dropped_packages') if k in v} for p,v in candidates(root)]

def discard(root,job_id):
 if not re.fullmatch(r'[0-9a-f]{32}',job_id):raise ValueError('Invalid cache job identity')
 removed=[]
 for p,value in candidates(root):
  if value.get('job_id')==job_id:
   files(p);shutil.rmtree(p);removed.append(value['selection'])
 remove_empty_parent(root);return removed
