const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../services/web/index.html'),'utf8');
const source=html.slice(html.indexOf('let mykosHostMetricsLoading = false;'),html.indexOf('// MYKOS HOST VERSION DISPLAY'));
const elements=new Map(),requests=[],timers=new Map(),samples=[];let nextTimer=0,now=1000;
const el=id=>{if(!elements.has(id))elements.set(id,{hidden:false});return elements.get(id)};
const document={hidden:false,documentElement:{dataset:{role:'admin'}}};
const ctx=vm.createContext({el,document,AbortController,Date:{now:()=>now},mykosHostBytes:String,mykosSetHostMetric:(...v)=>samples.push(v),mykosLoadCountMetrics(){},mykosRenderHostStatus(){},
 setTimeout:(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,ms});return id},clearTimeout:id=>timers.delete(id),fetch:(url,options)=>new Promise((resolve,reject)=>{assert.equal(url,'/mykos/api/web/host-metrics');options.signal.addEventListener('abort',()=>reject(new Error('Timed out')));requests.push({resolve,reject})})});
vm.runInContext(source,ctx);const poll=()=>vm.runInContext('mykosLoadHostMetrics()',ctx);
const response=stamp=>({ok:true,json:async()=>({collected_at:stamp,cpu_percent:12,cpu_count:4,memory:null,disk:null,load_average:[1,1,1]})});
const scheduled=()=>[...timers.values()].filter(t=>t.ms!==5000);
(async()=>{
 assert.equal(scheduled()[0].ms,1000);
 const first=poll();await poll();assert.equal(requests.length,1,'Never overlap a request');requests[0].resolve(response(1));await first;
 assert.equal(el('mykos-host-cpu-value').textContent,'12.0%');assert(samples.every(s=>s[4]===1000),'Use collection time, not fetch time');
 now=1999;await poll();assert.equal(requests.length,1);assert.equal(scheduled()[0].ms,1,'An early timer reschedules its remaining millisecond instead of losing a full tick');
 now=2000;const second=poll();requests[1].resolve(response(2));await second;assert.equal(requests.length,2);
 const count=samples.length;now=3000;const duplicate=poll();requests[2].resolve(response(2));await duplicate;assert.equal(samples.length,count,'Repeated cached reading adds no new graph samples');
 now=4000;const failed=poll();requests[3].reject(new Error('Offline'));await failed;assert.equal(el('mykos-host-cpu-value').textContent,'Unknown');
 now=5000;const stalled=poll();const abort=[...timers.values()].find(t=>t.ms===5000);assert(abort);now=10000;abort.fn();await stalled;assert.equal([...timers.values()].filter(t=>t.ms===5000).length,0,'Timeout releases in-flight guard');
 const recovered=poll();requests[5].resolve(response(10));await recovered;assert.equal(el('mykos-host-cpu-value').textContent,'12.0%');
 document.hidden=true;now+=1000;await poll();assert.equal(requests.length,6);document.hidden=false;el('dashboard').hidden=true;now+=1000;await poll();assert.equal(requests.length,6);
 assert.match(html,/mykosUpdateHostChartHeads\(Date\.now\(\)\)/);assert.match(html,/const interval = 1000;/);
 console.log('One-second polling: no skipped early tick, no overlapping requests, real timestamp deduplication, timeout recovery and hidden-page pause passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
