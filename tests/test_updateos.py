from pathlib import Path
import hashlib,json,os,subprocess,sys,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"services/helpers"))
SOURCE=Path(__file__).resolve().parents[1]/'bin/updateos'

@pytest.mark.parametrize('case',['ok','empty','unavailable','strict-failure'])
def test_styled_stage_listing_executes_only_real_scripts(tmp_path,case):
    cache=tmp_path/'cache';cache.mkdir();fake=tmp_path/'curl';calls=tmp_path/'calls';marker=tmp_path/'ran';release=tmp_path/'release';release.write_text('Red Hat Enterprise Linux release 7.9 (Maipo)\n')
    body='#!/bin/bash\n'+('false\n' if case=='strict-failure' else '')+'printf stage-ok >> '+str(marker)+'\n'; digest=hashlib.md5(body.encode()).hexdigest()
    top='<html><head><link rel="stylesheet" href="/.mykos-index.css"></head><body><a href="stage/">stage/</a></body></html>'
    stage='<link rel="stylesheet" href="/.mykos-index.css" type="text/css">\n<a href="/updateos/rh7/">Parent</a>\n<a href="?C=N">Name</a>\n<a href="13_sethostname">script</a>\n<a href="README.md">README.md</a>\n'
    data={'https://test.invalid/updateos/rh7/':top,'https://test.invalid/updateos/rh7/stage/':stage,'https://test.invalid/updateos/rh7/stage/13_sethostname':body,'https://test.invalid/updateos/rh7/stage/13_sethostname.md5':digest}
    if case=='empty':data['https://test.invalid/updateos/rh7/stage/']='<html>No files</html>'
    if case=='unavailable':del data['https://test.invalid/updateos/rh7/stage/']
    fake.write_text('#!/usr/bin/env python3\nimport sys,json,pathlib\nd='+repr(data)+'\na=sys.argv[1:];u=next(x for x in a if x.startswith("https://"));pathlib.Path('+repr(str(calls))+').open("a").write(u+"\\n")\nif u not in d:sys.exit(22)\nb=d[u]\nif "-o" in a:pathlib.Path(a[a.index("-o")+1]).write_text(b)\nelse:print(b,end="")\n');fake.chmod(0o755)
    (tmp_path/'nc').write_text('#!/bin/sh\nexit 0\n');(tmp_path/'nc').chmod(0o755)
    text=SOURCE.read_text().replace('ID=$(/usr/bin/id -u)','ID=0').replace('/usr/bin/.updateos',str(cache)).replace('/usr/bin/curl',str(fake)).replace('`curl -s','`'+str(fake)+' -s').replace('/etc/redhat-release',str(release)).replace('LOG_FILE="/tmp/updateos.log"','LOG_FILE="'+str(tmp_path/'log')+'"')
    runner=tmp_path/'updateos';runner.write_text(text)
    r=subprocess.run(['bash',str(runner),'stage'],capture_output=True,text=True,env={**os.environ,'PATH':str(tmp_path)+':'+os.environ['PATH'],'MYKOS_UPDATEOS_BACKEND':'https://test.invalid/updateos','MYKOS_UPDATEOS_STRICT':'1'},timeout=10)
    if case=='ok':
        assert r.returncode==0,(r.stdout,r.stderr)
        assert marker.read_text()=='stage-ok'
    else:
        assert r.returncode!=0,(r.stdout,r.stderr)
        assert not marker.exists()
    assert 'stylesheet' not in calls.read_text() and '.mykos-index.css' not in calls.read_text()
    assert not list(cache.glob('.*'))

def functions():
    text=SOURCE.read_text();start=text.index('download_script () {');end=text.index('\n}\n',start)+3
    return text[start:end]

def test_failed_download_preserves_cache_and_never_publishes_html(tmp_path):
    cache=tmp_path/'cache';cache.mkdir();script=cache/'13_sethostname';script.write_text('old-good');fake=tmp_path/'curl';mode=tmp_path/'mode';mode.write_text('404')
    fake.write_text('#!/usr/bin/env python3\nimport sys,pathlib\na=sys.argv[1:]\nif a[-1].endswith(".md5"):sys.exit(22)\nmode=pathlib.Path('+repr(str(mode))+').read_text()\nif "-o" in a:pathlib.Path(a[a.index("-o")+1]).write_text("<!DOCTYPE html><html>Not Found</html>")\nsys.exit(22 if mode=="404" else 0)\n');fake.chmod(0o755)
    code=functions().replace('/usr/bin/.updateos',str(cache)).replace('/usr/bin/curl',str(fake))
    harness='NETUP=Y;BACKEND=https://test.invalid;GROUP=rh7;SUBGROUP=stage;SCRIPT=13_sethostname;log(){ :; };\n'+code+'\ndownload_script\n'
    for value in ('404','html'):
        mode.write_text(value);r=subprocess.run(['bash','-c',harness],capture_output=True,text=True,timeout=10);assert r.returncode!=0;assert script.read_text()=='old-good';assert not list(cache.glob('.*'))

def test_rejects_script_path_traversal(tmp_path):
    code='NETUP=Y;SCRIPT=../escape;SUBGROUP=stage;log(){ :; };\n'+functions()+'\ndownload_script\n'
    assert subprocess.run(['bash','-c',code],capture_output=True).returncode==2
