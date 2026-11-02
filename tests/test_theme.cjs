const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
for(const saved of [null,'dark','light','receiver','invalid']) {
 const make=()=>({attrs:{},setAttribute(k,v){this.attrs[k]=v},addEventListener(k,fn){this[k]=fn}});
 const controls=[make(),make()],selectors=[make(),make()];const root={dataset:{}};
 const storage={value:saved,getItem(){return this.value},setItem(k,v){this.value=v}};
 const context=vm.createContext({window:{},document:{documentElement:root,querySelectorAll(q){return q==='.mykos-theme-select'?selectors:controls}},localStorage:storage});
 vm.runInContext(scripts.find(s=>s.includes('Apply the saved theme')),context);
 vm.runInContext(scripts.find(s=>s.includes('MYKOS THEME CONTROL')),context);
 const initial=['dark','light','receiver'].includes(saved)?saved:'dark';assert.equal(root.dataset.theme,initial);
 for(let i=0;i<3;i++){controls[i%2].click();assert.equal(storage.value,root.dataset.theme);for(const selector of selectors)assert.equal(selector.value,root.dataset.theme);}
 assert.equal(root.dataset.theme,initial,'full leaf cycle returns to starting theme');
 selectors[0].value='receiver';selectors[0].change();assert.equal(root.dataset.theme,'receiver');assert.equal(selectors[1].value,'receiver');assert.match(controls[1].attrs['aria-label'],/Receiver theme; switch to dark/);
 selectors[1].value='light';selectors[1].change();assert.equal(root.dataset.theme,'light');
 selectors[0].value='invalid';selectors[0].change();assert.equal(root.dataset.theme,'light');
}
// Receiver meter values must be real, bounded visually, stale-aware and independent of lifecycle controls.
const face={dataset:{},needle:{style:{}},output:{},svg:{setAttribute(k,v){this[k]=v}},querySelector(q){return q==='.mykos-receiver-needle'?this.needle:q==='output'?this.output:this.svg}};
const context=vm.createContext({window:{},document:{documentElement:{dataset:{theme:'receiver'}},getElementById(id){return id==='mykos-receiver-cpu'?face:null}}});
const meter=scripts.find(s=>s.includes('MYKOS THEME CONTROL')).split('// Receiver meters')[1];
vm.runInContext('// Receiver meters'+meter,context);
context.window.mykosReceiverReading('cpu',45);assert.equal(face.output.textContent,'45%');assert.equal(face.needle.style.transform,'rotate(-6deg)');
context.window.mykosReceiverReading('cpu',150);assert.equal(face.output.textContent,'150%');assert.equal(face.needle.style.transform,'rotate(60deg)');
context.window.mykosReceiverReading('cpu',null);assert.equal(face.dataset.available,'false');assert.equal(face.output.textContent,'—');
assert.match(html,/input\[type="range"\]::-webkit-slider-thumb/);assert.match(html,/input\[type="range"\]::-moz-range-thumb/);
console.log('Three themes: saved/default, leaf cycle, selectors, real/stale/overload VU readings passed.');
