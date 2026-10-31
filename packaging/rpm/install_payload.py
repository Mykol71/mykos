"""Stage RPM-owned code from the canonical installer, excluding mutable state."""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

source=Path(__file__).resolve().parents[2]
loader=importlib.machinery.SourceFileLoader('mykos_installer',str(source/'bin/mykos-install'))
spec=importlib.util.spec_from_loader(loader.name,loader)
module=importlib.util.module_from_spec(spec);loader.exec_module(module)
runtime_python=sys.argv[2] if len(sys.argv)>2 else '/usr/bin/python3.9'
if runtime_python not in ('/usr/bin/python3.9','/usr/bin/python3.11','/usr/bin/python3'):raise ValueError('Unsupported RPM Python runtime')
installer=module.Installer(SimpleNamespace(root='/',python=runtime_python,public_origin='https://mykos.example.com'))
root=Path(sys.argv[1]);listing=[]
for name,entry in installer.entries().items():
    kickstart='/pos/ks/' in name and name.endswith('.ks')
    persistent_sysctl=name=='/etc/sysctl.d/90-mykos-inotify.conf'
    if entry['mutable'] and not (kickstart or persistent_sysctl):continue
    path=root/name.lstrip('/');path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(entry['data']);path.chmod(entry['mode'])
    group='mykosweb' if entry['owner'] in ('mykosweb','web-read') else 'root'
    config='%config(noreplace) ' if kickstart or persistent_sysctl else ''
    listing.append(config+'%%verify(not mtime) %%attr(%04o,root,%s) %s' % (entry['mode'],group,name))
# Track source files individually so operator-edited kickstarts survive RPM upgrades.
source_tree=root/'usr/share/mykos'
for path in [source_tree,*sorted(source_tree.rglob('*'))]:
    name='/'+str(path.relative_to(root))
    mode=path.stat().st_mode & 0o777
    if path.is_dir():listing.append('%%dir %%attr(%04o,root,root) %s' % (mode,name))
    elif path.is_file():
        config='%config(noreplace) ' if '/pos/ks/' in name and name.endswith('.ks') else ''
        listing.append(config+'%%verify(not mtime) %%attr(%04o,root,root) %s' % (mode,name))
Path('runtime.files').write_text('\n'.join(sorted(listing))+'\n')
