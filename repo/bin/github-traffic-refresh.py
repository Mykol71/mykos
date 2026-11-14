#!/usr/bin/env python3
"""Refresh aggregate GitHub traffic over an existing SSH connection; no tokens leave this Mac."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time


def fetch(repository, gh='gh'):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):raise ValueError('Invalid repository')
    value={'repository':repository}
    for key,endpoint in [('views','views?per=day'),('clones','clones?per=day'),('referrers','popular/referrers'),('paths','popular/paths')]:
        r=subprocess.run([gh,'api','-H','Accept: application/vnd.github+json','repos/'+repository+'/traffic/'+endpoint],capture_output=True,timeout=30)
        if r.returncode:raise RuntimeError('GitHub traffic request failed: '+key)
        if len(r.stdout)>256*1024:raise ValueError('GitHub traffic response too large')
        raw=json.loads(r.stdout)
        if key in ('views','clones'):
            value[key]={'count':raw['count'],'uniques':raw['uniques'],key:[{k:x[k] for k in ('timestamp','count','uniques')} for x in raw[key]]}
        else:
            label='referrer' if key=='referrers' else 'path';value[key]=[{k:x[k] for k in (label,'count','uniques')} for x in raw]
    value['updated_at']=int(time.time());return value


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repository',required=True);p.add_argument('--host',required=True);p.add_argument('--control-path',required=True);p.add_argument('--known-hosts',required=True);p.add_argument('--gh',default='gh');p.add_argument('--state',type=Path,required=True);a=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+',a.host):raise ValueError('Invalid SSH target')
    os.umask(0o077);a.state.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (a.state/'refresh.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        try:
            value=fetch(a.repository,a.gh);raw=json.dumps(value,separators=(',',':')).encode();assert len(raw)<=256*1024
            # No credential copying or password prompts. Atomically replace only the aggregate inbox.
            command="set -eu; umask 077; d=/var/lib/mykos-github-traffic-inbox; t=$(mktemp \"$d/.incoming.XXXXXXXX\"); trap 'rm -f -- \"$t\"' EXIT; cat > \"$t\"; chmod 600 \"$t\"; mv -f -- \"$t\" \"$d/traffic.json\""
            r=subprocess.run(['/usr/bin/ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ControlPath='+a.control_path,'-o','UserKnownHostsFile='+a.known_hosts,a.host,command],input=raw,capture_output=True,timeout=45)
            if r.returncode:raise RuntimeError('SSH aggregate delivery failed; existing traffic retained')
            status={'state':'ok','updated_at':value['updated_at'],'repository':a.repository}
        except Exception as e:
            status={'state':'failed','attempted_at':int(time.time()),'error':type(e).__name__+'; GitHub or SSH refresh unavailable; existing report data retained'}
        target=a.state/'status.json';temp=target.with_suffix('.tmp');temp.write_text(json.dumps(status)+'\n');temp.replace(target)
        if status['state']!='ok':raise SystemExit(1)

if __name__=='__main__':main()
