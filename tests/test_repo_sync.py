"""Exercise repository sync selection without network or real containers."""
from pathlib import Path
import os
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_mocked(tmp_path, args):
    tools = tmp_path / 'bin'
    tools.mkdir()
    events = tmp_path / 'events'
    for name in ('yum', 'dnf', 'createrepo', 'reposync'):
        tool = tools / name
        tool.write_text('#!/bin/bash\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$EVENTS"\n')
        tool.chmod(0o755)
    (tools / 'flock').write_text('#!/bin/bash\nexit 0\n')
    (tools / 'flock').chmod(0o755)
    (tools / 'podman').write_text('''#!/bin/bash
printf 'worker %s\n' "${@: -1}" >> "$EVENTS"
exec bash -s -- "${@: -1}"
''')
    (tools / 'podman').chmod(0o755)
    source = (ROOT / 'repo/bin/sync_repos').read_text()
    source = source.replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                            'export PATH="$TEST_PATH:$PATH"')
    source = source.replace('/run/lock/mykos-repo-sync.lock', str(tmp_path / 'sync.lock'))
    result = subprocess.run(['bash', '-s', '--', *args], input=source,
                            text=True, capture_output=True,
                            env={**os.environ, 'TEST_PATH': str(tools), 'EVENTS': str(events)})
    return result, events.read_text().splitlines() if events.exists() else []


@pytest.mark.parametrize('args,expected', [([], [7, 8, 9, 10]),
                                         (['7'], [7]), (['8'], [8]),
                                         (['9'], [9]), (['10'], [10])])
def test_repository_sync_workers(tmp_path, args, expected):
    result, lines = run_mocked(tmp_path, args)
    assert result.returncode == 0, result.stderr
    assert [int(line.split()[1]) for line in lines if line.startswith('worker ')] == expected
    for major in expected:
        if major == 7:
            for component in ('', '-optional', '-extras'):
                assert any(line.startswith('reposync ') and
                           f'--repo=rhel-{major}-server{component}-rpms' in line and
                           '--download-metadata' in line for line in lines)
        else:
            for component in ('baseos', 'appstream'):
                assert any(line.startswith('dnf reposync ') and
                           f'--repo=rhel-{major}-for-x86_64-{component}-rpms' in line
                           for line in lines)
    assert 'Repository synchronization complete.' in result.stdout


@pytest.mark.parametrize('args', [['6'], ['11'], ['7', '8']])
def test_invalid_or_removed_release_starts_no_workers(tmp_path, args):
    result, lines = run_mocked(tmp_path, args)
    assert result.returncode == 2
    assert 'Usage: sync_repos [7|8|9|10]' in result.stderr
    assert not lines
    assert 'complete' not in result.stdout


def test_failed_worker_stops_sync_before_later_releases(tmp_path):
    source = (ROOT / 'repo/bin/sync_repos').read_text()
    source = source.replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                            'flock() { :; }; podman() { echo "failed worker ${@: -1}"; return 37; }')
    source = source.replace('/run/lock/mykos-repo-sync.lock', str(tmp_path / 'sync.lock'))
    result = subprocess.run(['bash', '-s'], input=source, text=True, capture_output=True)
    assert result.returncode == 37
    assert result.stdout == 'failed worker 7\n'
