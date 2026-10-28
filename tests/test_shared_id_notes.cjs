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
