const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
const escape=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const cache={
 '12345678':{networks:[{name:'mykos-lan',address:'192.168.22.134',prefix:24}],vpn_topology:{links:[{name:'office',peer:'vpn.example',remote:'10.77.0.0/24',mode:'direct'}],via_gateways:[]},vpn:{established_connections:['office']}},
 '87654321':{networks:[{name:'mykos-lan'}],vpn_topology:{links:[],via_gateways:[]}},
 '00000000':{networks:[{name:'unrelated'}],vpn_topology:{links:[],via_gateways:[]}}
};
const context=vm.createContext({escape,mykosListMemory:cache,nodes:Object.keys(cache).map(id=>({id,vpn:'up'}))});
vm.runInContext(html.slice(html.indexOf('function mykosVpnGraph('),html.indexOf('\nfunction table(){')),context);
let graph=context.mykosVpnGraph('12345678');
assert.match(graph,/vpn.example/);assert.match(graph,/192\.168\.22\.0\/24/);assert.match(graph,/10\.77\.0\.0\/24/);assert.match(graph,/font-size="9"/);assert.match(graph,/>office<\/text>/);assert.match(graph,/stroke="#4ade80"/);
cache['12345678'].vpn.established_connections=[];assert.doesNotMatch(context.mykosVpnGraph('12345678'),/stroke="#4ade80"/);cache['12345678'].vpn.established_connections=['office'];assert.doesNotMatch(graph,/87654321|00000000/);
graph=context.mykosVpnGraph('12345678',true);
assert.match(graph,/87654321/);assert.doesNotMatch(graph,/00000000/);
assert.match(graph,/reachability not tested/);assert.match(graph,/style="color:#94a3b8"/);assert.match(graph,/style="color:#38bdf8"/);assert.match(graph,/stroke-dasharray/);
cache['12345678'].vpn_topology.links[0].peer='<img src=x onerror=alert(1)>';
assert.doesNotMatch(context.mykosVpnGraph('12345678'),/<img/);
assert.match(context.mykosVpnGraph('12345678'),/&lt;img/);
// The IP action has its own expansion mode; general ID details are retained.
assert.match(html,/class="mykos-network-button"/);
assert.match(html,/mykosToggleShop\(networkButton.dataset.shop,'network'\)/);
assert.match(html,/mykosExpandShop\(mykosSelectedShop, mykosSelectedDetailMode\)/);
assert.match(html,/function mykosToggleShop\(shop, mode = 'details'\)/);
assert.doesNotMatch(html,/function mykosOpenVpnTopology/);
assert.match(html,/tr.addEventListener\('click',event=>event.stopPropagation\(\)\)/);
console.log('Network topology optional guest membership, separation, escaping and entry-point checks passed.');

// Per-ID choice survives a new page context, with safe fallback if storage is blocked.
const preferences=html.slice(html.indexOf('const mykosNetworkPeersHidden ='),html.indexOf('function mykosSection('));
const stored=new Map(),storage={getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,value)};
const prefs=vm.createContext({localStorage:storage});vm.runInContext(preferences,prefs);
assert.equal(prefs.mykosNetworkPeersShown('12345678'),true);
prefs.mykosSaveNetworkPeers('12345678',false);
const reopened=vm.createContext({localStorage:storage});vm.runInContext(preferences,reopened);
assert.equal(reopened.mykosNetworkPeersShown('12345678'),false);
assert.equal(reopened.mykosNetworkPeersShown('87654321'),true);
reopened.mykosSaveNetworkPeers('12345678',true);
assert.equal(reopened.mykosNetworkPeersShown('12345678'),true);
const blocked=vm.createContext({localStorage:{getItem(){throw Error('blocked')},setItem(){throw Error('blocked')}}});vm.runInContext(preferences,blocked);
blocked.mykosSaveNetworkPeers('12345678',false);assert.equal(blocked.mykosNetworkPeersShown('12345678'),false);
console.log('Per-ID network peer preference persistence and storage fallback passed.');
// Reuse each pane's existing OS logo, including SVG and embedded PNG vendors.
let logo={tagName:'svg',innerHTML:'<path data-test-os="redhat" d="M0 0h24v24H0z"/>',getAttribute:key=>({viewBox:'0 0 24 24',fill:'currentColor'}[key]??null)};
context.document={getElementById:()=>({querySelector:()=>logo})};
context.mykosNodeFamily=()=> 'linux';context.mykosOsId=(family,id)=>id;context.getComputedStyle=()=>({color:'rgb(238, 0, 0)'});
let branded=context.mykosVpnGraph('12345678',true);
assert.match(branded,/<svg x="-6" y="-8" width="12" height="12"/);assert.match(branded,/data-test-os="redhat"/);
assert.match(branded,/fill="#facc15"/);assert.match(branded,/filter:grayscale\(1\)/);
logo={tagName:'img',getAttribute:()=> 'data:image/png;base64,cHJldmlldw=='};
assert.match(context.mykosVpnGraph('12345678'),/<image x="-6" y="-8" width="12" height="12" href="data:image\/png;base64,/);
logo={tagName:'img',getAttribute:()=> 'https://untrusted.invalid/logo.png'};
assert.doesNotMatch(context.mykosVpnGraph('12345678'),/untrusted.invalid/);
console.log('Embedded vendor OS icons, muted inactive icons, and yellow node labels passed.');
