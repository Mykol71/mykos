from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_serial as serial

def test_non_ubuntu_does_not_get_wrapper_shell(monkeypatch):
    monkeypatch.setattr(serial.vm,'read',lambda key:{'role':'instance','os':'win11'})
    with pytest.raises(ValueError,match='Only Ubuntu'):serial.serial_path('12345678')

def test_paused_guest_is_rejected(monkeypatch):
    monkeypatch.setattr(serial.vm,'read',lambda key:{'role':'instance','os':'ubuntu24.04','name':'12345678.host'})
    monkeypatch.setattr(serial.vm,'inspect',lambda name:{'State':{'Running':True,'Paused':True}})
    with pytest.raises(ValueError,match='Start the Ubuntu'):serial.serial_path('12345678')
