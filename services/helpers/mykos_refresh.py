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
