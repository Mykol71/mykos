"""Per-base console ownership and session-only power-command guards."""
import base64,fcntl,os
from pathlib import Path
import mykos_vm as vm

SHELL_GUARD=r'''
unset HISTFILE
_mykos_power_disabled() { printf '%s\n' 'Shutdown/reboot is disabled in a base-image session. Exit to reset to the captured image.' >&2; return 1; }
shutdown() { _mykos_power_disabled; }; reboot() { _mykos_power_disabled; }
poweroff() { _mykos_power_disabled; }; halt() { _mykos_power_disabled; }
kexec() { _mykos_power_disabled; }; logout() { exit; }
init() { case "${1:-}" in 0|6) _mykos_power_disabled ;; *) command init "$@" ;; esac; }
systemctl() {
  local arg
  for arg in "$@"; do case "$arg" in reboot|poweroff|halt|kexec|shutdown|exit|soft-reboot|reboot.target|poweroff.target|halt.target|shutdown.target|kexec.target) _mykos_power_disabled;return 1;; esac;done
  command systemctl "$@"
}
sudo() {
  local arg
  for arg in "$@"; do case "${arg##*/}" in shutdown|reboot|poweroff|halt|kexec) _mykos_power_disabled;return 1;; esac;done
  command sudo "$@"
}
readonly -f _mykos_power_disabled shutdown reboot poweroff halt kexec init systemctl sudo logout
printf '%s\n' 'BASE IMAGE: shutdown/reboot disabled. Capture changes before exiting; logout or window close restores the captured image.'
'''


def shell_command(*,mac=False,logout_marker=None):
    import shlex
    code=SHELL_GUARD
    if logout_marker:
        if not logout_marker.isalnum():raise ValueError('Invalid console logout token')
        code='trap '+shlex.quote("printf '\\n"+logout_marker+"\\n'")+' EXIT\n'+code
    encoded=base64.b64encode(code.encode()).decode()
    decoder='/usr/bin/base64 -D' if mac else 'base64 -d'
    return 'exec /bin/bash --noprofile --rcfile <(printf %s '+shlex.quote(encoded)+' | '+decoder+') -i'


def acquire(os_name, *, operation=False):
    from mykos_image_admin import STATE,selection,job
    selection(os_name);vm.safe_dir(STATE)
    fd=os.open(STATE/(os_name+'.session.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('This base image already has an open console')
        pending=job(os_name)
        if not operation and pending.get('state') in ('queued','deleting','resetting'):
            raise ValueError('An image operation is in progress; wait before connecting')
        return fd
    except BaseException:os.close(fd);raise


def windows_script(script):
    # This is an accidental-command guard, not a sandbox for an administrator.
    import re
    if re.search(r'(?i)(?<![\w-])(?:restart-computer|stop-computer|shutdown(?:\.exe)?|psshutdown(?:\.exe)?)(?![\w-])',script):
        raise ValueError('Shutdown/reboot is disabled in a base-image session; exit to reset')
    return script
