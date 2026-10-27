"""Serialize targeted and routine cache updates without guest calls."""
import fcntl

def collect_locked(path, callback):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_name(path.name+'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return callback()
