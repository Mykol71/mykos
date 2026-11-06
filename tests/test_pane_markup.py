"""Actual markup must satisfy every registered pane's startup contract."""
from pathlib import Path
from html.parser import HTMLParser
import ast,re
ROOT=Path(__file__).resolve().parents[1]
class Markup(HTMLParser):
 def __init__(self):super().__init__();self.ids=set();self.links=set()
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if a.get('id'):self.ids.add(a['id'])
  if tag=='a' and a.get('href'):self.links.add(a['href'])
def test_registered_families_have_real_navigation_and_controls():
 html=(ROOT/'services/web/index.html').read_text();m=Markup();m.feed(html)
 families=ast.literal_eval('['+html.split('const mykosOsFamilies = [',1)[1].split('];',1)[0]+']')
 for family in families:
  prefix='' if family=='linux' else family+'-'
  for suffix in ('mykos-containers-pane','mykos-container-panel','mykos-stage-controls','mykos-stage-button','mykos-container-refresh','mykos-image-builds'):
   assert prefix+suffix in m.ids,(family,suffix)
  assert '#'+prefix+'mykos-containers-pane' in m.links,family
