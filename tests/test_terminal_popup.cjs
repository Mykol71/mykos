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
const terminals=[],sockets=[],events={};
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
 mykosNodeFamily:node=>node.id==='11111111'?'windows':node.id==='22222222'?'macos':'ubuntu',
 setTimeout(){},setInterval(){},alert(){throw Error('Unexpected terminal alert');},mykosRenderMykos(){},mykosRenderInventory(){}});
const run=code=>vm.runInContext(code,context);
run(source);
run("mykosOpenTerminal('host')");assert.equal(sockets.length,0,'Permission guard must remain');
run("mykosTerminalAllowed=true;mykosOpenTerminal('host')");
let dialog=body.children[0];assert.equal(dialog.tag,'dialog');assert.equal(dialog.open,true);assert.equal(dialog['aria-labelledby'],'mykos-terminal-title');
assert.equal(sockets[0].url,'wss://mykol.com/mykos/api/terminal/host');
assert.ok(terminals[0].focused);
sockets[0].readyState=1;sockets[0].onopen();terminals[0].input('test input');assert.deepEqual(sockets[0].sent,['test input']);
run("mykosOpenTerminal('01234567')");assert.equal(body.children.length,1);assert.ok(sockets[0].closed);assert.ok(terminals[0].disposed);assert.equal(sockets[0].onmessage,null);
assert.equal(sockets[1].url,'wss://mykol.com/mykos/api/terminal/01234567');
dialog=body.children[0];let prevented=false;dialog.listeners.cancel({preventDefault(){prevented=true;}});
assert.ok(prevented);assert.equal(body.children.length,0);assert.ok(sockets[1].closed);assert.ok(terminals[1].disposed);assert.ok(opener.focused);
run("mykosOpenTerminal('01234567',false,'add-vpn')");assert.equal(sockets[2].url,'wss://mykol.com/mykos/api/terminal/01234567?action=add-vpn');
body.children[0].children[0].children[1].listeners.click();assert.ok(sockets[2].closed);assert.equal(body.children.length,0);
run("mykosOpenTerminal('host')");events.pagehide();assert.ok(sockets[3].closed);assert.equal(body.children.length,0);
assert.ok(!body.children.some(item=>item.tag==='tr'));
console.log('Terminal popup checks passed: host/container targets, permission guard, input, replacement cleanup, Close, Escape, focus and page exit.');

run("mykosOpenTerminal('11111111')");assert.equal(body.children[0].children[0].children[0].textContent,'PowerShell — 11111111');
run("mykosOpenTerminal('22222222')");assert.equal(body.children[0].children[0].children[0].textContent,'macOS shell — 22222222');
