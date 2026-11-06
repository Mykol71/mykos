"""Native build visibility requires no running generic base or finished image."""
import importlib.machinery,importlib.util,sys
from pathlib import Path
import pytest
HELPERS=Path(__file__).resolve().parents[1]/'services/helpers'
sys.path.insert(0,str(HELPERS))
import mykos_prune,mykos_vm,mykos_image_admin,mykos_runtime

def helper():
    loader=importlib.machinery.SourceFileLoader('build_visibility_helper',str(HELPERS/'mykos-container-helper'))
    spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module);return module

@pytest.mark.parametrize('os_name',['debian13','archlinux','sles15sp7','amazon2023','alpine'])
def test_stage_inventory_includes_native_build_without_any_image(monkeypatch,os_name):
    monkeypatch.setattr(mykos_prune,'builders',lambda:{11:{'args':['python3','./build_native_oci.py',os_name],'parent':1},12:{'args':['python3','./build_native_oci.py',os_name],'parent':11},13:{'args':['/sbin/anaconda','--kickstart','/var/tmp/rh9.ks'],'parent':1}})
    monkeypatch.setattr(mykos_vm,'installing_images',lambda:[])
    monkeypatch.setattr(mykos_image_admin,'catalog',lambda:[])
    module=helper();monkeypatch.setattr(module,'stage_images',lambda:[]);monkeypatch.setattr(mykos_runtime,'inventory_read',lambda action,producer:producer())
    result=module.dispatch(['stage-images'])
    assert result['images']==[] and result['captured_images']==[]
    assert len(result['builds'])==1
    row=result['builds'][0];assert row['os']==os_name and row['build_state']=='building' and row['unattended']
    assert 'console' not in row and 'console_url' not in row
    assert not any('/var/tmp' in str(value) or 'python3' in str(value) for value in row.values())

def test_completed_or_unrecognized_process_is_not_reported(monkeypatch):
    import mykos_native_builds as native
    for table in ({},{1:{'args':['python3','other.py','debian13']}},{1:{'args':['/sbin/anaconda','--kickstart','/var/tmp/rh9.ks']}}):
        monkeypatch.setattr(mykos_prune,'builders',lambda:table)
        assert native.installing_images()==[]
