"""Exercise the real stage dispatcher through its queue boundary."""
from pathlib import Path
import importlib.util,importlib.machinery
import pytest
import mykos_vm,mykos_runtime,mykos_host_maintenance


def helper(tmp_path,monkeypatch,os_name):
    p=Path(__file__).parents[1]/'services/helpers/mykos-container-helper';loader=importlib.machinery.SourceFileLoader('stage_args292',str(p));spec=importlib.util.spec_from_loader(loader.name,loader);m=importlib.util.module_from_spec(spec);loader.exec_module(m)
    monkeypatch.setattr(m,'JOBS',tmp_path);monkeypatch.setattr(mykos_runtime,'JOBS',tmp_path)
    monkeypatch.setattr(mykos_host_maintenance,'admission',lambda:None);monkeypatch.setattr(mykos_runtime,'require_container_capacity',lambda *a:None)
    monkeypatch.setattr(m,'active',lambda shop:False);monkeypatch.setattr(m,'stage_check',lambda shop:{'exists':False,'desktop':False,'backup':False});monkeypatch.setattr(m,'config',lambda:{})
    monkeypatch.setattr(m,'stage_images',lambda:[{'os':os_name,'image':'localhost/'+os_name+':latest'}]);monkeypatch.setattr(mykos_vm,'read',lambda key:{'ready':True})
    calls=[];monkeypatch.setattr(m,'run',lambda args,**kw:calls.append(args) or '')
    return m,calls


@pytest.mark.parametrize('os_name',sorted(mykos_runtime.EL_VM_PROFILES))
def test_real_stage_queue_preserves_custom_ram(tmp_path,monkeypatch,os_name):
    m,calls=helper(tmp_path,monkeypatch,os_name)
    result=m.dispatch(['stage-start','97829201','no',os_name,'6144'])
    assert result['ram_mib']==6144 and result.get('desktop_profile') is None
    assert calls==[['/usr/bin/systemctl','start','--no-block','mykos-stage@97829201.service']]
