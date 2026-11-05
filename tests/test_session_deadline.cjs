const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../services/web/index.html'),'utf8');
for(const tag of html.match(/<script[^>]+src="https:\/\/unpkg\.com\/[^>]+>/g))assert.match(tag,/\basync\b/,'Optional CDN scripts cannot block parsing');
for(const tag of html.match(/<link[^>]+href="https:\/\/unpkg\.com\/[^>]+>/g))assert.match(tag,/media="print"/,'Optional CDN styles cannot block session startup');
assert.match(html,/await mykosLoadCurrentUser\(controller.signal\)/);
const source=html.slice(html.indexOf('async function mykosLoadCurrentUser('),html.indexOf('// Unavailable options remain visible'));
(async()=>{
 const controller=new AbortController();let observed;
 const context=vm.createContext({window:{},fetch:(_,options)=>{observed=options.signal;return new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('deadline'))));}});
 vm.runInContext(source,context);const pending=context.mykosLoadCurrentUser(controller.signal);assert.equal(observed,controller.signal);controller.abort();await pending;
 console.log('Optional CDN resources do not block parsing; current-user fetch shares the startup deadline and recovers on abort.');
})().catch(error=>{console.error(error);process.exitCode=1;});
