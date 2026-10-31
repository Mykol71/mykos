const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(x=>x[1]);
for(const saved of [null,'on','off','invalid']) {
 const attrs={},events=[],stored=[];let click;
 const root={dataset:{}};
 const control={setAttribute(k,v){attrs[k]=v;},addEventListener(name,fn){assert.equal(name,'click');click=fn;}};
 const ctx=vm.createContext({document:{documentElement:root,getElementById:id=>{assert.equal(id,'mykos-map-toggle');return control;}},localStorage:{getItem:k=>k==='mykos-map-background'?saved:'light',setItem:(k,v)=>stored.push([k,v])},window:{dispatchEvent:e=>events.push(e.type)},Event:class{constructor(type){this.type=type;}}});
 vm.runInContext(scripts.find(s=>s.includes('Apply the saved theme')),ctx);
 vm.runInContext(scripts.find(s=>s.includes('MYKOS BACKGROUND MAP CONTROL')),ctx);
 assert.equal(root.dataset.mykosMapBackground,saved==='on'?'on':'off');
 assert.equal(attrs['aria-pressed'],String(saved==='on'));
 click();assert.equal(root.dataset.mykosMapBackground,saved==='on'?'off':'on');
 assert.equal(stored[0][0],'mykos-map-background');assert.equal(stored[0][1],root.dataset.mykosMapBackground);
 assert.equal(root.dataset.theme,'light');assert.equal(events[0],'mykos-map-background-change');
 click();assert.equal(root.dataset.mykosMapBackground,saved==='on'?'on':'off');
 ctx.localStorage.setItem=()=>{throw Error('Storage blocked');};click();
 assert.equal(attrs['aria-pressed'],String(root.dataset.mykosMapBackground!=='off'));
}
assert.match(html,/\| <button id="mykos-map-toggle" type="button"/);
assert.match(html,/data-mykos-map-background="off"[^}]+#mykos-geo-background,#mykos-location-layer[^}]+display:none !important/);
assert.match(html,/addEventListener\('mykos-map-background-change',updateBackgroundMap\)/);
console.log('Footer map leaf toggles backdrop and bubbles, persists preference, preserves theme and supports blocked storage.');
