from pathlib import Path
import os,subprocess,pytest,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/helpers'))

@pytest.mark.parametrize('os_name,failed,expected',[('rh8rti',False,True),('rh8rti',True,False),('rh8',False,True)])
def test_stage_precedes_commit_and_failure_blocks_it(tmp_path,os_name,failed,expected):
 text=(ROOT/'pos/pos').read_text();function=text[text.index('finalize_native_image() {'):text.index('build-image() (')]
 log=tmp_path/'calls';fake=tmp_path/'podman';fake.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\nif [[ "$*" == *"/usr/bin/updateos stage"* ]] && [ "$FAIL" = 1 ]; then exit 42; fi\n');fake.chmod(0o755)
 result=subprocess.run(['bash','-c',function+'\nfinalize_native_image owned '+os_name],env={**os.environ,'PATH':str(tmp_path)+':'+os.environ['PATH'],'CALLS':str(log),'FAIL':str(int(failed)),'REPOSERVER':'repo.test'},capture_output=True,text=True)
 calls=log.read_text();assert (any(line.startswith('commit ') and line.endswith('owned '+os_name+':latest') for line in calls.splitlines()))==expected
 assert (result.returncode==0)==expected
 if os_name=='rh8rti':
  assert 'MYKOS_UPDATEOS_STRICT=1' in calls and 'MYKOS_IMAGE_BUILD=rh8rti' in calls
  if expected:
   assert 'systemctl enable smb.service' in calls
   assert calls.index('/usr/bin/updateos stage')<calls.index('commit ')
 else:assert '/usr/bin/updateos stage' not in calls


def test_unfinished_native_image_is_not_offered_for_staging(monkeypatch):
 import mykos_runtime as runtime,mykos_vm,json
 building={'Names':['rh8rti'],'Image':'localhost/mykos-building/rh8rti:0123456789abcdef','State':'running','Labels':{'io.mykos.image.building':'true'}}
 monkeypatch.setattr(runtime,'run',lambda args,**kwargs:json.dumps([building] if args[1]=='ps' else []))
 monkeypatch.setattr(mykos_vm,'prepared_templates',lambda:[])
 assert runtime.stage_images()==[]

def test_private_rti_start_validates_image_and_marks_build(monkeypatch):
 import importlib.util,importlib.machinery
 loader=importlib.machinery.SourceFileLoader('rti_private_start',str(ROOT/'services/helpers/mykos-native-image-start'))
 spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
 monkeypatch.setattr(module,'build_priority',lambda:None);monkeypatch.setattr(module,'arguments',lambda *args:[]);monkeypatch.setattr(module,'config',lambda:{})
 monkeypatch.setattr(module,'cgroup_arguments',lambda *args:[]);monkeypatch.setattr(module,'init_command',lambda *args:['/sbin/init'])
 calls=[];monkeypatch.setattr(module,'run',lambda args:calls.append(args) or 'owned')
 image='localhost/mykos-building/rh8rti:0123456789abcdef'
 assert module.launch('rh8rti',image)=='owned'
 assert 'io.mykos.image.building=true' in calls[0] and image in calls[0]
 with pytest.raises(ValueError):module.launch('rh8rti','untrusted:latest')
 assert len(calls)==1
