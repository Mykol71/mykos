const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.isConnected=false;this.open=false;}
 setAttribute(key,value){this[key]=value;}
 append(...children){for(const child of children){child.parent=this;child.isConnected=this.isConnected;this.children.push(child);}}
 addEventListener(name,fn){this.listeners[name]=fn;}
 showModal(){this.open=true;}
 close(){this.open=false;this.listeners.close?.();}
 remove(){this.isConnected=false;if(this.parent)this.parent.children=this.parent.children.filter(item=>item!==this);}
 focus(){this.focused=true;}
}
const terminals=[],sockets=[],events={},notesOpened=[];
class Terminal {
 constructor(){this.writes=[];terminals.push(this);}
 open(surface){this.surface=surface;}
 write(value){assert.ok(!this.disposed,'No writes after terminal disposal');this.writes.push(value);}
 onData(fn){this.input=fn;}
 focus(){this.focused=true;}
 dispose(){this.disposed=true;}
}
class WebSocket {
 static OPEN=1;
 constructor(url){this.url=String(url);this.sent=[];sockets.push(this);}
 send(value){this.sent.push(value);}
 close(){this.closed=true;this.onclose?.();}
}
const body=new Element('body');body.isConnected=true;
const opener=new Element('button');opener.isConnected=true;
const document={body,activeElement:opener,createElement:tag=>new Element(tag)};
const window={Terminal,location:{href:'https://mykol.com/mykos/',protocol:'https:'},addEventListener:(name,fn)=>events[name]=fn};
const source=html.slice(html.indexOf('// MYKOS INTERACTIVE TERMINAL V1'),html.indexOf('</script>',html.indexOf('// MYKOS INTERACTIVE TERMINAL V1')));
const context=vm.createContext({document,window,Terminal,WebSocket,URL,ArrayBuffer,Uint8Array,
 mykosNotesButton:(pane,label)=>{const b=new Element('button');b.notePane=pane;b.onclick=()=>notesOpened.push([pane,label]);return b;},
 mykosImageFamily:os=>os==='rh9'?'linux':os.startsWith('win')?'windows':'macos',
 mykosNodeFamily:node=>node.id==='11111111'?'windows':node.id==='22222222'?'macos':'ubuntu',
 setTimeout(){},setInterval(){},alert(){throw Error('Unexpected terminal alert');},mykosRenderMykos(){},mykosRenderInventory(){}});
const run=code=>vm.runInContext(code,context);
run(source);
run("mykosOpenTerminal('host')");assert.equal(sockets.length,0,'Permission guard must remain');
run("mykosTerminalAllowed=true;mykosOpenTerminal('host')");
let dialog=body.children[0];assert.equal(dialog.tag,'dialog');assert.equal(dialog.open,true);assert.equal(dialog['aria-labelledby'],'mykos-terminal-title');
assert.equal(sockets[0].url,'wss://mykol.com/mykos/api/terminal/host');
assert.ok(terminals[0].focused);
const notes=dialog.children[0].children[1];assert.equal(notes.notePane,'terminal');notes.onclick();assert.deepEqual(notesOpened,[['terminal','Terminal']]);assert.equal(dialog.open,true);assert.ok(!sockets[0].closed);assert.ok(!terminals[0].disposed,'Opening notes must preserve the terminal session');
sockets[0].readyState=1;sockets[0].onopen();terminals[0].input('test input');assert.deepEqual(sockets[0].sent,['test input']);
run("mykosOpenTerminal('01234567')");assert.equal(body.children.length,1);assert.ok(sockets[0].closed);assert.ok(terminals[0].disposed);assert.equal(sockets[0].onmessage,null);
assert.equal(sockets[1].url,'wss://mykol.com/mykos/api/terminal/01234567');
dialog=body.children[0];let prevented=false;dialog.listeners.cancel({preventDefault(){prevented=true;}});
assert.ok(prevented);assert.equal(body.children.length,0);assert.ok(sockets[1].closed);assert.ok(terminals[1].disposed);assert.ok(opener.focused);
run("mykosOpenTerminal('01234567',false,'add-vpn')");assert.equal(sockets[2].url,'wss://mykol.com/mykos/api/terminal/01234567?action=add-vpn');
body.children[0].children[0].children.at(-1).listeners.click();assert.ok(sockets[2].closed);assert.equal(body.children.length,0);
run("mykosOpenTerminal('host')");events.pagehide();assert.ok(sockets[3].closed);assert.equal(body.children.length,0);
assert.ok(!body.children.some(item=>item.tag==='tr'));
console.log('Terminal popup checks passed: host/container targets, permission guard, input, replacement cleanup, Close, Escape, focus and page exit.');

run("mykosOpenTerminal('11111111')");assert.equal(body.children[0].children[0].children[0].textContent,'PowerShell — 11111111');
run("mykosOpenTerminal('22222222')");assert.equal(body.children[0].children[0].children[0].textContent,'macOS shell — 22222222');

run("mykosOpenTerminal('rh9',false,'image-shell')");
assert.equal(sockets.at(-1).url,'wss://mykol.com/mykos/api/terminal/rh9?action=image-shell');

// Successful same-browser windows do not replace an existing main-page session.
const popouts=[];context.window.open=(url,name,features)=>{const popup={focus(){this.focused=true;}};popouts.push({url,name,features,popup});return popup;};
const prior=sockets.length;
run("mykosOpenTerminal('01234567');mykosOpenTerminal('87654321')");
assert.equal(popouts.length,2);assert.equal(sockets.length,prior);
assert.match(popouts[0].url,/\/mykos\/api\/terminal\/window\/01234567/);
assert.match(popouts[1].url,/87654321/);assert.equal(popouts[0].name,'_blank');
assert.ok(popouts.every(item=>item.popup.opener===null && item.popup.focused));
context.window.open=()=>null;run("mykosOpenTerminal('host')");assert.equal(sockets.length,prior+1,'Blocked popup keeps the inline fallback');
console.log('Separate same-browser windows and blocked-popup fallback passed.');

(async()=>{
 const host={host:{vpn:'not_configured'}},renders=[];let requests=0;
 const permissions=vm.createContext({document:{readyState:'complete'},fetch:async()=>{requests++;return {ok:true,json:async()=>({allowed:true})};},
  mykosTerminalAllowed:false,mykosHostStatusData:host,mykosRenderHostStatus:data=>renders.push(data),
  mykosLoadStageImages(){},mykosCloseTerminal(){},mykosRenderMykos(){},mykosRenderSnapshots(){},mykosRenderInventory(){}});
 const check=html.slice(html.indexOf('async function mykosCheckTerminalPermission()'),html.indexOf('setTimeout(mykosCheckTerminalPermission'));
 vm.runInContext(check,permissions);
 await Promise.all([vm.runInContext('mykosCheckTerminalPermission()',permissions),vm.runInContext('mykosCheckTerminalPermission()',permissions)]);
 assert.equal(requests,1);assert.deepEqual(renders,[host]);
 console.log('Host icons redraw on permission arrival using cached data, without duplicate permission calls.');
})().catch(error=>{console.error(error);process.exitCode=1;});
