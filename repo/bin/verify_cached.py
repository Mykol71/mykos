#!/usr/bin/env python
"""Recheck saved RPM payloads inside a worker's vendor-only trusted RPM DB."""
from __future__ import print_function
import os,re,stat,subprocess,sys

def verify(root):
 root=os.path.realpath(root);paths=[];valid=0;invalid=0
 for base,dirs,names in os.walk(root,followlinks=False):
  if any(os.path.islink(os.path.join(base,name)) for name in dirs):raise ValueError('Symlink cache directory rejected')
  for name in names:
   if not name.endswith('.rpm'):continue
   path=os.path.join(base,name)
   if os.path.islink(path) or not stat.S_ISREG(os.lstat(path).st_mode):raise ValueError('Unsafe cached RPM')
   with open(path,'rb') as stream:magic=stream.read(4)
   if magic!=b'\xed\xab\xee\xdb':os.unlink(path);invalid+=1;continue
   paths.append(path)
 env=os.environ.copy();env['LC_ALL']='C'
 for offset in range(0,len(paths),32):
  chunk=paths[offset:offset+32]
  process=subprocess.Popen(['rpm','--checksig','--verbose','--']+chunk,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env)
  output=process.communicate()[0].decode('utf-8','replace')
  # A batch failure is resolved per file. Unsigned RPMs can have valid digests,
  # so an explicit trusted Signature: OK line is required in every case.
  for path in chunk:
   marker=path+':'
   start=output.find(marker)
   next_start=min([x for x in [output.find(other+':',start+len(marker)) for other in chunk if other!=path] if x>=0] or [len(output)])
   block=output[start:next_start] if start>=0 else ''
   code=process.returncode
   if code:
    single=subprocess.Popen(['rpm','--checksig','--verbose','--',path],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env)
    block=single.communicate()[0].decode('utf-8','replace');code=single.returncode
   accepted=code==0 and bool(re.search(r'Signature,[^\r\n]*: OK\s*$',block,re.M)) and not re.search(r'NOT OK|NOKEY|BAD|NOTTRUSTED',block)
   if accepted:valid+=1
   else:os.unlink(path);invalid+=1
 print('MYKOS_JOB_CACHE_VERIFIED %s' % valid)
 print('MYKOS_JOB_CACHE_REJECTED %s' % invalid)
 return valid,invalid

if __name__=='__main__':
 if len(sys.argv)!=2 or not os.path.isdir(sys.argv[1]):sys.exit('Select one private staged repository')
 verify(sys.argv[1])
