const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
const elements=new Map(),callbacks={};
function element(id){if(!elements.has(id))elements.set(id,{value:'',textContent:'',disabled:false,open:false,addEventListener(name,fn){this[name]=fn;},showModal(){this.open=true},close(){this.open=false},focus(){}});return elements.get(id)}
let response={id:'01234567',text:'Shared text',revision:1,updated_at:1,updated_by:'reader'},reject=null,requests=[];
const context=vm.createContext({console,document:{getElementById:element,addEventListener:(n,f)=>callbacks[n]=f},window:{confirm:()=>false},mykosStageJSON:async(url,options)=>{requests.push([url,options]);if(reject)throw Error(reject);return response;}});
const start=html.indexOf('const mykosIdNotesDialog=');vm.runInContext(html.slice(start,html.indexOf('</script>',start)),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 context.mykosOpenIdNotes('01234567');await tick();assert.equal(element('mykos-id-notes-text').value,'Shared text');assert.equal(element('mykos-id-notes-save').disabled,false);
 element('mykos-id-notes-text').value='My edits';assert.equal(context.mykosCloseIdNotes(),false);assert.equal(element('mykos-id-notes-dialog').open,true);
 reject='Another user changed these notes';await element('mykos-id-notes-form').submit({preventDefault(){}});assert.equal(element('mykos-id-notes-text').value,'My edits');assert.match(element('mykos-id-notes-status').textContent,/Another user/);
 reject=null;response={...response,text:'My edits',revision:2};await element('mykos-id-notes-form').submit({preventDefault(){}});assert.equal(JSON.parse(requests.at(-1)[1].body).revision,1);assert.equal(context.mykosCloseIdNotes(),true);
 response={...response,text:'Second customer',id:'87654321',revision:1};context.mykosOpenIdNotes('87654321');await tick();assert.match(requests.at(-1)[0],/87654321$/);assert.equal(element('mykos-id-notes-text').value,'Second customer');context.mykosCloseIdNotes(true);assert.equal(element('mykos-id-notes-text').value,'');
 reject='Server unavailable';context.mykosOpenIdNotes('01234567');await tick();assert.equal(element('mykos-id-notes-save').disabled,true);
 assert.ok(html.includes("if (event.target.closest('[data-id-notes]')) return;"));
 console.log('Shared ID notes: isolated customer IDs, conflict retention, load failures, discard protection and logout clearing verified.');
})().catch(error=>{console.error(error);process.exitCode=1});

// Exercise the real capture handler: SVG leaf clicks must never reach row details.
const captureStart=html.indexOf('// Capture inventory clicks before the previous details handler.');
const captureEnd=html.indexOf('// Infrastructure uptime and memory refresh',captureStart);
let capture,opened=[],details=[];
const events=vm.createContext({document:{getElementById:()=>({addEventListener:(name,handler,capturePhase)=>{assert.equal(capturePhase,true);capture=handler;}})},mykosOpenIdNotes:id=>opened.push(id),showNodeDetails:id=>details.push(id),mykosToggleShop:()=>{}});
vm.runInContext(html.slice(captureStart,captureEnd),events);
let stopped=false,prevented=false;
capture({target:{closest:selector=>selector==='[data-id-notes]'?{dataset:{idNotes:'01234567'}}:selector==='tr.mykos-node-row'?{dataset:{mykosShop:'01234567'}}:null},preventDefault(){prevented=true},stopImmediatePropagation(){stopped=true}});
assert.deepEqual(opened,['01234567']);assert.deepEqual(details,[]);assert.ok(stopped&&prevented);
capture({target:{closest:selector=>selector==='tr.mykos-node-row'?{dataset:{mykosShop:'87654321'}}:null},stopImmediatePropagation(){}});assert.deepEqual(details,['87654321']);

// VPN controls must reach their own target handlers, not row details.
for(const selector of ['.mykos-add-vpn-button','.mykos-remove-vpn-button']) {
    let intercepted=false;const before=details.length;
    capture({target:{closest:s=>s===selector?{}:s==='tr.mykos-node-row'?{dataset:{mykosShop:'01234567'}}:null},stopImmediatePropagation(){intercepted=true}});
    assert.equal(intercepted,false,selector+' must reach its button handler');
    assert.equal(details.length,before,selector+' must not open node details');
}
