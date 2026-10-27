from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_powershell as ps

def test_commands_use_guest_agent_not_host_shell(monkeypatch,capsys):
    value={'role':'instance','os':'win11','name':'12345678.host'}
    monkeypatch.setattr(ps.vm,'read',lambda key:value)
    monkeypatch.setattr(ps,'GuestAgent',lambda value:type('Agent',(),{'close':lambda self:None})())
    entered=iter(['Get-Date','exit']);monkeypatch.setattr('builtins.input',lambda prompt:next(entered))
    calls=[]
    monkeypatch.setattr(ps,'powershell',lambda v,s,t:calls.append((v,s,t)) or {'output':'ok','exit_code':0})
    ps.open_terminal('12345678')
    assert calls==[(value,'Get-Date',120)]
    assert 'Each line runs separately' in capsys.readouterr().out
