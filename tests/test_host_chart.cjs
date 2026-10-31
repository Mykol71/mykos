// Run with node tests/test_host_chart.cjs. No browser or external packages needed.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(require('node:path').join(__dirname, '../services/web/index.html'), 'utf8');
const source = html.slice(html.indexOf('// Browser-local history:'), html.indexOf('\nfunction mykosLoadCountMetrics()'));
class Element {
    constructor(tag = 'div') { this.tag = tag; this.children = []; this.attrs = {}; this.dataset = {}; this.style = {}; this.hidden = false; this.clientWidth = 1000; this.offsetWidth = 180; }
    setAttribute(key, value) { this.attrs[key] = String(value); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener() {}
}
const elements = new Map();
const el = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
const clock = {now: 1000000};
class FakeDate extends Date { static now() { return clock.now; } }
const context = vm.createContext({el, Date:FakeDate, assert, clock, mykosHostBytes:String,
    document:{createElement:tag => new Element(tag), createElementNS:(_,tag) => new Element(tag)},
    window:{addEventListener() {}}, requestAnimationFrame:() => 1, setInterval:() => 1});
vm.runInContext(source + `
assert.equal(new Set(mykosChartMetrics.map(m => m.color)).size, 7);
assert.ok(!mykosChartMetrics.some(m => ['backups','repos'].includes(m.key)));
assert.equal(mykosChartWindow, 300000);
assert.equal(mykosCountPercent(40),100);
assert.equal(mykosCountPercent(8),20);
assert.equal(mykosCountPercent(0),0);
assert.equal(mykosCountPercent(null),null);
assert.equal(mykosCountPercent(80),200);
const animatedMetric = {points:[{t:clock.now-7500,percent:20},{t:clock.now,percent:80}]};
const animatedPoint = animatedMetric.points[1];
assert.equal(mykosChartDisplayPercent(animatedMetric,animatedPoint,clock.now),20);
assert.ok(mykosChartDisplayPercent(animatedMetric,animatedPoint,clock.now+500)>20);
assert.ok(mykosChartDisplayPercent(animatedMetric,animatedPoint,clock.now+500)<80);
assert.equal(mykosChartDisplayPercent(animatedMetric,animatedPoint,clock.now+1000),80);
assert.equal(animatedPoint.percent,80,'Display easing must not mutate samples');
assert.equal(mykosChartDisplayPercent({...animatedMetric,bar:true},animatedPoint,clock.now),80);
mykosSetHostMetric('cpu', 74, '74%', '8 CPUs'); mykosDrawHostChart();
assert.equal(mykosChartLayout.x(clock.now), mykosChartLayout.right, 'Current time stays at the right edge of the live five-minute window');
assert.equal(el('mykos-host-cpu-metric').dataset.level, '');
assert.equal(el('mykos-chart-alert').hidden, true);
clock.now += 10000;
mykosSetHostMetric('cpu', 75, '75%', '8 CPUs'); mykosDrawHostChart();
assert.equal(el('mykos-host-cpu-metric').dataset.level, 'high');
assert.match(el('mykos-chart-alert').textContent, /CPU high/);
clock.now += 10000;
mykosSetHostMetric('cpu', 90, '90%', '8 CPUs');
mykosSetHostMetric('containers', mykosCountPercent(2), '2', 'running / 2');
mykosSetHostMetric('vpns', mykosCountPercent(1), '1', 'up / 2'); mykosDrawHostChart();
const countLines = el('mykos-host-chart').children.filter(e => e.tag === 'rect' && ['containers','vpns'].includes(e.attrs['data-series']));
assert.equal(countLines.length,2);
assert.equal(el('mykos-host-chart').children.filter(e=>e.tag==='rect').length,2);
assert.equal(countLines[0].attrs.fill,'#fef08a');
assert.equal(countLines[0].attrs['fill-opacity'],'0.09');
assert.ok(Number(countLines[0].attrs.x)+Number(countLines[0].attrs.width) <= Number(countLines[1].attrs.x), 'Container and VPN columns never overlap');
const chartChildren = el('mykos-host-chart').children;
assert.ok(chartChildren.indexOf(countLines[1]) < chartChildren.findIndex(item => item.attrs['data-series'] === 'cpu'), 'Counts stay behind resource lines');
assert.notEqual(countLines[0].attrs.height, countLines[1].attrs.height);
assert.ok(!el('mykos-host-chart').children.some(e => e.tag === 'path' && e.attrs.stroke === '#fb923c'));
assert.equal(el('mykos-host-cpu-metric').dataset.level, 'critical');
assert.equal(el('mykos-host-containers-metric').dataset.level, '');
assert.equal(el('mykos-chart-alert').dataset.level, 'critical');
assert.ok(el('mykos-host-chart').children.some(e => e.tag === 'circle' && e.attrs.stroke === '#fb7185'));
assert.ok(el('mykos-host-chart').children.some(e => e.tag === 'line' && e.attrs['stroke-width'] === '6'));
clock.now += 10000;
mykosSetHostMetric('cpu', null, 'Unknown', 'Unavailable');
clock.now += 10000;
mykosSetHostMetric('cpu', 20, '20%', '8 CPUs'); mykosDrawHostChart();
const cpuPath = el('mykos-host-chart').children.find(e => e.tag === 'path' && e.attrs.stroke === '#38bdf8');
assert.equal((cpuPath.attrs.d.match(/M/g) || []).length, 2, 'Missing samples must break the line');
mykosShowChartTooltip(clock.now);
assert.equal(el('mykos-chart-tooltip').hidden, false);
clock.now += 30000; mykosDrawHostChart();
assert.equal(el('mykos-host-cpu-value').textContent, '—');
assert.equal(el('mykos-host-containers-value').textContent, '—');
assert.equal(el('mykos-chart-alert').hidden, true);
clock.now += mykosChartWindow; mykosDrawHostChart();
assert.ok(mykosChartMetrics.find(m => m.key === 'cpu').points.length > 0, 'Data older than five minutes must remain available for browsing');
mykosSetHostMetric('load', 150, '12.00', '8 CPUs'); mykosDrawHostChart();
assert.ok(el('mykos-host-chart').children.some(e => e.tag === 'text' && e.textContent === '150%'), 'Over-capacity load must remain visible');
mykosResetHostChart();
assert.ok(mykosChartMetrics.every(m => m.points.length === 0));
assert.equal(mykosChartStartedAt, null);
mykosMergeHostHistory([{collected_at:(clock.now - 600000) / 1000, cpu_percent:42, cpu_count:4, memory:null, disk:null, load_average:[1,1,1], graph_counts:{containers:1,vpns:1}, graph_totals:{containers:2,vpns:1}}]);
assert.equal(mykosChartMetrics.find(m => m.key === 'cpu').points[0].value, '42.0%');
assert.equal(mykosChartMetrics.find(m => m.key === 'containers').points[0].percent, 100/40);
mykosResetHostChart();
mykosSetHostMetric('containers', 20, '3', 'running');
clock.now += 10000;
mykosSetHostMetric('containers', 40, '6', 'running');
clock.now += 30000;
mykosSetHostMetric('containers', 60, '9', 'running');
clock.now += 10000;
mykosSetHostMetric('containers', null, 'Unknown', 'Unavailable');
clock.now += 10000;
mykosSetHostMetric('containers', 80, '12', 'running');
mykosDrawHostChart();
const countBars = el('mykos-host-chart').children.filter(item => item.attrs['data-series'] === 'containers');
assert.equal(countBars.length, 4, 'Only actual known count buckets are drawn; gaps remain empty');
assert.ok(countBars.every(item => item.tag === 'rect' && Number(item.attrs.width)>0));
assert.equal(countBars.filter(item=>item.attrs['data-changed']==='true').length,1, 'Only a known adjacent count change is emphasized');
const emphasized=countBars.find(item=>item.attrs['data-changed']==='true');
assert.equal(emphasized.attrs['stroke-width'],'1.5');
assert.equal(emphasized.attrs['fill-opacity'],'0.36');
assert.equal(countBars[0].attrs['fill-opacity'],'0.09');

mykosResetHostChart();
mykosSetHostMetric('cpu', 10, '10%', '8 CPUs'); mykosDrawHostChart();
const firstReading = clock.now;
assert.equal(mykosChartLayout.x(firstReading), mykosChartLayout.right);
clock.now += 600000;
mykosSetHostMetric('cpu', 35, '35%', '8 CPUs'); mykosDrawHostChart();
assert.equal(mykosChartLayout.start, clock.now - 300000);
assert.ok(mykosChartMetrics.find(m => m.key === 'cpu').points.some(point => point.t === firstReading));
mykosMoveHistory(-600); mykosDrawHostChart();
assert.equal(mykosChartLayout.now, clock.now - 600000);
assert.equal(el('mykos-host-cpu-value').textContent, '10%', 'Historical navigation must display the selected old reading');
mykosMoveHistory(999999); mykosDrawHostChart();
assert.equal(mykosChartViewEnd, null);
assert.equal(mykosChartLayout.now, clock.now);
clock.now += mykosChartRetention + 1;
mykosDrawHostChart();
assert.equal(mykosChartMetrics.find(m => m.key === 'cpu').points.length, 0, 'Data must expire after thirty minutes');
`, context);
console.log('Host chart checks passed: thresholds, healthy availability, colors, gaps, staleness, retention, scaling and reset.');

vm.runInContext(`assert.equal(mykosHistoryValue({data:{percent:25,used_bytes:250,total_bytes:1000}}, "data").value,"25.0%"); assert.equal(mykosHistoryValue({}, "data").percent,null); assert.equal(mykosChartMetrics.find(m=>m.key==="disk").label,"System");`,context);
