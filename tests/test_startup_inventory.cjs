const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
let now=10000,calls=0,finish;
const context=vm.createContext({Date:{now:()=>now},Map,mykosLoadInventory:()=>{calls++;return new Promise(resolve=>finish=resolve)}});
const start=html.indexOf('function load(force=false)'),end=html.indexOf('async function mykosLoadInventory()',start);
vm.runInContext('let mykosInventoryRequest=null,mykosInventoryCompletedAt=0;'+html.slice(start,end),context);
(async()=>{
 const first=context.load(),second=context.load(true);assert.equal(first,second);assert.equal(calls,1);finish();await first;
 await context.load();assert.equal(calls,1);now+=2000;const third=context.load();assert.equal(calls,2);finish();await third;
 vm.runInContext(html.slice(html.indexOf('async function mykosRefreshStartupInventory()'),html.indexOf('\nsetInterval(()=>{',html.indexOf('async function mykosRefreshStartupInventory()'))),context);
 const fourth=context.mykosRefreshStartupInventory();assert.equal(calls,3);finish();await fourth;
 console.log('Shared inventory refresh coalesces concurrent calls, bounds repeats and supports explicit refresh.');
})().catch(e=>{console.error(e);process.exitCode=1});
