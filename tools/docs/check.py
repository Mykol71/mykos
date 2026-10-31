"""Validate local documentation links, assets, viewer allowlists and originals."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit,unquote
import ast,hashlib,json,re,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[2];DOCS=ROOT/'docs'
class Page(HTMLParser):
 def __init__(self,text):
  super().__init__();self.ids=set();self.links=[];self.feed(text)
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if 'id' in a:
   assert a['id'] not in self.ids,'Duplicate anchor '+a['id'];self.ids.add(a['id'])
  if tag in ('a','img') and a.get('href' if tag=='a' else 'src'):
   self.links.append(a.get('href' if tag=='a' else 'src'))
pages={p.name:Page(p.read_text()) for p in (DOCS/'rendered').glob('*.html')}
links=0
for name,page in pages.items():
 for href in page.links:
  url=urlsplit(href)
  if url.scheme or url.netloc or url.path.startswith('/'):continue
  target=unquote(url.path) or name
  assert '..' not in Path(target).parts,(name,href)
  if target.endswith('.html'):
   assert target in pages,(name,href,'missing page')
   if url.fragment:assert unquote(url.fragment) in pages[target].ids,(name,href,'missing anchor')
  else:assert (DOCS/target).is_file(),(name,href,'missing asset')
  links+=1
 # Ensure render retained each Markdown local link and converted page suffixes.
 source=(DOCS/name.replace('.html','.md')).read_text()
 for href in re.findall(r'!?\[[^\]\n]*\]\(([^\s)]+)\)',source):
  if urlsplit(href).scheme or href.startswith('/'):continue
  expected=re.sub(r'\.md(?=$|#)', '.html',href)
  assert expected in page.links,(name,href,'source link absent from render')
values={}
for item in ast.parse((ROOT/'services/web/supporting_docs.py').read_text()).body:
 if isinstance(item,ast.Assign) and isinstance(item.targets[0],ast.Name):
  if item.targets[0].id in ('GUIDES','DIAGRAMS','DOWNLOADS','SCREENSHOTS','BRAND_LOGOS'):values[item.targets[0].id]=ast.literal_eval(item.value)
public_guides=json.loads((ROOT/'tools/docs/guides.json').read_text())
assert set(values['GUIDES'])==set(public_guides)
assert all((DOCS/name).is_file() for name in public_guides)
assert set(pages)=={p[:-3]+'.html' for p in values['GUIDES']}
assert values['DIAGRAMS']=={p.name for p in (DOCS/'diagrams').iterdir()}
assert values['SCREENSHOTS']=={p.name for p in (DOCS/'screenshots').iterdir()}
assert values['BRAND_LOGOS']=={p.name for p in (DOCS/'brands').iterdir()}
models=json.loads((ROOT/'tools/docs/diagrams.json').read_text())
for model in models:
 xml=ET.parse(DOCS/'diagrams'/(model['name']+'.svg'))
 assert xml.getroot().tag.endswith('svg')
 assert len([x for x in xml.iter() if x.attrib.get('class')=='node'])==len(model['nodes'])
 assert len([x for x in xml.iter() if x.attrib.get('class')=='edge'])==len(model['edges'])
 assert (DOCS/'diagrams'/(model['name']+'.mmd')).read_text().startswith('flowchart TD\n')
protected={
 'MYKOS_Administrator_Guide.docx':'c680ad5e198e7127683c811a9ed2b755d6314ea6210a782d9ba4be3e66b1f33b',
 'KEY_ISSUES.md':'bc472241055012e2638dd7843075a60ffa1ea6ae078ded115960b6ce56538235',
 'MYKOS_Administrator_Guide.md':'bc899eeaa339ac8e0811ad645fd130acc09acdce7268025349e321153adedaa3'}
for name,digest in protected.items():assert hashlib.sha256((DOCS/name).read_bytes()).hexdigest()==digest,name+' was changed'
for name in pages:
 text=(DOCS/'rendered'/name).read_text()
 assert 'color-scheme:only light' in text and 'name="color-scheme" content="only light"' in text
 assert '<script' not in text.lower(),name
print(json.dumps({'html_pages':len(pages),'local_links':links,'diagrams':len(models),'protected_originals':len(protected),'allowlists':'exact','theme':'permanent light'},indent=2))
