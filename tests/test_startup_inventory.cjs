const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
let calls=0,now=10000,renders=0,stats=0;
const context=vm.createContext({nodes:[],Date:{now:()=>now},fetch:async()=>{calls++;return {ok:true,json:async()=>({nodes:[{id:'12345678',status:'online'}]})}},mykosRefreshListStats:async()=>{stats++},mykosRenderInventory(){renders++},setInterval(){}});
vm.runInContext(html.slice(html.indexOf('let mykosStartupRefreshAt'),html.indexOf('async function mykosStageJSON')),context);
(async()=>{
 await context.mykosRefreshStartupInventory();
 await context.mykosRefreshStartupInventory();assert.equal(calls,1);
 now+=2000;await context.mykosRefreshStartupInventory();
 assert.equal(calls,2);assert.equal(renders,2);assert.equal(stats,2);assert.equal(context.nodes[0].id,'12345678');
 console.log('Startup UI refresh reads cached inventory at most every two seconds.');
})().catch(e=>{console.error(e);process.exitCode=1});
