"""KVM install probe verifies capability without starting a guest."""
import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import stat
import pytest

loader = importlib.machinery.SourceFileLoader('kvm_check', str(Path(__file__).resolve().parents[1]/'services/helpers/mykos-kvm-check'))
spec = importlib.util.spec_from_loader(loader.name, loader)
probe = importlib.util.module_from_spec(spec)
loader.exec_module(probe)


def test_probe_creates_and_closes_empty_vm(monkeypatch):
    monkeypatch.setattr(probe.platform, 'machine', lambda:'x86_64')
    monkeypatch.setattr(probe.os, 'stat', lambda path:SimpleNamespace(st_mode=stat.S_IFCHR))
    monkeypatch.setattr(probe.os, 'open', lambda *args:10)
    calls=[];closed=[]
    monkeypatch.setattr(probe.fcntl, 'ioctl', lambda fd, request, arg:calls.append((fd,request,arg)) or (12 if request==0xAE00 else 11))
    monkeypatch.setattr(probe.os, 'close', closed.append)
    assert 'available' in probe.check_kvm()
    assert calls==[(10,0xAE00,0),(10,0xAE01,0)]
    assert closed==[11,10]


def test_disabled_kvm_closes_device_and_warns(monkeypatch, capsys):
    monkeypatch.setattr(probe.platform, 'machine', lambda:'x86_64')
    monkeypatch.setattr(probe.os, 'stat', lambda path:SimpleNamespace(st_mode=stat.S_IFCHR))
    monkeypatch.setattr(probe.os, 'open', lambda *args:10)
    closed=[]
    def disabled(*args):raise OSError('virtualization disabled')
    monkeypatch.setattr(probe.fcntl, 'ioctl', disabled)
    monkeypatch.setattr(probe.os, 'close', closed.append)
    assert probe.main()==1
    assert closed==[10]
    assert 'Desktop VMs cannot start' in capsys.readouterr().err
