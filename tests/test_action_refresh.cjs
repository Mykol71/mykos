const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
const source=html.slice(html.indexOf('async function mykosRefreshMykos('),html.indexOf('function mykosBackupForm('));
const calls=[];let snapshots=0;
const backup={preserved:true};
const context=vm.createContext({Date,Promise,mykosLoading:false,mykosRefreshed:0,mykosData:{backups:backup},
 fetch:async(url,options)=>{calls.push([url,options?.method||'GET']);return {ok:true,json:async()=>({fresh:true})}},
 el:()=>({}),mykosRefreshSnapshots:async()=>{snapshots++},mykosRenderMykos:()=>{}});
vm.runInContext(source,context);
(async()=>{
 await context.mykosAction('repos',{action:'enable'});
 assert.deepEqual(calls,[['/mykos/api/web/mykos/repos','POST'],['/mykos/api/web/mykos/repos?refresh=true','GET']]);
 assert.equal(context.mykosData.backups,backup);assert.equal(snapshots,0);
 calls.length=0;await context.mykosRefreshMykos(true);
 assert.equal(calls.length,2);assert.equal(snapshots,1);
 console.log('Action refresh: only changed pane reloads; manual full refresh remains available.');
})().catch(error=>{console.error(error);process.exitCode=1});
