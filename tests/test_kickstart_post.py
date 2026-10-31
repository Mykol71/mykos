"""Run the RH7 post section against a temporary root with mocked network access."""
import os
from pathlib import Path
import re
import subprocess
import sys
import pytest

KS = Path(__file__).resolve().parents[1]/'pos/ks/rh7.ks'

@pytest.mark.parametrize('download,plugin', [('ok',False),('ok',True),('fail',False),('invalid',False)])
def test_rh7_post_handles_optional_files_and_download_failure(tmp_path,download,plugin):
    root=tmp_path/'root'
    for directory in ('usr/bin','etc','var/run'):(root/directory).mkdir(parents=True,exist_ok=True)
    destination=root/'usr/bin/updateos';destination.write_text('# old script sentinel\n')
    if plugin:
        conf=root/'etc/yum/pluginconf.d/subscription-manager.conf'
        conf.parent.mkdir(parents=True);conf.write_text('[main]\nenabled=1\n')
    fakebin=tmp_path/'bin';fakebin.mkdir()
    (fakebin/'systemd-tmpfiles').write_text('#!/bin/sh\nexit 0\n')
    (fakebin/'systemctl').write_text('#!/bin/sh\ncase "$1" in mask) exit 0;; enable) [ "$2" = sshd ] || exit 90;; *) exit 90;; esac\nexit 0\n')
    # The image uses GNU sed; macOS sed has different -i argument handling.
    (fakebin/'sed').write_text('#!'+sys.executable+'''\nimport sys
from pathlib import Path
assert sys.argv[1:3] == ['-i','s/enabled=1/enabled=0/g']
file=Path(sys.argv[3]);file.write_text(file.read_text().replace('enabled=1','enabled=0'))
''')
    (fakebin/'curl').write_text('''#!/usr/bin/env bash
set -eu
output=; urls=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    --retry|--connect-timeout|--max-time) shift 2 ;;
    --fail|--show-error|--location) shift ;;
    https://mykol.com/updateos/updateos) urls=$((urls+1)); shift ;;
    *) exit 90 ;;
  esac
done
[ "$urls" = 1 ] && [ -n "$output" ] || exit 91
case "$DOWNLOAD" in
  fail) exit 22 ;;
  invalid) printf '<html>error</html>\\n' > "$output" ;;
  ok) printf '#!/bin/bash\\nexit 0\\n' > "$output" ;;
esac
''')
    for file in fakebin.iterdir():file.chmod(0o755)
    post=KS.read_text().split('%post ',1)[1].split('\n',1)[1].split('%end',1)[0]
    # Rebase absolute write paths in one pass; cleanup stays inside the fixture.
    post=re.sub(r'/(?:etc|var|usr|boot)(?=/|\b)',lambda m:str(root)+m[0],post)
    result=subprocess.run(['bash','-c',post],text=True,capture_output=True,
                          env={**os.environ,'PATH':str(fakebin)+':'+os.environ['PATH'],'DOWNLOAD':download})
    if download=='ok':assert result.returncode==0,result.stderr
    assert (root/'etc/yum/vars/infra').read_text()=='container\n'
    assert not (root/'etc/dnf').exists()
    assert not list((root/'usr/bin').glob('.updateos.*'))
    if download=='ok':
        assert result.returncode==0,result.stderr
        assert destination.read_text()=='#!/bin/bash\nexit 0\n'
        assert destination.stat().st_mode & 0o777==0o755
    else:
        assert result.returncode!=0
        assert destination.read_text()=='# old script sentinel\n'
    if plugin:assert conf.read_text()=='[main]\nenabled=0\n'
