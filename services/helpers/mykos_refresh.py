"""One-time, resource-scoped UI collection after an administrator action."""
from contextlib import contextmanager
import os
import re
import sys
from mykos_runtime import run as default_run


@contextmanager
def priority():
    previous = None
    if os.geteuid() == 0:
        previous = os.getpriority(os.PRIO_PROCESS, 0)
        os.setpriority(os.PRIO_PROCESS, 0, -5)
    try:
        yield
    finally:
        if previous is not None:
            os.setpriority(os.PRIO_PROCESS, 0, previous)


def refresh(target, run=default_run):
    if target != 'host' and not re.fullmatch(r'[0-9]{8}', target):
        raise ValueError('Expected host or eight-digit ID')
    collectors = ('collect-nodes.py',) if target == 'host' else ('collect-nodes.py', 'collect-details.py', 'collect-extended.py')
    for collector in collectors:
        run(['/usr/bin/nice', '-n', '-5', sys.executable, '/opt/mykos-web/' + collector, target], timeout=45)


def best_effort(target, result, run=default_run):
    try:
        refresh(target, run=run)
    except Exception as error:
        if isinstance(result, dict):
            result['refresh_warning'] = 'Action completed; related data refresh deferred: ' + str(error)
    return result


STATUS_DIR = __import__('pathlib').Path('/run/mykos-startup-status')


def notify(shop, kind, state):
    """Publish only a lifecycle state; the filename routes it to its UI row."""
    import stat,tempfile
    from pathlib import Path
    if not re.fullmatch(r'[0-9]{8}',shop) or kind not in ('container','vpn') or state not in ('up','down','deleted'):
        raise ValueError('Invalid lifecycle signal')
    if os.geteuid()!=0:raise PermissionError('Lifecycle signals require root')
    try:
        STATUS_DIR.mkdir(mode=0o755)
        STATUS_DIR.chmod(0o755)
    except FileExistsError:pass
    st=STATUS_DIR.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:raise ValueError('Unsafe lifecycle signal directory')
    fd,temp=tempfile.mkstemp(prefix='.signal-',dir=str(STATUS_DIR))
    try:
        with os.fdopen(fd,'w') as handle:
            os.fchmod(handle.fileno(),0o644);handle.write(state);handle.flush()
        os.replace(temp,STATUS_DIR/(kind+'.'+shop))
    finally:
        if os.path.exists(temp):os.unlink(temp)


def vpn_up(shop, run=default_run, timeout=90):
    """Signal a real established tunnel, without collecting inventory/details."""
    import json,time
    import mykos_vm as vm
    import mykos_host_monitoring as host
    from mykos_runtime import PODMAN,target,shopcode
    shopcode(shop)
    folder=vm.POS/('.'+shop)
    if folder.is_symlink() or not any(p.is_file() and not p.is_symlink() for p in folder.glob('*.conn.conf')):return False
    def inspect():return json.loads(run([PODMAN,'inspect',target(shop)],timeout=15))[0]
    info=inspect();identity=(info['Id'],info['State'].get('Pid'));deadline=time.monotonic()+timeout
    while True:
        if not info['State'].get('Running') or (info['Id'],info['State'].get('Pid'))!=identity:return False
        if host.query_vpn(info).get('status')=='up':notify(shop,'vpn','up');return True
        if time.monotonic()>=deadline:return False
        time.sleep(2);info=inspect()


def lifecycle(shop, operation, run=default_run):
    """Called once the actual lifecycle operation succeeds; never probe stats."""
    if operation=='delete':
        notify(shop,'container','deleted');notify(shop,'vpn','deleted')
    elif operation=='stop':
        notify(shop,'container','down')
        import mykos_vm as vm
        folder=vm.POS/('.'+shop)
        if not folder.is_symlink() and any(p.is_file() and not p.is_symlink() for p in folder.glob('*.conn.conf')):notify(shop,'vpn','down')
    elif operation=='stop-vpn':notify(shop,'vpn','down')
    elif operation=='start-vpn':vpn_up(shop,run=run)
    elif operation in ('start','restore','stage-start','stage',None):
        notify(shop,'container','up');vpn_up(shop,run=run)
    else:return False
    return True


def vpn_current(shop,run=default_run):
    import json
    import mykos_vm as vm,mykos_host_monitoring as host
    from mykos_runtime import PODMAN,target,shopcode
    shopcode(shop)
    folder=vm.POS/('.'+shop)
    if not any(folder.glob('*.conn.conf')):notify(shop,'vpn','deleted');return
    info=json.loads(run([PODMAN,'inspect',target(shop)],timeout=15))[0]
    status=host.query_vpn(info).get('status')
    if status in ('up','down'):notify(shop,'vpn',status)
