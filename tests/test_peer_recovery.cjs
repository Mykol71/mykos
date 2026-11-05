const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../services/web/peer-ui.js'),'utf8');
const make=()=>({style:{},children:[],append(...nodes){this.children.push(...nodes)},setAttribute(k,v){this[k]=v},getAttribute(k){return this[k]||null},removeAttribute(k){delete this[k]},addEventListener(){}});
const frames=new Map(),region=make(),calls=[];let confirmed=false,refreshes=0,reject=false;
const context=vm.createContext({document:{createElement:make},frames,region,confirm:()=>confirmed,request:async(p,o)=>{calls.push([p,o.method]);if(reject)throw Error('Teardown failed');},refresh:async()=>refreshes++,attachActions:()=>{}});
vm.runInContext(source.slice(source.indexOf('    async function removePairing('),source.indexOf('    function attachActions('))+source.slice(source.indexOf('    function render('),source.indexOf('    async function refresh(')),context);
(async()=>{
 const peer={id:'a'.repeat(32),name:'Unfinished host',state:'pending',connected:false,expired:false};context.render(peer);const item=frames.get(peer.id),button=item.removePairingButton;
 assert.equal(item.frame.hidden,true);assert.equal(button.hidden,false);
 const click=()=>button.onclick({stopPropagation(){}});
 await click();assert.equal(calls.length,0,'Declining confirmation leaves pairing untouched');
 confirmed=true;await click();assert.deepEqual(calls,[['/'+peer.id,'DELETE']]);assert.equal(refreshes,1);
 reject=true;button.disabled=false;await click();assert.equal(item.state.textContent,'Teardown failed');assert.equal(button.disabled,false,'Failure allows retry');
 context.render({...peer,state:'active',connected:false});assert.equal(button.hidden,false);
 context.render({...peer,state:'active',connected:true});assert.equal(button.hidden,false,'Removal remains available until embedded controls actually load');
 assert.match(source,/item\.removePairingButton\.hidden=true;/);
 console.log('Pending, offline and unloaded peer controls remain removable with confirmation, error recovery and exact local DELETE target.');
})().catch(e=>{console.error(e);process.exitCode=1;});
