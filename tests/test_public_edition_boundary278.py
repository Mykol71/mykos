"""Public product content stays generic; private integration stays separately packaged."""
from pathlib import Path
from types import SimpleNamespace
import importlib.machinery,importlib.util,re
ROOT=Path(__file__).resolve().parents[1]
PRIVATE=(ROOT/'services/web/rti_access.py').is_file()

def test_visible_product_and_guides_have_the_intended_edition():
    html=(ROOT/'services/web/index.html').read_text()
    assert ('rti-mykos-containers-pane' in html)==PRIVATE
    assert ('RTI Desktops' in html)==PRIVATE
    assert ('Dove Desktops' in html)==PRIVATE
    if not PRIVATE:
        assert not re.search(r'\bRTI\b',html,re.I)
        for folder,pattern in ((ROOT/'docs','*.md'),(ROOT/'docs/rendered','*.html')):
            for p in folder.glob(pattern):
                assert not re.search(r'\bRTI\b',p.read_text(),re.I),str(p)

def test_installer_cannot_package_business_payload_as_public_core(tmp_path):
    loader=importlib.machinery.SourceFileLoader('edition278_installer',str(ROOT/'bin/mykos-install'))
    spec=importlib.util.spec_from_loader(loader.name,loader);mod=importlib.util.module_from_spec(spec);loader.exec_module(mod)
    entries=mod.Installer(SimpleNamespace(root=str(tmp_path),python='/usr/bin/python3.11',public_origin='https://example.test')).entries(include_extensions=False)
    blocked=('rti_access.py','pxe_api.py','pxe-ui.js','mykos-rti-restore.py','mykos_business_desktops.py','mykos-pxe')
    for name in blocked:assert any(Path(n).name==name for n in entries)==PRIVATE,name
    assert any(re.search(r'/rh[0-9]+rti[.]ks$',n) for n in entries)==PRIVATE
    if not PRIVATE:
        assert b'RTI Desktops' not in entries['/opt/mykos-web/index.html']['data']
        assert b'rti-mykos-containers-pane' not in entries['/opt/mykos-web/index.html']['data']

def test_public_cached_private_application_fields_are_removed():
    if PRIVATE:return
    spec=importlib.util.spec_from_file_location('edition278_monitoring',ROOT/'services/web/generic_monitoring.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    assert mod.details({'details':{'1':{'rti_version':'private','rti_backup_mounted':True,'RTI_status':'private','running':True}}})=={'details':{'1':{'running':True}}}
    assert mod.extended({'nodes':{'1':{'rti':{'version':'private'},'rti_backup_mounted':True,'resources':{'cpu':'1%'}}}})=={'nodes':{'1':{'resources':{'cpu':'1%'}}}}
