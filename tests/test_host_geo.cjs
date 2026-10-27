const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
const html=fs.readFileSync(path.join(__dirname,'../services/web/index.html'),'utf8');
class Element {constructor(tag){this.tag=tag;this.children=[];this.textContent='';}append(...children){this.children.push(...children);}set innerHTML(_){throw Error('Hover metadata must use safe text nodes');}}
const points=[],removed=[];let maps=0;
const map={setView(){return this;},removeLayer(marker){removed.push(marker);}};
const L={map(){maps++;return map;},tileLayer(){return {addTo(){}};},circleMarker(coords,options){const marker={coords,options,addTo(){return this;},bindTooltip(content,settings){this.content=content;this.tooltip=settings;return this;}};points.push(marker);return marker;}};
const context=vm.createContext({window:{L,mykosGeoCurrentHost:{hostname:'host.example',status:'online',os:'RHEL',version:'9.8'}},L,map:null,markers:[],
 nodes:[{id:'12345678',lat:40,lon:-90,status:'online',shop_name:'<img src=x>',pos_type:'rti',app_info:'Application info'},
 {id:'22222222',lat:41,lon:-91,status:'offline'},{id:'33333333',lat:42,lon:-92,status:'unknown'},{id:'44444444',lat:'',lon:''}],
 mykosGeoHosts:[{hostname:'host.example',lat:35,lon:-95},null],mykosListDetails:{'12345678':{os_type:'RHEL',os_version:'8.10'}},mykosListMemory:{'12345678':{ipv4:'192.168.22.5'}},document:{createElement:tag=>new Element(tag)}});
vm.runInContext(html.slice(html.indexOf('function mykosHasGeo('),html.indexOf('function topology()')),context);
context.drawMap();assert.equal(points.length,4);assert.equal(maps,1);
assert.deepEqual(points.slice(0,3).map(marker=>marker.options.fillColor),['#22c55e','#ef4444','#facc15']);
assert.equal(points[3].options.radius,9);assert.equal(points[3].options.fillColor,'#22c55e');
assert.ok(points[0].content.children.some(child=>child.textContent==='Shop: <img src=x>'));
assert.ok(points[0].content.children.some(child=>child.textContent==='POS: rti'));
assert.ok(points[0].content.children.some(child=>child.textContent==='OS: RHEL 8.10'));
assert.equal(points[0].tooltip.sticky,true);
context.drawMap();assert.equal(maps,1);assert.equal(removed.length,4,'Refresh reuses the same map instance');
const mapElement={},home={append(element){this.restored=element;}},row={dataset:{hostDiagnostic:'geo'},remove(){this.removed=true;}};
context.mykosGeoHome=home;context.el=()=>mapElement;
vm.runInContext(html.slice(html.indexOf('function mykosCloseHostDiagnostic('),html.indexOf('async function mykosShowHostDiagnostic(')),context);
context.mykosCloseHostDiagnostic(row);assert.equal(home.restored,mapElement);assert.equal(row.removed,true);
assert.match(html,/data-host-action="geo"/);
console.log('Host geographic map: ID/host points, safe hover metadata, green/red/yellow status, valid coordinates, map reuse and close cleanup passed.');
