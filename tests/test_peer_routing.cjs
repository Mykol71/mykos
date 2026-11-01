const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const prefix = '/mykos/api/web/peers/' + 'a'.repeat(32) + '/proxy';
const fetched = [], sockets = [], opened = [];
class Socket { constructor(url, protocols) { sockets.push({url, protocols}); } }
const window = {fetch: (url, options) => {fetched.push({url,options});return Promise.resolve({});}, WebSocket:Socket,
 open:(url,...args)=>opened.push({url,args}), addEventListener:()=>{}};
const document = {addEventListener:()=>{}};
const context = {window,document,URL,Request,location:{href:'https://local.example/mykos/api/web/peers/id/view',origin:'https://local.example',host:'local.example'}};
const source = fs.readFileSync(path.join(__dirname,'../services/web/peer-bootstrap.js'),'utf8').replace('__MYKOS_PEER_PREFIX__',JSON.stringify(prefix));
vm.runInNewContext(source,context);
window.fetch('/mykos/api/web/host-metrics');
window.fetch('/mykos/api/web/host-history?since=123');
window.fetch(prefix+'/api/web/host/reboot',{method:'POST',body:'{"hostname":"remote"}'});
assert.equal(fetched[0].url,'https://local.example'+prefix+'/api/web/host-metrics');
assert.equal(fetched[1].url,'https://local.example'+prefix+'/api/web/host-history?since=123');
assert.equal(fetched[2].url,'https://local.example'+prefix+'/api/web/host/reboot');
assert.equal(fetched[2].options.method,'POST');
assert.equal(fetched.length,3); // Mutations are sent once, never automatically retried.
new window.WebSocket('wss://local.example/mykos/api/terminal/host');
new window.WebSocket('wss://local.example'+prefix+'/console/01234567/websockify',['binary']);
assert.equal(sockets[0].url,'wss://local.example'+prefix+'/api/terminal/host');
assert.equal(sockets[1].url,'wss://local.example'+prefix+'/console/01234567/websockify');
window.open('/mykos/api/terminal/window/host','_blank');
window.open('about:blank','_blank');
assert.equal(opened[0].url,'https://local.example'+prefix+'/api/terminal/window/host');
assert.equal(opened[1].url,'about:blank');
assert.equal(window.mykosNodeURL('/mykos/api/web/snapshot-preview/'+'b'.repeat(32)+'/page'), 'https://local.example'+prefix+'/api/web/snapshot-preview/'+'b'.repeat(32)+'/page');
console.log('Peer graph/history, HTTP mutations, terminal and console routing passed');
