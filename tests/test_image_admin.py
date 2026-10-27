from pathlib import Path
import sys,json,subprocess
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_image_admin as images
import mykos_vm as vm

@pytest.mark.parametrize('selection',['../rh9','rocky7','alma11','centos7','fedora45','other'])
def test_invalid_image_selections_cannot_start_workers(selection):
    with pytest.raises(ValueError):images.selection(selection)

def test_remove_rejects_dependent_container_before_any_mutation(monkeypatch,tmp_path):
    import mykos_containers as containers
    import mykos_prune as prune
    monkeypatch.setattr(vm,'ROOT',tmp_path)
    monkeypatch.setattr(prune,'builders',lambda:{})
    monkeypatch.setattr(images.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,3,'inactive\n',''))
    calls=[]
    monkeypatch.setattr(images,'run',lambda args,**kw:calls.append(args) or json.dumps([{'Id':'sha256:abc'}]))
    monkeypatch.setattr(containers,'all_containers',lambda:[{'Name':'/rocky9','Image':'abc','Id':'base'},{'Name':'/12345678.host','Image':'sha256:abc','Id':'instance'}])
    with pytest.raises(ValueError,match='used by container'):images.remove('rocky9','sha256:abc')
    assert len(calls)==1 and calls[0][1:3]==['image','inspect']

def test_remove_rejects_snapshot_before_stopping_generic_base(monkeypatch,tmp_path):
    import mykos_containers as containers
    import mykos_snapshots as snapshots
    import mykos_prune as prune
    monkeypatch.setattr(vm,'ROOT',tmp_path);monkeypatch.setattr(prune,'builders',lambda:{})
    monkeypatch.setattr(images.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,3,'inactive\n',''))
    calls=[];monkeypatch.setattr(images,'run',lambda args,**kw:calls.append(args) or json.dumps([{'Id':'sha256:abc'}]))
    monkeypatch.setattr(containers,'all_containers',lambda:[{'Name':'/rocky9','Image':'abc','Id':'base'}])
    monkeypatch.setattr(snapshots,'list_snapshots',lambda:[{'os':'rocky9','image':'other'}])
    with pytest.raises(ValueError,match='intentional snapshot'):images.remove('rocky9','sha256:abc')
    assert len(calls)==1

def test_queue_requires_exact_confirmation(tmp_path,monkeypatch):
    monkeypatch.setattr(images,'STATE',tmp_path)
    with pytest.raises(ValueError,match='Confirm'):images.queue('rocky9','alma9')
    assert list(tmp_path.iterdir())==[]


def test_additional_image_alias_prevents_removal(monkeypatch,tmp_path):
    monkeypatch.setattr(vm,'ROOT',tmp_path)
    calls=[]
    monkeypatch.setattr(images,'run',lambda args,**kw:calls.append(args) or json.dumps([{'Id':'sha256:abc','RepoTags':['localhost/rocky9:latest','localhost/shared:latest']}]))
    with pytest.raises(ValueError,match='additional tags'):images.remove('rocky9','sha256:abc')
    assert len(calls)==1


@pytest.mark.parametrize('value,expected',[
    ('2026-10-26T12:00:00.123456789Z',1793016000),
    ('2026-10-26T07:00:00-05:00',1793016000),
    (None,None),('invalid',None)])
def test_image_production_time_uses_image_metadata(value,expected):
    assert images.produced_at(value)==expected


@pytest.mark.parametrize('label,parent,allowed',[
    ('mssql2022','parent',True),
    ('other','parent',False),
    ('mssql2022','unrelated',False),
])
def test_native_committed_image_accepts_only_verified_parent_base(monkeypatch,tmp_path,label,parent,allowed):
    import mykos_containers as containers
    import mykos_snapshots as snapshots
    import mykos_prune as prune
    monkeypatch.setattr(vm,'ROOT',tmp_path)
    monkeypatch.setattr(prune,'builders',lambda:{})
    monkeypatch.setattr(prune,'process_table',lambda:{})
    monkeypatch.setattr(images.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,3,'inactive\n',''))
    base={'Name':'/mssql2022','Image':'parent','ImageName':'localhost/mssql2022:latest','Id':'base','State':{'Running':True},'Config':{'Labels':{'io.mykos.os':label}}}
    current=[base];calls=[]
    monkeypatch.setattr(containers,'all_containers',lambda:current[:])
    monkeypatch.setattr(snapshots,'list_snapshots',lambda:[])
    def run(args,**kwargs):
        calls.append(args)
        if args[1:3]==['image','inspect']:return json.dumps([{'Id':'child','Parent':parent,'RepoTags':['localhost/mssql2022:latest']}])
        if args[1]=='stop':current.clear()
        return ''
    monkeypatch.setattr(images,'run',run)
    if allowed:
        assert images.remove('mssql2022','child')['state']=='deleted'
        assert [c[1] for c in calls]==['image','stop','rmi']
        assert '--force' not in sum(calls,[])
    else:
        with pytest.raises(ValueError,match='points to another image'):images.remove('mssql2022','child')
        assert len(calls)==1


def native_base(**updates):
    return {'Name':'/rh9','Id':'base-id','ImageName':'localhost/rh9:latest',
            'Config':{'Labels':{'io.mykos.os':'rh9'}},'State':{'Running':True},**updates}

@pytest.mark.parametrize('state',[
    native_base(Name='/12345678.host'),native_base(ImageName='unrelated'),
    native_base(Config={'Labels':{'io.mykos.os':'rh8'}}),
    native_base(State={'Running':False}),native_base(State={'Running':True,'Paused':True}),
])
def test_base_console_rejects_wrong_identity_or_stopped_base(monkeypatch,state):
    monkeypatch.setattr(vm,'inspect',lambda name:state)
    with pytest.raises(ValueError):images.base_container('rh9')


def test_native_capture_commits_exact_base_and_does_not_stop_or_remove(monkeypatch):
    import contextlib,mykos_prune as prune
    monkeypatch.setattr(vm,'locked',contextlib.nullcontext)
    monkeypatch.setattr(vm,'inspect',lambda name:native_base())
    monkeypatch.setattr(images,'job',lambda name:{'state':'idle'})
    monkeypatch.setattr(prune,'builders',lambda:{})
    monkeypatch.setattr(prune,'process_table',lambda:{})
    calls=[];monkeypatch.setattr(vm,'run',lambda args,**kw:calls.append(args) or 'new-image-id')
    assert images.capture('rh9','rh9')['base_running']
    assert calls==[[images.PODMAN,'commit','--pause=true','base-id','localhost/rh9:latest']]


def test_capture_requires_confirmation_before_any_inspection(monkeypatch):
    monkeypatch.setattr(vm,'inspect',lambda name:pytest.fail('Must not inspect before confirmation'))
    with pytest.raises(ValueError,match='Confirm'):images.capture('rh9','other')


def test_vm_recapture_keeps_generic_base_and_embeds_disk(monkeypatch):
    monkeypatch.setattr(vm,'read',lambda key:{'ready':True})
    monkeypatch.setattr(images,'base_container',lambda key:{})
    calls=[];monkeypatch.setattr(vm,'prepare',lambda *a,**kw:calls.append((a,kw)) or {'ready':True})
    images.capture('mac15','mac15')
    assert calls==[(('mac15',),{'replace':True,'keep_running':True,'embed_disk':True})]


def test_native_capture_rejects_active_installer(monkeypatch):
    import contextlib,mykos_prune as prune
    monkeypatch.setattr(vm,'locked',contextlib.nullcontext)
    monkeypatch.setattr(vm,'inspect',lambda name:native_base())
    monkeypatch.setattr(images,'job',lambda name:{'state':'idle'})
    monkeypatch.setattr(prune,'builders',lambda:{1:{'args':['anaconda','--kickstart','/var/tmp/rh9.ks']}})
    monkeypatch.setattr(vm,'run',lambda *a,**kw:pytest.fail('No commit during install'))
    with pytest.raises(ValueError,match='build is still active'):images.capture('rh9','rh9')
