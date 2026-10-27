// Exercise real button handlers with a fake DOM; no live container requests.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../services/web/index.html'),'utf8');
const source=html.slice(html.indexOf('async function mykosRunContainerAction('),html.indexOf('// Override the existing table renderer.'));
class Element{
 constructor(){this.children=[];this.style={};this.dataset={};this.listeners={};this.classList={add(){}};}
 append(...items){this.children.push(...items);}
 setAttribute(k,v){this[k]=v;}
 addEventListener(k,v){this.listeners[k]=v;}
 querySelector(){return null;}
}
function setup(admin=true){
 let nextId=10000000;
 const nodes=['linux','ubuntu','windows','windows-server','macos'].flatMap(family=>['online','offline','unknown'].map(status=>({id:String(nextId++),family,status,vpn:'down'})));
 const rows=nodes.map(n=>{const e=new Element();e.cells=[{textContent:n.id}];return e;});
 const requests=[],prompts=[];let response=null;
 const ctx=vm.createContext({nodes,mykosBusyContainers:new Map(),mykosSnapshotData:[],mykosRefreshSnapshots:async()=>{},mykosRenderSnapshots(){},mykosRefreshStartupInventory:async()=>{},
  mykosListDetails:Object.fromEntries(nodes.map(n=>[n.id,{persistent_storage:true,desktop:n.family==='ubuntu'}])),mykosTerminalAllowed:admin,
  mykosSelectedShop:null,mykosIpsecShop:null,mykosOriginalTable(){},mykosNodeFamily:n=>n.family,mykosRefreshSampleRows(){},
  document:{querySelectorAll:()=>rows,createElement:()=>new Element()},window:{confirm(){return true;},prompt(s){prompts.push(s);return response;}},
  alert(){},mykosRefreshListStats:async()=>{},load:async()=>{},mykosStageJSON:async(url,options)=>{requests.push({url,options});return {state:'done'};}});
 vm.runInContext(source+'\nmykosRenderInventory();',ctx);
 return {nodes,rows,requests,prompts,ctx,respond:r=>response=r};
}
(async()=>{
 const env=setup();
 for(let i=0;i<env.rows.length;i++){
  const b=env.rows[i].children[0].children[0].children.find(b=>b.className==='mykos-delete-button');
  assert.ok(b,env.nodes[i].family+' '+env.nodes[i].status+' trash action');assert.equal(b.disabled,false);assert.match(b.innerHTML,/M3 6h18/);
 }
 const shop=env.nodes[0].id,b=env.rows[0].children[0].children[0].children.find(b=>b.className==='mykos-delete-button');
 env.respond('wrong');await b.listeners.click({stopPropagation(){}});assert.equal(env.requests.length,0);
 env.respond(null);await b.listeners.click({stopPropagation(){}});assert.equal(env.requests.length,0);
 env.ctx.mykosSnapshotData=[{code:shop,capture_mode:'criu-memory'}];env.respond(shop);
 await b.listeners.click({stopPropagation(){}});
 assert.equal(env.requests.length,1);assert.equal(env.requests[0].url,'/mykos/api/web/containers/'+shop);
 assert.equal(env.requests[0].options.method,'DELETE');assert.deepEqual(JSON.parse(env.requests[0].options.body),{confirmation:shop});
 assert.match(env.prompts.at(-1),/original container for restore/);
 const readonly=setup(false);
 for(const row of readonly.rows)assert.equal(row.children[0].children[0].children.some(b=>b.className==='mykos-delete-button'),false);
 console.log('Delete actions: all OS panes/statuses, typed confirmation, cancellation, read-only access and DELETE JSON passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
