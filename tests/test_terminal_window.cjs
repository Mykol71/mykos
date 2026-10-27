const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
const html=fs.readFileSync(path.join(__dirname,'../services/web/terminal-window.html'),'utf8');
const script=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
function client(shop,username='mgreen',action='shell') {
 const elements=new Map(),events={},sockets=[],terms=[],timers=[];let access={allowed:true,username};
 const el=id=>{if(!elements.has(id))elements.set(id,{id,value:'',textContent:'',disabled:false,hidden:false,open:false,handlers:{},addEventListener(name,fn){this.handlers[name]=fn;},showModal(){this.open=true;},close(){this.open=false;},focus(){}});return elements.get(id);};
 el('terminal-config').textContent=JSON.stringify({shop,username,action,title:'root terminal — '+shop,run_sshbbx:false});
 class Terminal {constructor(){this.writes=[];terms.push(this);}open(){}focus(){}write(data){assert.ok(!this.disposed);this.writes.push(data);}onData(fn){this.input=fn;}dispose(){this.disposed=true;}}
 class WebSocket {static OPEN=1;constructor(url){this.url=String(url);sockets.push(this);this.readyState=1;}send(data){(this.sent??=[]).push(data);}close(){this.closed=true;}}
 const window={Terminal,location:{href:'https://mykol.com/mykos/api/terminal/window/'+shop},addEventListener:(name,fn)=>events[name]=fn,close(){this.closed=true;}};
 const requests=[];
 const context=vm.createContext({document:{getElementById:el},window,location:{protocol:'https:'},Terminal,WebSocket,URL,ArrayBuffer,Uint8Array,AbortController,confirm:()=>true,
  setTimeout(){return 1;},clearTimeout(){},setInterval(fn){timers.push(fn);return timers.length;},clearInterval(){},
  fetch:async(url,options)=>{requests.push([url,options]);return {ok:true,json:async()=>url.includes('/permission')?access:{text:'private notes',revision:4}};}});
 vm.runInContext(script,context);
 return {context,el,events,sockets,terms,window,requests,setAccess:state=>access=state,run:code=>vm.runInContext(code,context)};
}
(async()=>{
 const first=client('12345678'),second=client('87654321','operator');
 assert.equal(first.sockets.length,1);assert.equal(second.sockets.length,1);
 assert.match(first.sockets[0].url,/terminal\/12345678$/);assert.match(second.sockets[0].url,/terminal\/87654321$/);
 first.sockets[0].onopen();first.terms[0].input('one');assert.deepEqual(first.sockets[0].sent,['one']);
 assert.equal(first.el('notes-button').hidden,false);assert.equal(second.el('notes-button').hidden,true);
 first.el('notes').showModal();await first.run('loadNotes()');assert.equal(first.el('notes-text').value,'private notes');assert.ok(!first.sockets[0].closed);
 first.events.pagehide();assert.ok(first.sockets[0].closed);assert.ok(first.terms[0].disposed);assert.ok(!second.sockets[0].closed);
 second.setAccess({allowed:false,username:'operator'});await second.run('permission()');assert.ok(second.sockets[0].closed);
 const switched=client('11111111');switched.setAccess({allowed:true,username:'another-admin'});await switched.run('permission()');assert.ok(switched.sockets[0].closed);
 const base=client('rh9','mgreen','image-shell');assert.equal(base.el('base-notice').hidden,false);assert.match(base.sockets[0].url,/action=image-shell/);base.events.pagehide();assert.ok(base.sockets[0].closed);
 assert.ok(second.requests.every(([url])=>!url.includes('host-metrics')&&!url.endsWith('/nodes')));
 console.log('Independent terminal windows: input, private notes, separate cleanup, session revocation/user switch and image-session cleanup passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
