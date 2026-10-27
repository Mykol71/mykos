"""Admin PowerShell command console over the private QEMU guest agent."""
import mykos_vm as vm
from mykos_commands import GuestAgent, powershell


def open_terminal(key, *, allow_image=False):
    value = vm.read(key)
    if value.get('role') != ('template' if allow_image else 'instance') or vm.VERSIONS.get(value.get('os'), ('',))[0] != 'windows':
        raise ValueError('PowerShell console requires a Windows instance')
    agent = GuestAgent(value,allow_prepared_image=True) if allow_image else GuestAgent(value)
    agent.close()
    print('Windows PowerShell via QEMU Guest Agent (no RDP or guest IP).', flush=True)
    print('Each line runs separately as the guest-agent service account. Interactive programs are not supported. Type exit to close.', flush=True)
    while True:
        try: script = input('PS> ')
        except EOFError: return
        except KeyboardInterrupt:
            print(); continue
        if script.strip().lower() == 'exit': return
        if not script.strip(): continue
        if len(script.encode('utf-8')) > 65536:
            print('Command is too long', flush=True);continue
        result = powershell(value, script, 120, allow_prepared_image=True) if allow_image else powershell(value, script, 120)
        print(result['output'], end='' if result['output'].endswith('\n') else '\n', flush=True)
        if result.get('timed_out'): print('Command timed out after 120 seconds.', flush=True)
        if result.get('truncated'): print('Output truncated.', flush=True)
