import importlib.machinery,importlib.util
from pathlib import Path
import subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
def module():
 loader=importlib.machinery.SourceFileLoader('limits_installer',str(ROOT/'bin/mykos-install'));spec=importlib.util.spec_from_loader(loader.name,loader);m=importlib.util.module_from_spec(spec);loader.exec_module(m);return m

def test_shared_inotify_floor_preserves_larger_operator_limits(tmp_path,monkeypatch):
 m=module();quota=tmp_path/'quota';monkeypatch.setattr(m,'INOTIFY_INSTANCES',quota);calls=[];monkeypatch.setattr(m,'command',lambda args:calls.append(args))
 quota.write_text('128');m.ensure_container_limits();assert calls==[['/usr/sbin/sysctl','-w','fs.inotify.max_user_instances=4096']]
 calls.clear()
 for value in (4096,8192):quota.write_text(str(value));m.ensure_container_limits();assert calls==[]

def test_staged_install_keeps_operator_sysctl_configuration(tmp_path):
 root=tmp_path/'root'
 def install():subprocess.run([sys.executable,str(ROOT/'bin/mykos-install'),'install','--root',str(root)],check=True,capture_output=True)
 install();p=root/'etc/sysctl.d/90-mykos-inotify.conf';assert '4096' in p.read_text();p.write_text('fs.inotify.max_user_instances = 8192\n');install();assert p.read_text()=='fs.inotify.max_user_instances = 8192\n'
