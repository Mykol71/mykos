// Exercise pending refreshes and out-of-order responses without a live backend.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(require('node:path').join(__dirname, '../services/web/index.html'), 'utf8');
const source = html.slice(html.indexOf('async function mykosExpandShop('), html.indexOf('\nfunction mykosToggleShop('));
class Element {
    constructor() { this.dataset={}; this.cells=[]; this.children=[]; this.className=''; this.isConnected=true; this.classList={contains:name=>this.className===name,add(){}}; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children=children; this.textContent=''; }
    querySelector() { return grid; }
}
const grid = new Element(); grid.textContent='Previously loaded details';
const detail = new Element(); detail.className='mykos-detail-row'; detail.dataset.detailShop='12345678';
const row = new Element(); row.dataset.mykosShop='12345678'; row.nextElementSibling=detail;
row.after=()=>{ throw Error('Refresh must reuse the expanded row'); };
const requests=[];
const context=vm.createContext({document:{getElementById:()=>({querySelectorAll:()=>[row]}),createElement:()=>new Element()},
    mykosDetailVersion:0, mykosNodeFamily:()=> 'linux', mykosSection:(title,rows)=>({title,rows}),
    mykosOsLabel:value=>value, vpnLabel:value=>value, Date,
    fetch:()=>new Promise(resolve=>requests.push(resolve))});
vm.runInContext(html.slice(html.indexOf('function mykosUsesPodmanIp('), html.indexOf('function table(){')) + source,context);
const response=name=>({ok:true,json:async()=>({details:{container:name},fresh:true,nodes:{}})});
(async()=>{
    const first=context.mykosExpandShop('12345678');
    assert.equal(grid.textContent,'Previously loaded details','Keep content while requests are pending');
    const second=context.mykosExpandShop('12345678');
    requests[2](response('newest')); requests[3](response('newest'));
    await second;
    assert.equal(grid.children[0].rows[0][1],'newest');
    requests[0](response('older')); requests[1](response('older'));
    await first;
    assert.equal(grid.children[0].rows[0][1],'newest','Ignore superseded response');
    const failed=context.mykosExpandShop('12345678');
    requests[4]({ok:false}); requests[5]({ok:false});
    await failed;
    assert.equal(grid.children[0].rows[0][1],'newest','Keep content when refresh fails');
    console.log('Expanded detail refresh checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
