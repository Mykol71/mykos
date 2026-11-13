#!/usr/bin/env python3
"""Synchronize signed RHEL mirrors privately and publish complete repository trees."""
import argparse
import bz2
import ctypes
import errno
import fcntl
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path('/var/www/html')
LOCK = Path('/run/lock/mykos-repo-sync.lock')
SLICE = 'mykosreposync.slice'
LABEL = 'io.mykos.repo-sync'
IMAGES = {
    6: 'registry.access.redhat.com/ubi7/ubi:7.9-1445',
    7: 'registry.access.redhat.com/ubi7/ubi:7.9-1445',
    8: 'registry.access.redhat.com/ubi8/ubi:8.10-1786322297',
    9: 'registry.access.redhat.com/ubi9/ubi:9.8-1786631889',
    10: 'registry.access.redhat.com/ubi10/ubi:1791894362',
}
NS = {'r': 'http://linux.duke.edu/metadata/repo', 'p': 'http://linux.duke.edu/metadata/common'}
MIN_FREE = 5 * 1024 ** 3

import importlib.util
_cache_spec=importlib.util.spec_from_file_location('mykos_retry_cache',Path(__file__).with_name('retry_cache.py'))
CACHE=importlib.util.module_from_spec(_cache_spec);_cache_spec.loader.exec_module(CACHE)


def run(args, **kwargs):
    command=[str(x) for x in args]
    if command[:2]!=['podman','run'] or '--cidfile' not in command:return subprocess.run(command,check=True,**kwargs)
    import time
    root=Path(command[command.index('--cidfile')+1]).parent.parent
    process=subprocess.Popen(command,**kwargs)
    try:
        while process.poll() is None:
            if shutil.disk_usage(root).free<MIN_FREE:raise RuntimeError('Sync stopped to preserve5 GiB of application disk headroom; recoverable candidates remain private')
            time.sleep(2)
        if process.returncode:raise subprocess.CalledProcessError(process.returncode,command)
        return subprocess.CompletedProcess(command,0)
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)


def legacy_catalog():
    return json.loads((Path(__file__).resolve().parents[2] / 'pos/legacy_el.json').read_text())

def repositories(major):
    if isinstance(major,str):return [r['name'] for r in legacy_catalog()[major]['repositories']]
    if major in (6,7):
        return ['rhel-%s-server' % major + part + '-rpms' for part in ('', '-optional', '-extras')]
    return ['rhel-%s-for-x86_64-%s-rpms' % (major, part) for part in ('baseos', 'appstream')]


def relative_file(root, href):
    url = urlsplit(href)
    name = unquote(url.path)
    parts = PurePosixPath(name).parts
    if url.scheme or url.netloc or url.query or url.fragment or not parts or name.startswith('/') or '..' in parts:
        raise ValueError('Unsafe repository location: ' + href)
    path = root.joinpath(*parts)
    for parent in (path, *path.parents):
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise ValueError('Repository location uses a symlink: ' + href)
    if not path.is_file():
        raise ValueError('Referenced file is missing: ' + href)
    return path


def digest(path, algorithm):
    algorithm = 'sha1' if algorithm == 'sha' else algorithm
    if algorithm not in ('sha1', 'sha256', 'sha384', 'sha512'):
        raise ValueError('Unsupported repository checksum: ' + str(algorithm))
    value = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def checked_file(root, href, checksum, size=None):
    path = relative_file(root, href)
    if checksum is None or not checksum.text or digest(path, checksum.get('type')) != checksum.text.strip():
        raise ValueError('Checksum mismatch: ' + href)
    if size is not None and path.stat().st_size != int(size):
        raise ValueError('Size mismatch: ' + href)
    return path


def validate_repository(root):
    repomd = relative_file(root, 'repodata/repomd.xml')
    if repomd.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Repository manifest is too large')
    manifest = ET.parse(repomd).getroot()
    primary = None
    for item in manifest.findall('r:data', NS):
        location = item.find('r:location', NS)
        if location is None:
            raise ValueError('Metadata location is missing')
        path = checked_file(root, location.get('href', ''), item.find('r:checksum', NS), item.findtext('r:size', namespaces=NS))
        if item.get('type') == 'primary':
            primary = path
    if primary is None:
        raise ValueError('Primary repository metadata is missing')
    opener = {'.gz': gzip.open, '.bz2': bz2.open, '.xz': lzma.open, '.xml': open}.get(primary.suffix)
    if opener is None:
        raise ValueError('Unsupported primary metadata compression: ' + primary.name)
    count = 0
    with opener(primary, 'rb') as stream:
        for _, item in ET.iterparse(stream, events=('end',)):
            if item.tag != '{%s}package' % NS['p']:
                continue
            location = item.find('p:location', NS)
            size = item.find('p:size', NS)
            if location is None or size is None:
                raise ValueError('Package location or size is missing')
            checked_file(root, location.get('href', ''), item.find('p:checksum', NS), size.get('package'))
            count += 1
            item.clear()
    return count


def clone_repository(live, staged):
    if not live.exists():
        staged.mkdir(mode=0o755)
        return
    if not live.is_dir() or live.is_symlink():
        raise ValueError('Repository root must be a real directory: ' + str(live))
    # Require COW: never hard-link writable RPMs or silently duplicate a huge mirror.
    run(['cp', '-a', '--reflink=always', '--', live, staged])


def preserve_previous(live, staged):
    """Keep immutable URLs needed by clients that already fetched old repomd.xml."""
    if staged.is_symlink():
        raise ValueError('Staged repository is a symlink')
    if not live.exists():
        return
    for base, dirs, files in os.walk(live, followlinks=False):
        if any((Path(base) / name).is_symlink() for name in dirs):
            raise ValueError('Repository contains a directory symlink')
        for name in files:
            old = Path(base) / name
            rel = old.relative_to(live)
            if old.is_symlink():
                raise ValueError('Repository contains a file symlink')
            if rel == Path('repodata/repomd.xml'):
                continue
            new = staged / rel
            if new.exists() or new.is_symlink():
                relative_file(staged, str(rel))
                # File attributes are not content identity, even for a COW clone.
                if digest(old, 'sha256') != digest(new, 'sha256'):
                    raise ValueError('Refusing to replace an existing immutable URL: ' + str(rel))
            else:
                new.parent.mkdir(parents=True, exist_ok=True)
                run(['cp', '-a', '--reflink=always', '--', old, new])


def exchange(staged, live):
    """Linux atomic directory exchange; never fall back to a two-rename gap."""
    if not live.exists():
        os.rename(staged, live)
    else:
        libc = ctypes.CDLL(None, use_errno=True)
        function = getattr(libc, 'renameat2', None)
        if function is None:
            raise OSError(errno.ENOSYS, 'Atomic repository exchange is unavailable')
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        if function(-100, os.fsencode(staged), -100, os.fsencode(live), 2):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
    fd = os.open(live.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def worker_command(major, work, run_id):
    if isinstance(major,str):
        return ['podman','run','--rm','--name','mykos-repo-sync-rhel'+major,
                '--cidfile',str(work/'worker.cid'),'--label',LABEL+'='+run_id,
                '--cgroup-parent',SLICE,'--cpus=1','--memory=2g','--memory-swap=2g',
                '--pids-limit=256','--oom-score-adj=500','--cpu-shares=128',
                '--cgroup-conf=io.weight=default 10','--security-opt','label=disable',
                '--volume',str(work/'input')+':/input:ro','--volume',str(work/'stage')+':/stage:rw',
                IMAGES[7],'/usr/bin/python','/input/legacy_worker.py',major,'sync']
    credentials = []
    for source, target in [('/etc/pki/entitlement', '/run/mykos-entitlement'), ('/etc/rhsm/ca', '/run/mykos-rhsm-ca')]:
        if Path(source).is_dir():
            credentials += ['--volume', source + ':' + target + ':ro']
    return ['podman', 'run', '--rm', '--name', 'mykos-repo-sync-rhel%s' % major,
            '--cidfile', str(work / 'worker.cid'), '--label', LABEL + '=' + run_id,
            '--cgroup-parent', SLICE, '--cpus=1', '--memory=2g', '--memory-swap=2g',
            '--pids-limit=256', '--oom-score-adj=500', '--cpu-shares=128', '--cgroup-conf=io.weight=default 10',
            '--security-opt', 'label=disable', '--volume', str(work / 'stage') + ':/stage:rw',
            '--volume', str(Path(__file__).with_name('sync_worker.sh')) + ':/run/mykos-sync-worker:ro',
            '--volume',str(Path(__file__).with_name('verify_cached.py'))+':/run/mykos-verify-cached:ro',
            *credentials, IMAGES[major], '/bin/bash', '/run/mykos-sync-worker', str(major)]


def cleanup_worker(work, run_id):
    cidfile = work / 'worker.cid'
    if cidfile.is_symlink():
        raise ValueError('Owned worker record is a symlink')
    fallback = not cidfile.exists()
    if fallback:
        owner = work / 'owner.json'
        name = json.loads(owner.read_text()).get('worker_name') if owner.is_file() else None
        if name not in ['mykos-repo-sync-rhel%s' % major for major in [*IMAGES,*legacy_catalog()]]:
            return
        candidate = name
    else:
        candidate = cidfile.read_text().strip()
        if len(candidate) != 64 or any(c not in '0123456789abcdef' for c in candidate):
            raise ValueError('Invalid owned worker ID; preserving scratch for inspection')
    result = subprocess.run(['podman', 'container', 'exists', candidate], check=False)
    if result.returncode == 1:
        return
    if result.returncode:
        raise RuntimeError('Cannot verify owned worker; preserving scratch')
    value = json.loads(subprocess.check_output(['podman', 'inspect', candidate], text=True))[0]
    if fallback and value.get('Config', {}).get('Labels', {}).get(LABEL) != run_id:
        return  # A name collision belongs to someone else; never stop it.
    cid = value.get('Id') if fallback else candidate
    if not cid or len(cid) != 64 or any(c not in '0123456789abcdef' for c in cid):
        raise ValueError('Invalid inspected worker ID')
    if value.get('Config', {}).get('Labels', {}).get(LABEL) != run_id:
        raise ValueError('Worker ownership mismatch; preserving scratch')
    run(['podman', 'stop', '--time', '15', cid], timeout=30)
    # --rm may already have removed it. Never force-remove a conflicting worker.
    if subprocess.run(['podman', 'container', 'exists', cid], check=False).returncode == 0:
        run(['podman', 'rm', cid], timeout=30)


def sync_major(major, root, runner=run, clone=clone_repository, publish=exchange, cleanup=cleanup_worker):
    run_id=uuid.uuid4().hex
    work=Path(tempfile.mkdtemp(prefix='.mykos-sync-',dir=root));work.chmod(0o700)
    success=False;publishing=False
    try:
        restored=CACHE.take(root,major,work,cleanup)
        if restored:
            inputs=work/'input'
            if inputs.exists():shutil.rmtree(inputs)
        else:(work/'stage').mkdir()
        CACHE.write_owner(work,{'format':1,'run_id':run_id,'worker_name':'mykos-repo-sync-rhel%s' % major,'selection':str(major),'job_id':os.environ.get('MYKOS_JOB_ID')})
        if isinstance(major,str):
            source=Path(__file__).resolve().parents[2]/'pos';inputs=work/'input';inputs.mkdir()
            for name in ('legacy_el.json','legacy_worker.py'):shutil.copy2(source/name,inputs/name)
            shutil.copy2(Path(__file__).with_name('verify_cached.py'),inputs/'verify_cached.py')
            shutil.copytree(source/'legacy-keys',inputs/'legacy-keys')
            shutil.copy2('/etc/pki/tls/certs/ca-bundle.crt',inputs/'build-ca.crt')
        if shutil.disk_usage(root).free<MIN_FREE:raise RuntimeError('Repository sync needs at least5 GiB free headroom')
        for name in repositories(major):
            if not (work/'stage'/name).exists():clone(root/name,work/'stage'/name)
        print('MYKOS_JOB_PHASE downloading',flush=True)
        runner(worker_command(major,work,run_id))
        print('MYKOS_JOB_PHASE validating',flush=True)
        validated=[]
        for name in repositories(major):
            live,staged=root/name,work/'stage'/name
            preserve_previous(live,staged)
            count=validate_repository(staged);validated.append((name,count))
        # A failed cache can only contain private pre-publication candidates.
        # Once exchanges start, stages contain old live directories; do not
        # adopt those as failed-download cache after a publication interruption.
        publishing=True
        value=CACHE.owner(work);value['phase']='publishing';CACHE.write_owner(work,value)
        print('MYKOS_JOB_PHASE publishing',flush=True)
        for name,count in validated:
            staged,live=work/'stage'/name,root/name
            runner(['restorecon','-RF',staged]);publish(staged,live)
            print('Published %s: %s referenced signed packages verified' % (name,count),flush=True)
        success=True
    finally:
        # Ownership/stop failure retains the original private work for review.
        cleanup(work,run_id)
        if not success and not publishing and (work/'owner.json').is_file():
            CACHE.keep(root,major,work,os.environ.get('MYKOS_JOB_ID'))
        if work.exists():shutil.rmtree(work)


def recover_stale(root):
    for work in sorted(root.glob('.mykos-sync-*')):
        if work.is_symlink() or not work.is_dir() or work.stat().st_uid != 0 or work.stat().st_mode & 0o077:
            raise ValueError('Untrusted sync scratch entry: ' + str(work))
        owner = work / 'owner.json'
        if not owner.is_file() or owner.is_symlink() or owner.stat().st_uid != 0 or owner.stat().st_mode & 0o022:
            raise ValueError('Unrecognized sync scratch; inspect manually: ' + str(work))
        value = json.loads(owner.read_text())
        run_id = value.get('run_id', '')
        if value.get('format') != 1 or len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
            raise ValueError('Invalid stale sync ownership record')
        cleanup_worker(work, run_id)
        selected=value.get('selection') or value.get('worker_name','').removeprefix('mykos-repo-sync-rhel')
        if selected in CACHE.ALLOWED and value.get('phase')!='publishing':CACHE.keep(root,selected,work,value.get('job_id'))
        if work.exists():shutil.rmtree(work)


def interrupted(signum, frame):
    raise InterruptedError('Sync interrupted')


def main(argv=None, track=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', choices=[*map(str,sorted(IMAGES)),*sorted(k for k in legacy_catalog() if not k.startswith('rh'))], nargs='?', help='Default: synchronize RHEL 7 through 10')
    args = parser.parse_args(argv)
    if os.geteuid() != 0 or sys.platform != 'linux':
        parser.error('Run as root on a compatible Linux repository host')
    if not Path('/sys/fs/cgroup/cgroup.controllers').exists():
        parser.error('Repository sync requires cgroup v2 resource controls')
    if track and os.environ.get('MYKOS_JOB_SYNC_MANAGED')!='1':
        helpers=Path(__file__).resolve().parents[2]/'services/helpers'
        sys.path.insert(0,str(helpers) if (helpers/'mykos_jobs.py').is_file() else '/usr/local/sbin')
        import mykos_jobs
        return mykos_jobs.worker('repository-sync',args.release or 'all')
    os.setpriority(os.PRIO_PROCESS, 0, 15)
    run(['ionice', '-c', '2', '-n', '7', '-p', str(os.getpid())])
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
    with os.fdopen(os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644), 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A repository synchronization is already running')
        root = ROOT.resolve(strict=True)
        recover_stale(root)
        for major in [int(args.release) if args.release.isdigit() else args.release] if args.release else [7,8,9,10]:
            sync_major(major, root)
    print('Repository synchronization complete.', flush=True)


if __name__ == '__main__':
    try:
        sys.exit(main(track=True) or 0)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Repository synchronization failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
