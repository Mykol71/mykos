"""Collectors must import in installed layout without pytest's helper PYTHONPATH."""
from pathlib import Path
import shutil
import subprocess
import sys


def test_details_collector_bootstraps_helpers_in_installed_layout(tmp_path):
    root=Path(__file__).resolve().parents[1]
    web=tmp_path/'opt/mykos-web';web.mkdir(parents=True)
    helpers=tmp_path/'usr/local/sbin'
    shutil.copytree(root/'services/helpers',helpers,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('collect-details.py','mykos_config.py'):
        shutil.copy2(root/'services/web'/name,web/name)
    # Relocate only the absolute installed helper path into this disposable root.
    shim=(root/'services/web/host_monitoring.py').read_text()
    assert shim.count("'/usr/local/sbin'")==1
    (web/'host_monitoring.py').write_text(shim.replace("'/usr/local/sbin'",repr(str(helpers))))
    code="import sys,runpy;sys.path.insert(0,"+repr(str(web))+");runpy.run_path("+repr(str(web/'collect-details.py'))+",run_name='collector_import_test');print('COLLECTOR_IMPORT_OK')"
    result=subprocess.run([sys.executable,'-I','-B','-c',code],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='COLLECTOR_IMPORT_OK'
    assert not list(tmp_path.rglob('__pycache__'))
    assert not (web/'data').exists(),'Import must not execute collection or write live data'
