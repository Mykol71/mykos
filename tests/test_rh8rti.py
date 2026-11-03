from pathlib import Path
import importlib.util,sys,re
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'services/helpers'))
import mykos_runtime as runtime
import mykos_host_monitoring as host

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_rti_is_separate_native_el8_selection():
 build=load('rti_build',ROOT/'pos/build_rootfs.py')
 assert 'rh8rti' in runtime.NATIVE_OS_NAMES and 'rh8rti' in runtime.STAGE_OS_NAMES
 assert build.SUPPORTED.fullmatch('rh8rti') and build.EL8.fullmatch('rh8rti')
 assert not build.SUPPORTED.fullmatch('rh8rti-evil')
 assert 'rh7|rh8|rh8rti|rh9|rh10' in (ROOT/'pos/pos').read_text()

def test_rti_packages_groups_and_exclusions_are_explicit():
 source=(ROOT/'pos/ks/rh8rti.ks').read_text();lines=source.split('%packages',1)[1].split('%end',1)[0].splitlines()[1:]
 required='aide audispd-plugins libreswan opensc openscap openscap-scanner pcsc-lite scap-security-guide net-tools java perl wget mailx procmail bind-utils ksh expect iptables iptables-services samba cups chrony ntpstat telnet fetchmail rsyslog python2 rear sed lrzsz ncurses-term rsync grub2-efi-x64-modules grub2-tools-efi sendmail crypto-policies-scripts mutt NetworkManager dos2unix nodejs usbutils audit'.split()
 assert set(required) <= set(lines)
 assert '@^minimal-environment' in lines and '@system-tools' in lines and '-firewalld' in lines
 assert lines.count('java')==1 and 'rootpw --lock' in source
 assert '-NetworkManager' not in lines and '-chrony' not in lines
 assert 'systemctl mask chronyd.service tuned.service auditd.service' in source
 assert 'COPY updateos /usr/bin/updateos' in (ROOT/'pos/pos').read_text()

def test_rti_uses_el8_native_rpmdb_conversion(monkeypatch):
 prep=load('rti_prepare',ROOT/'pos/prepare_ks.py')
 monkeypatch.setattr(prep,'builder_interpreter',lambda:['/usr/bin/python3'])
 text=prep.native_rpmdb_post((ROOT/'pos/ks/rh8rti.ks').read_text(),ROOT/'pos/ks/rh8rti.ks')
 assert '--major 8' in text and 'mykos-native-rpmdb' in text

def test_rti_identity_prefers_named_image_and_vendor_version(monkeypatch):
 monkeypatch.setattr(host,'image_release',lambda _:None)
 info={'ImageName':'a'*64,'Image':'a'*64};image={'RepoTags':['localhost/mykos-snapshots/01234567:123','localhost/rh8rti:latest'],'Labels':{'io.mykos.os':'rh8rti','io.mykos.os.version':'8.10'}}
 assert host.image_name(info,image)=='localhost/rh8rti:latest'
 result=host.image_os(info,image);assert result['os_type']=='Red Hat Enterprise Linux' and result['os_version']=='8.10'

def test_rti_ram_policy_rejects_capture_and_restore_before_criu(monkeypatch):
 import pytest,contextlib
 import mykos_snapshots as snap,mykos_linux
 state={'Config':{'Labels':{'io.mykos.os':'rh8rti'}},'State':{'Running':True},'HostConfig':{'AutoRemove':False}}
 assert host.ram_snapshots_supported(state) is False
 assert host.ram_snapshots_supported({'Config':{'Labels':{'io.mykos.os':'rh8'}}}) is True
 assert host.ram_snapshots_supported({'ImageName':'localhost/rh8rti:latest'}) is False
 monkeypatch.setattr(snap.vm,'locked',contextlib.nullcontext)
 monkeypatch.setattr(mykos_linux,'inspect',lambda _:('98982401.test',state))
 monkeypatch.setattr(snap,'criu_binary',lambda:pytest.fail('CRIU must not start for disabled RH8RTI snapshots'))
 with pytest.raises(ValueError,match='disabled for rh8rti'):snap.capture_native('98982401')
 monkeypatch.setattr(snap,'read_snapshot',lambda *args:(Path('/unused'),{'capture_mode':'criu-memory','os':'rh8rti'}))
 with pytest.raises(ValueError,match='disabled for rh8rti'):snap.restore_native('98982401','unused')
