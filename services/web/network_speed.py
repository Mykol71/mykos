"""Bounded Ookla measurements; readers use cache and never start network tests."""
import fcntl
import grp
import json
import math
import os
import pwd
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from mykos_config import BASE

OUTPUT = BASE / 'data/network-speed.json'
LOCK = Path('/run/lock/mykos-network-speed.lock')
BINARY = Path('/usr/bin/speedtest')
INTERVAL = 60
TIMEOUT = 45
STALE_AFTER = 180


def read_cache(path=None):
    path = OUTPUT if path is None else path
    try:
        if path.is_symlink() or path.stat().st_size > 32768: return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) and value.get('schema') == 1 else {}
    except (OSError, ValueError): return {}


def number(value):
    try: return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError: return False


def public(value=None, now=None):
    value = read_cache() if value is None else value
    now = time.time() if now is None else now
    result = {'status': 'unavailable', 'interval_seconds': INTERVAL, 'stale': True,
              'download_mbps': None, 'upload_mbps': None, 'measured_at': None,
              'age_seconds': None}
    if not isinstance(value, dict): return result
    if value.get('status') in ('ok', 'running', 'error', 'unavailable'):
        result['status'] = value['status']
    if value.get('error') in ('not_installed', 'unsupported_client', 'timeout', 'command_failed', 'invalid_result', 'interrupted'):
        result['error'] = value['error']
    stamp = value.get('last_attempt_at')
    if number(stamp) and stamp <= now + 1: result['last_attempt_at'] = stamp
    sample = value.get('last_success')
    if (isinstance(sample, dict) and number(sample.get('measured_at'))
            and sample['measured_at'] <= now + 1
            and all(number(sample.get(key)) for key in ('download_mbps', 'upload_mbps'))):
        age = max(0, int(now - sample['measured_at']))
        result.update({key: sample[key] for key in ('measured_at', 'download_mbps', 'upload_mbps')})
        result.update(age_seconds=age, stale=age > STALE_AFTER or result['status'] in ('error', 'unavailable'))
        if number(sample.get('latency_ms')): result['latency_ms'] = sample['latency_ms']
    return result


def publish(value):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.network-speed-', dir=str(OUTPUT.parent))
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, allow_nan=False); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, 0o640)
        if os.geteuid() == 0: os.chown(name, 0, grp.getgrnam('mykosweb').gr_gid)
        os.replace(name, OUTPUT)
    finally:
        if os.path.exists(name): os.unlink(name)


def execution_options():
    environment = dict(os.environ, LC_ALL='C')
    options = {}
    user = os.environ.get('MYKOS_SPEEDTEST_USER')
    if user:
        try: account = pwd.getpwnam(user)
        except KeyError: raise RuntimeError('unsupported_client')
        if os.geteuid() not in (0, account.pw_uid): raise RuntimeError('unsupported_client')
        home = os.environ.get('MYKOS_SPEEDTEST_HOME') or account.pw_dir
        if not Path(home).is_absolute(): raise RuntimeError('unsupported_client')
        environment.update(HOME=home, USER=user, LOGNAME=user)
        options['cwd'] = home
        if os.geteuid() == 0:
            options.update(user=account.pw_uid, group=account.pw_gid,
                           extra_groups=os.getgrouplist(user, account.pw_gid))
    return environment, options


def execute(arguments, timeout):
    environment, options = execution_options()
    child = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True,
                             env=environment, **options)
    try:
        output, _ = child.communicate(timeout=timeout)
    except BaseException:
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        child.communicate()
        raise
    if child.returncode: raise RuntimeError('command_failed')
    if len(output) > 65536: raise ValueError('invalid_result')
    return output


def collect(now=None):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return {'state': 'already_running'}
        stamp = int(time.time() if now is None else now)
        previous = read_cache()
        attempted = previous.get('last_attempt_at')
        if number(attempted) and 0 <= stamp - attempted < INTERVAL:
            return {'state': 'not_due'}
        value = {'schema': 1, 'status': 'running', 'last_attempt_at': stamp}
        cached = public(previous, stamp)
        if cached['measured_at'] is not None:
            value['last_success'] = {key: cached[key] for key in ('measured_at', 'download_mbps', 'upload_mbps', 'latency_ms') if key in cached}
        publish(value)
        try:
            path = BINARY.resolve(strict=True)
            info = path.stat()
            for parent in path.parents:
                status = parent.stat()
                if status.st_uid != 0 or status.st_mode & 0o022: raise RuntimeError('unsupported_client')
            if not path.is_file() or info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError('unsupported_client')
            version = execute([str(path), '--version'], 5).decode('utf-8', 'replace')
            if not version.startswith('Speedtest by Ookla '): raise RuntimeError('unsupported_client')
            raw = json.loads(execute([str(path), '--format=json', '--progress=no'], TIMEOUT))
            if not isinstance(raw, dict) or raw.get('type') != 'result': raise ValueError('invalid_result')
            download = raw.get('download', {}).get('bandwidth')
            upload = raw.get('upload', {}).get('bandwidth')
            latency = raw.get('ping', {}).get('latency')
            if not all(number(n) for n in (download, upload)): raise ValueError('invalid_result')
            # Ookla machine-readable bandwidth is bytes/second, decimal Mbps here.
            sample = {'download_mbps': round(download * 8 / 1000000, 3),
                      'upload_mbps': round(upload * 8 / 1000000, 3),
                      'measured_at': int(time.time() if now is None else now)}
            if not all(number(sample[key]) for key in ('download_mbps', 'upload_mbps')): raise ValueError('invalid_result')
            if number(latency): sample['latency_ms'] = latency
            value.update(status='ok', last_success=sample)
        except FileNotFoundError:
            value.update(status='unavailable', error='not_installed')
        except subprocess.TimeoutExpired:
            value.update(status='error', error='timeout')
        except (KeyboardInterrupt, InterruptedError):
            value.update(status='error', error='interrupted'); publish(value); raise
        except (OSError, RuntimeError) as error:
            value.update(status='error', error=str(error) if str(error) in ('unsupported_client', 'command_failed') else 'command_failed')
        except (ValueError, TypeError, AttributeError, OverflowError):
            value.update(status='error', error='invalid_result')
        publish(value)
        return {'state': value['status']}
