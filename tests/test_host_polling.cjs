const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../services/web/index.html'), 'utf8');
const source = html.slice(html.indexOf('let mykosHostMetricsLoading = false;'), html.indexOf('// MYKOS HOST VERSION DISPLAY'));
const elements = new Map(), requests = [], timers = new Map(), intervals = [];
const el = id => { if (!elements.has(id)) elements.set(id, {hidden:false}); return elements.get(id); };
let timerId = 0;
let now = 0;
const document = {hidden:false};
const context = vm.createContext({el, document, AbortController, Date:{now:() => now},
    mykosHostBytes:String, mykosSetHostMetric(){}, mykosLoadCountMetrics(){}, mykosRenderHostStatus(){},
    setInterval:(callback, ms) => intervals.push(ms),
    setTimeout:(callback, ms) => { assert.equal(ms,5000); timers.set(++timerId, callback); return timerId; },
    clearTimeout:id => timers.delete(id),
    fetch:(url, options) => new Promise((resolve, reject) => {
        assert.equal(url,'/mykos/api/web/host-metrics');
        options.signal.addEventListener('abort', () => reject(new Error('Timed out')));
        requests.push({resolve, reject});
    })});
vm.runInContext(source, context);
const poll = () => { now += 3000; return vm.runInContext('mykosLoadHostMetrics()', context); };
const response = {ok:true, json:async () => ({cpu_percent:12, cpu_count:4, memory:null, disk:null, load_average:[1,1,1], uptime_seconds:60})};
(async () => {
    assert.deepEqual(intervals,[3000]);
    const first = poll(); await poll();
    assert.equal(requests.length,1,'Overlapping refreshes must share a single in-flight request');
    requests[0].resolve(response); await first;
    now -= 3000;
    await vm.runInContext('mykosLoadHostMetrics()', context);
    assert.equal(requests.length,1,'Dashboard reloads must not bypass the sampling interval');
    assert.equal(timers.size,0);
    assert.equal(el('mykos-host-cpu-value').textContent,'12.0%');
    const failed = poll(); requests[1].reject(new Error('Offline')); await failed;
    assert.equal(el('mykos-host-cpu-value').textContent,'Unknown');
    const stalled = poll(); timers.values().next().value(); await stalled;
    assert.equal(timers.size,0,'Timeout must release the poll guard');
    const recovered = poll(); requests[3].resolve(response); await recovered;
    assert.equal(el('mykos-host-cpu-value').textContent,'12.0%');
    document.hidden = true; await poll(); assert.equal(requests.length,4);
    document.hidden = false; el('dashboard').hidden = true; await poll(); assert.equal(requests.length,4);
    assert.match(html,/mykosChartReducedMotion \? 1000 : 50/);
    assert.match(html,/requestAnimationFrame\(mykosAnimateHostChart\)/);
    console.log('Host polling checks passed: three-second sampling, throttled dashboard reloads, animation-frame rendering, no overlapping requests, timeout recovery and hidden-page pause.');
})().catch(error => { console.error(error); process.exitCode = 1; });
