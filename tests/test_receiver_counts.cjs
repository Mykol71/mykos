const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');const code=html.match(/window\.mykosReceiverCounts=function\(metrics,start,end\)\{([\s\S]*?)\n \};/)[0];
let writes=0;const nodes={};const get=id=>nodes[id]??={set innerHTML(x){this.drawing=x;writes++},setAttribute(k,v){this[k]=v},style:{}};
const root={dataset:{theme:'receiver'}},range={value:'1500',max:'1500'};const context=vm.createContext({window:{},document:{getElementById:get},root,range,lastCountPaint:'',recallReading(){},mykosReceiverSignalLevel:p=>Number.isFinite(p)?Math.max(0,Math.min(10,Math.ceil(p/10))):0});vm.runInContext(code,context);
const metric=(key,points)=>({key,points}),p=(t,percent,value)=>({t,percent,value});const metrics=[metric('containers',[p(10000,4,'2'),p(300000,8,'4'),p(302000,100,'50')]),metric('vpns',[p(300000,100/15,'1')])];
context.window.mykosReceiverCounts(metrics,1000,301000);assert.equal(get('receiver-container-count').textContent,'4');assert.equal(get('receiver-vpn-count').textContent,'1');
for(const id of ['receiver-container-signal','receiver-vpn-signal']){assert.equal((get(id).drawing.match(/<rect/g)||[]).length,10);assert.equal((get(id).drawing.match(/data-lit="true"/g)||[]).length,1);}
assert.match(get('receiver-container-signal').drawing,/fill="#8ed5ff"/);assert.match(get('receiver-vpn-signal').drawing,/fill="#71de91"/);
const before=writes;context.window.mykosReceiverCounts(metrics,1000,301100);assert.equal(writes,before,'Rendering bounded to1Hz');
context.window.mykosReceiverCounts(metrics,100000,330000);assert.equal(get('receiver-container-count').textContent,'—');assert.equal((get('receiver-container-signal').drawing.match(/data-lit="true"/g)||[]).length,0,'Stale observations do not light bars');
context.window.mykosReceiverCounts([metric('containers',[p(331000,0,0)])],100000,331100);assert.equal(get('receiver-container-count').textContent,0);assert.equal((get('receiver-container-signal').drawing.match(/data-lit="true"/g)||[]).length,0);assert.equal(get('receiver-vpn-count').textContent,'—');
root.dataset.theme='dark';const old=writes;context.window.mykosReceiverCounts(metrics,100000,332000);assert.equal(writes,old);
console.log('Fixed signal counts: actual selected readings, zero, missing, stale/future exclusion and bounded inactive-theme behavior passed.');
