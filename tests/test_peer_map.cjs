const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../services/web/peer-ui.js'),'utf8');
const root={dataset:{theme:'dark',mykosMapBackground:'on'}},childRoot={dataset:{}},events={};let mutation,draws=0;
const child={mykosGeoCurrentHost:{hostname:'peer.example',internal_ip:'192.0.2.2'},mykosGeoPoints:()=>[{kind:'host',hostname:'peer.example',lat:36,lon:-90}]};
const item={frame:{contentDocument:{documentElement:childRoot},contentWindow:child},peer:{id:'peer1',name:'peer.example',ip:'192.0.2.2',state:'active',connected:true}};
const frames=new Map([['peer1',item]]),window={drawMap:()=>{draws++;},addEventListener:(event,fn)=>{events[event]=fn;}};
const context=vm.createContext({window,document:{documentElement:root},frames,MutationObserver:class{constructor(fn){mutation=fn;}observe(){}}});
vm.runInContext(source.slice(source.indexOf('    function syncAppearance('),source.indexOf('    function attachActions(')),context);
context.syncAppearance(item);assert.equal(childRoot.dataset.mykosMapBackground,'on');
root.dataset.mykosMapBackground='off';events['mykos-map-background-change']();assert.equal(childRoot.dataset.mykosMapBackground,'off');
root.dataset.theme='light';mutation();assert.equal(childRoot.dataset.theme,'light');
context.updateMapPeers();assert.equal(window.mykosGeoPeers.length,1);assert.equal(window.mykosGeoPeers[0].lat,36);assert.equal(window.mykosGeoPeers[0].status,'up');
child.mykosGeoPoints=()=>[];context.updateMapPeers();assert.equal(window.mykosGeoPeers.length,1);assert.equal(window.mykosGeoPeers[0].lat,undefined,'No coordinates are invented');
item.peer.connected=false;context.updateMapPeers();assert.equal(window.mykosGeoPeers.length,0);
item.peer.connected=true;item.peer.state='pending';context.updateMapPeers();assert.equal(window.mykosGeoPeers.length,0);
assert.ok(draws>=4);
console.log('Peer iframe inherits live map/theme changes; connected map metadata, missing coordinates and disconnect cleanup passed.');

// Compact frame height must shrink to the actual strip without accepting foreign messages.
item.frame.style={};context.location={origin:'https://owner.example'};
const resizeStart=source.indexOf("    window.addEventListener('message'");
vm.runInContext(source.slice(resizeStart,source.indexOf('    refresh(); window.setInterval',resizeStart)),context);
const resize={origin:'https://owner.example',source:child,data:{type:'mykos-peer-height',prefix:'/mykos/api/web/peers/peer1/proxy',height:72}};
events.message(resize);assert.equal(item.frame.style.height,'72px');
events.message({...resize,origin:'https://foreign.example',data:{...resize.data,height:400}});assert.equal(item.frame.style.height,'72px');
events.message({...resize,source:{},data:{...resize.data,height:400}});assert.equal(item.frame.style.height,'72px');
events.message({...resize,data:{...resize.data,prefix:'/wrong',height:400}});assert.equal(item.frame.style.height,'72px');
events.message({...resize,data:{...resize.data,height:4000}});assert.equal(item.frame.style.height,'1800px');
events.message({...resize,data:{...resize.data,height:0}});assert.equal(item.frame.style.height,'60px');
events.message({...resize,data:{...resize.data,height:NaN}});assert.equal(item.frame.style.height,'60px');
console.log('Compact peer frame shrink, bounds and origin/source/prefix guards passed.');

const classes=new Set(),attrs={};let moved=0,resized=0;
const main={},pane={after:footer=>{moved++;footer.parentElement=main;}},footer={parentElement:pane};
const compactItem={frame:{contentDocument:{documentElement:{classList:{contains:v=>classes.has(v),add:v=>classes.add(v)}},getElementById:id=>({'mykos-host-pane':pane,'mykos-host-hardware':footer,'mykos-host-chart':{setAttribute:(k,v)=>attrs[k]=v}}[id])},contentWindow:{mykosScheduleHostChart:()=>resized++}}};
context.compactPane(compactItem);context.compactPane(compactItem);
assert.equal(footer.parentElement,main);assert.equal(moved,1);assert.equal(resized,1);assert.equal(attrs.tabindex,'-1');
console.log('Peer gray footer moves outside once, preserving chart accessibility and resize behavior.');
