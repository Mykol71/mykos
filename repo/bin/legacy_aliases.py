#!/usr/bin/env python3
"""Render explicit LAN-only Apache mappings for the additional legacy mirrors.

Review/include this fragment before an existing catch-all /repos/ Alias.
Rendering never edits Apache configuration or changes the running server.
"""
import argparse,json,re
from pathlib import Path
LAN=('127.0.0.1','::1','192.168.222.0/24','192.168.22.0/24')
PATTERN=re.compile(r'(?:rhel-6-server(?:-optional|-extras)?-rpms|(?:centos|oracle|scientific|springdale)-[67]-x86_64-(?:os|updates|extras))\Z')

def repository_names(catalog=None):
 if catalog is None:catalog=json.loads((Path(__file__).resolve().parents[2]/'pos/legacy_el.json').read_text())
 names={'rhel-6-server'+part+'-rpms' for part in ('','-optional','-extras')}
 for spec in catalog.values():
  if spec['distribution']=='rhel':continue
  if spec['major'] not in (6,7):raise ValueError('Only legacy EL6/EL7 aliases are supported')
  names.update(r['name'] for r in spec['repositories'])
 if any(not PATTERN.fullmatch(name) for name in names):raise ValueError('Unsafe legacy repository name')
 return sorted(names)

def render(root='/home/mykos-repositories',catalog=None):
 if not re.fullmatch(r'/[A-Za-z0-9_./-]+',root) or '..' in Path(root).parts:raise ValueError('Use a simple absolute repository storage path')
 root=root.rstrip('/');lines=['# Generated legacy mirror aliases; every tree is explicitly LAN-only.']
 for name in repository_names(catalog):
  destination=root+'/'+name
  lines.extend(['Alias /'+name+'/ '+destination+'/',
                'Alias /repos/'+name+'/ '+destination+'/',
                '<Directory "'+destination+'">','    AuthMerging Off',
                '    Require ip '+' '.join(LAN),'</Directory>'])
 return '\n'.join(lines)+'\n'

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',default='/home/mykos-repositories');args=parser.parse_args()
 try:print(render(args.root),end='')
 except ValueError as error:parser.error(str(error))
