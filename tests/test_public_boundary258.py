from pathlib import Path
import importlib.machinery,importlib.util
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]

def test_public_core_or_business_extension_owns_provisioning(tmp_path):
 loader=importlib.machinery.SourceFileLoader('boundary258_installer',str(ROOT/'bin/mykos-install'));spec=importlib.util.spec_from_loader(loader.name,loader);mod=importlib.util.module_from_spec(spec);loader.exec_module(mod)
 entries=mod.Installer(SimpleNamespace(root=str(tmp_path),python='/usr/bin/python3.11',public_origin='https://example.test')).entries(include_extensions=False)
 private=(ROOT/'services/web/rti_access.py').exists()
 assert ('/opt/mykos-web/pxe_api.py' in entries)==private
 assert ('/usr/local/sbin/mykos-pxe' in entries)==private
 assert ('/etc/systemd/system/mykos-pxe-gc.timer' in entries)==private
 assert ('pxe-ui.js' in (ROOT/'services/web/index.html').read_text())==private
 assert ('PXE.md' in (ROOT/'services/web/supporting_docs.py').read_text())==private
