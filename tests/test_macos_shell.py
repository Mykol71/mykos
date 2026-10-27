from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_ssh as ssh

def test_ssh_uses_private_destination_and_no_host_credentials():
    args=ssh.ssh_arguments('10.88.0.2',Path('/private/known'),'mgreen')
    assert args[-3:]==['-l','mgreen','10.88.0.2']
    assert 'PubkeyAuthentication=no' in args and 'ForwardAgent=no' in args

@pytest.mark.parametrize('user',['-oProxyCommand=x','a b','root;id',''])
def test_login_validation(user):
    with pytest.raises(ValueError):ssh.ssh_arguments('10.88.0.2',Path('/known'),user)

def test_ubuntu_never_uses_ssh(monkeypatch):
    monkeypatch.setattr(ssh.vm,'read',lambda key:{'role':'instance','os':'ubuntu24.04'})
    with pytest.raises(ValueError,match='Only macOS'):ssh.destination('12345678')
