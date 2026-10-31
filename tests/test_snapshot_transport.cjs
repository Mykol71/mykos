const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(process.env.MYKOS_TEST_HTML || path.join(__dirname,'../services/web/index.html'),'utf8');
const parser=html.slice(html.indexOf('async function mykosStageJSON('),html.indexOf('function mykosStageSetAccess('));
const action=html.slice(html.indexOf('async function mykosRunContainerAction('),html.indexOf('function mykosContainerActionButton('));
let responses=[],requests=[],alerts=[];
const context=vm.createContext({fetch:async(url,options)=>{requests.push({url,options});const value=responses.shift();if(!value)throw Error('Unexpected request');return {ok:value.status<400,status:value.status,json:async()=>{if(value.html)throw SyntaxError('Unexpected token <');return value.data;}};},setTimeout:fn=>fn(),alert:message=>alerts.push(message),load:async()=>{},mykosRefreshListStats:async()=>{},mykosRefreshSnapshots:async()=>{},mykosRefreshStartupInventory:async()=>{},mykosSelectedShop:null});
vm.runInContext(parser+'\n'+action,context);
(async()=>{
 responses=[{status:200,data:{state:'done'}}];assert.equal((await context.mykosStageJSON('/status')).state,'done');
 responses=[{status:503,html:true}];await assert.rejects(context.mykosStageJSON('/snapshot',{method:'POST'}),e=>e.status===503 && /HTTP 503/.test(e.message) && !/Unexpected token|DOCTYPE/.test(e.message));
 responses=[{status:409,data:{detail:'Already active'}}];await assert.rejects(context.mykosStageJSON('/snapshot'),e=>e.status===409 && e.message==='Already active');
 responses=[{status:200,html:true}];await assert.rejects(context.mykosStageJSON('/status'),/invalid JSON/);
 requests=[];responses=[{status:200,data:{state:'queued'}},{status:503,html:true},{status:504,html:true},{status:200,data:{state:'done',output:'{}'}}];
 await context.mykosRunContainerAction('12345678','snapshot');
 assert.equal(requests.filter(r=>r.options.method==='POST').length,1);
 assert.equal(requests.length,4);assert.equal(alerts.length,1);assert.match(alerts[0],/Snapshot saved/);
 requests=[];responses=[{status:503,html:true}];await assert.rejects(context.mykosRunContainerAction('12345678','snapshot'),/HTTP 503/);assert.equal(requests.length,1,'An ambiguous action POST must never be replayed');
 console.log('Snapshot transport: clear HTTP errors, preserved JSON detail, GET recovery, and no duplicate POSTs verified');
})().catch(error=>{console.error(error);process.exitCode=1});
