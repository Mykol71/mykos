"""Content-addressed dashboard resources. Never handles authenticated data."""
import gzip, hashlib, re
from functools import lru_cache
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import Response

@lru_cache(maxsize=64)
def compressed(body):
    return gzip.compress(body,compresslevel=6,mtime=0)

def encoded(body, media_type, request=None, immutable=False):
    headers={'Cache-Control':'public, max-age=31536000, immutable' if immutable else 'no-store', 'Vary':'Accept-Encoding'}
    # Honor explicit q=0; do not compress event streams or API responses.
    accepted={}
    for item in (request.headers.get('accept-encoding','') if request else '').lower().split(','):
        parts=item.strip().split(';'); quality=1.0
        for parameter in parts[1:]:
            if parameter.strip().startswith('q='):
                try:quality=float(parameter.strip()[2:])
                except ValueError:quality=0
        accepted[parts[0].strip()]=quality
    if accepted.get('gzip',accepted.get('*',0))>0:
        body=compressed(body);headers['Content-Encoding']='gzip'
    return Response(body,media_type=media_type,headers=headers)

@lru_cache(maxsize=4)
def _plan(root, signature):
    root=Path(root); html=(root/'index.html').read_text(); assets={}
    def add(body,extension):
        data=body.encode() if isinstance(body,str) else body
        name=hashlib.sha256(data).hexdigest()+'.'+extension
        assets[name]=(data,'text/css' if extension=='css' else 'application/javascript')
        return '/mykos/assets/'+name
    def script(match):
        attrs,body=match.groups()
        if 'src=' in attrs or len(body)<1000:return match[0]
        return '<script'+attrs+' src="'+add(body,'js')+'"></script>'
    html=re.sub(r'<script([^>]*)>(.*?)</script>',script,html,flags=re.S)
    html=re.sub(r'<style>(.*?)</style>',lambda m:'<link rel="stylesheet" href="'+add(m[1],'css')+'">',html,flags=re.S)
    def local(match):
        prefix,name,suffix=match.groups();p=root/name
        if not p.is_file():return match[0]
        return prefix+add(p.read_bytes(),p.suffix[1:])+suffix
    html=re.sub(r'((?:src|href)=")/mykos/([A-Za-z0-9_-]+\.(?:js|css))(?:\?[^" ]*)?(")',local,html)
    # Discover scripts in the head so ordered classic execution does not cause
    # a serial network round-trip for each extracted inline block on a cold load.
    preload=''.join('<link rel="preload" as="script" href="/mykos/assets/'+name+'">' for name in assets if name.endswith('.js'))
    html=html.replace('</head>',preload+'</head>',1)
    return html,assets

def plan(root):
    root=Path(root)
    signature=tuple((p.name,p.stat().st_mtime_ns,p.stat().st_size) for p in sorted(root.iterdir()) if p.is_file() and p.suffix in ('.html','.css','.js'))
    return _plan(str(root),signature)

def asset(root,name,request):
    if not re.fullmatch(r'[a-f0-9]{64}\.(?:js|css)',name):raise HTTPException(404)
    value=plan(root)[1].get(name)
    if value is None:raise HTTPException(404)
    return encoded(*value,request=request,immutable=True)
