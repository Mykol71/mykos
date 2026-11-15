"""A build cancellation must escape subprocess selectors instead of mimicking EINTR."""
from pathlib import Path
import os,subprocess,sys,json

def test_sigterm_during_owned_subprocess_wait_cleans_child_and_assembly(tmp_path):
    script=r"""
import sys,os,time,signal,threading,tempfile,json,subprocess
from pathlib import Path
from mykos_profiles import el_vm
import mykos_vm as vm,mykos_image_build as build
el=el_vm();root=Path(sys.argv[1]);children=[];timers=[];original=el.subprocess.Popen
vm.preflight=lambda *a:None;vm.storage_root=lambda:root;vm.safe_dir=lambda p:p.mkdir(parents=True,exist_ok=True)
build.report=lambda *a:None
start=time.monotonic()
def popen(*a,**kw):
    child=original(*a,**kw);children.append(child)
    timer=threading.Timer(.15,lambda:os.kill(os.getpid(),signal.SIGTERM));timers.append(timer);timer.start()
    return child
el.subprocess.Popen=popen
def disk(*a):
    el.run([sys.executable,'-c','import time;time.sleep(3)'])
    raise AssertionError('Cancellation was swallowed by a blocking selector')
el.build_disk=disk
try:
    el.build_template('rh9vm',3072,{'username':'mykos','password':'synthetic'})
    raise AssertionError('Build continued after cancellation')
except KeyboardInterrupt:pass
finally:
    for timer in timers:timer.cancel();timer.join()
    for child in children:
        if child.poll() is None:child.terminate()
        child.wait(timeout=5)
assert children and all(child.poll() is not None for child in children)
assert time.monotonic()-start<2,'Cancellation waited for the child to finish normally'
assert not list(root.iterdir()),'Owned disk assembly scratch survived cancellation'
print(json.dumps({'child_reaped':True,'assembly_cleaned':True,'cancellation_escaped_selector':True}))
"""
    result=subprocess.run([sys.executable,'-B','-c',script,str(tmp_path)],capture_output=True,text=True,timeout=10,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert result.returncode==0,result.stdout+result.stderr
    assert all(json.loads(result.stdout).values()) and not list(tmp_path.iterdir())
