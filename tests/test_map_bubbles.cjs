const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync(require('path').join(__dirname,'../services/web/index.html'),'utf8');
class Element {constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={setProperty(k,v){this[k]=v;}};}append(...items){for(const item of items){this.children.push(item);item.parent=this;}}setAttribute(){}replaceChildren(...items){this.children=items;}getBoundingClientRect(){return {width:220,height:95};}remove(){this.parent.children=this.parent.children.filter(p=>p!==this);}set innerHTML(_){throw Error('Must render safe text');}}
const layer=new Element('div');let parent={id:'mykos-geo-background'},points=[{kind:'router',serial:'MX',shop_name:'Flowers',router_name:'MX router',address:'Main St',lat:36,lon:-90,status:'down',peer:'203.0.113.1'}];
const ctx=vm.createContext({document:{getElementById:()=>layer,createElement:tag=>new Element(tag)},map:{getContainer:()=>({parentElement:parent}),getSize:()=>({x:1000,y:700}),latLngToContainerPoint:()=>({x:500,y:350})},mykosGeoPoints:()=>points,mykosHasGeo:()=>true});
vm.runInContext(html.slice(html.indexOf('// MYKOS PASSIVE LOCATION BUBBLES'),html.indexOf('// END MYKOS PASSIVE LOCATION BUBBLES')),ctx);
ctx.mykosSyncLocationPins();assert.equal(layer.hidden,false);assert.equal(layer.children.length,2);assert.ok(layer.children.every(p=>p.tag!=='button'));assert.equal(layer.children[0].dataset.status,'down');
const bubble=layer.children[1];assert.equal(bubble.style.top,'239px');assert.ok(bubble.children.some(p=>p.textContent==='Main St'));
points=[{...points[0],status:'up'}];ctx.mykosSyncLocationPins();assert.equal(layer.children[1],bubble);assert.equal(bubble.dataset.status,'up');
points=[{...points[0],serial:'MX1',shop_name:'Site One'},{...points[0],serial:'MX2',shop_name:'Site Two',status:'unknown'}];ctx.mykosSyncLocationPins();const grouped=layer.children.find(p=>p.children.some(c=>c.textContent==='2 Meraki sites'));assert.ok(grouped);assert.ok(grouped.children.some(c=>c.textContent==='Site One'));assert.ok(grouped.children.some(c=>c.textContent==='Site Two'));
parent={id:'details'};ctx.mykosSyncLocationPins();assert.equal(layer.hidden,true);
assert.doesNotMatch(html,/mykos-location-close|mykos-location-popover|mykosLocationLocked/);assert.match(html,/#mykos-location-layer \*[^}]+pointer-events:none !important/);
console.log('Always-visible passive bubbles: full metadata, positioning, red/down to green/up refresh, no click targets, detail-view cleanup passed.');

parent={id:'mykos-geo-background'};points=[{kind:'host',hostname:'prod.example',name:'MYKOS',address:'Oklahoma City, OK',lat:36,lon:-90,status:'online'},points[0]];
ctx.mykosSyncLocationPins();const hostBubble=layer.children.find(p=>p.children.some(c=>c.textContent==='MYKOS'));
assert.equal(hostBubble.dataset.placement,'below');assert.ok(hostBubble.children.some(c=>c.textContent==='Oklahoma City, OK'));
console.log('Co-located MYKOS host label stays below its router label.');
