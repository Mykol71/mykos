"""Read-only, host-local aggregate RPM statistics. No request-identifying data is exposed."""
import datetime as dt
import html
import json
from pathlib import Path

SNAPSHOT = Path('/opt/mykos-web/data/rpm-download-stats.json')

def load(path=SNAPSHOT):
    if not path.is_file():
        return {'format':1,'configured':False}
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Statistics snapshot exceeds limit')
    value=json.loads(path.read_text())
    if value.get('format')!=1:raise ValueError('Unsupported statistics snapshot')
    value['configured']=True
    return value

def render(data):
    esc=lambda value:html.escape(str(value))
    def stamp(value):
        return dt.datetime.fromtimestamp(value,dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if value else 'Never'
    def table(head, rows):
        return '<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+esc(v)+'</th>' for v in head)+'</tr></thead><tbody>'+(''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows) or '<tr><td colspan="'+str(len(head))+'">No observations yet</td></tr>')+'</tbody></table></div>'
    body='<p class="eyebrow">MYKOS · Administrator report</p><h1>RPM download statistics</h1><nav><a href="/mykos/#mykos-repos-title">Back to repositories</a><a href="/mykos/api/web/mykos/rpm-downloads/view">Refresh report</a><a href="/mykos/api/web/mykos/rpm-downloads">View JSON</a></nav>'
    if not data.get('configured'):
        body+='<section><h2>Tracking is not configured on this host</h2><p>The repository host needs the optional RPM statistics collector and logging configuration. This report does not imply zero downloads.</p></section>'
    else:
        repo=data['repository'];g=data['github'];external=sum(r['requests'] for r in repo['totals'] if r['audience']=='external' and r['category']=='full');internal=sum(r['requests'] for r in repo['totals'] if r['audience']=='internal' and r['category']=='full');partial=sum(r['requests'] for r in repo['totals'] if r['category']=='partial')
        body+='<p class="muted">Collected '+esc(stamp(data['updated_at']))+' · Repository tracking began '+esc(stamp(data['tracking_since']))+'</p>'
        body+='<section><h2>Repository activity by day</h2><p class="muted">UTC dates; includes both full and partial traffic in byte totals.</p>'+table(['Day','Traffic','Full-file responses','Partial responses','Bytes'],[[r['day'],r['audience'],r['full'],r['partial'],r['bytes']] for r in repo['daily']])+'</section>'
        body+='<div class="cards">'
        for n,label in [(external,'External full-file responses'),(internal,'Internal full-file responses'),(partial,'Partial/range responses'),(g.get('total','—'),'GitHub-reported RPM downloads')]:
            body+='<section><strong class="count">'+esc(n)+'</strong><span>'+esc(label)+'</span></section>'
        body+='</div><p class="note">'+esc(data['notice'])+'</p>'
        if g.get('state')!='ok':body+='<p class="warning">GitHub statistics: '+esc(g.get('state','unavailable'))+'. Last successful refresh: '+esc(stamp(g.get('updated_at')))+'. '+esc(g.get('error',''))+'</p>'
        body+='<section><h2>Repository packages</h2><p class="muted">EL9 and EL10; all hostname and /repos aliases normalized. Internal traffic is shown separately. Validation requests are excluded.</p>'+table(['Package','Repository','Traffic','Full','Partial','Aborted','Failed','Other','Bytes'],[[r['package'],r['repo'].rsplit('-',1)[-1],r['audience'],r['full'],r['partial'],r['aborted'],r['failed'],r['other'],r['bytes']] for r in repo['packages']])+'</section>'
        body+='<section><h2>GitHub release assets</h2><p class="muted">Provider-reported counters for public RPM assets, including source RPMs. Last successful refresh '+esc(stamp(g.get('updated_at')))+'. These counters include repeat and test downloads and cannot identify unique installations.</p>'+table(['Release','Package','Reported downloads'],[[r['release'],r['package'],r['downloads']] for r in g.get('assets',[])])+'</section>'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RPM download statistics — MYKOS</title><style>body{margin:0;background:#f4f7fb;color:#20334a;font:15px/1.6 system-ui,sans-serif}main{max-width:1280px;margin:auto;padding:32px 24px}h1{font-size:32px;margin:6px 0 14px}h2{font-size:20px}.eyebrow,.muted{color:#52647a}.eyebrow{font-size:12px}nav{display:flex;gap:24px;flex-wrap:wrap;margin-bottom:24px}a{color:#075d9d}a:focus-visible{outline:3px solid #1978ad;outline-offset:4px}section{padding:24px;background:white;border:1px solid #d8e2ed;border-radius:10px;margin:18px 0}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.cards section{display:flex;flex-direction:column;background:#e6f2fc;margin:0}.cards span{font-size:12px}.count{font-size:32px;color:#155d8b}.table-wrap{max-width:100%;overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:10px 12px;text-align:left;border-bottom:1px solid #dce5ee}th{background:#edf3f9}td:first-child{overflow-wrap:anywhere;min-width:140px}.note,.warning{padding:16px;background:#fff2cd;border:1px solid #e2c577;border-radius:8px;font-size:13px}@media(max-width:700px){main{padding:22px 16px}.cards{grid-template-columns:repeat(2,1fr);gap:10px}.cards section{padding:16px}section{padding:18px}h1{font-size:27px}}</style></head><body><main>'+body+'</main></body></html>'
