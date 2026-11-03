const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
for(const saved of [null,'dark','light','receiver','invalid']) {
 const make=()=>({attrs:{},setAttribute(k,v){this.attrs[k]=v},addEventListener(k,fn){this[k]=fn}});
 const controls=[make(),make()],selectors=[make(),make()];selectors.forEach(s=>{s.receiver={hidden:true,disabled:true};s.querySelector=()=>s.receiver;});const root={dataset:{}};
 const storage={values:{'mykos-theme':saved},getItem(k){return this.values[k]??null},setItem(k,v){this.values[k]=v}};
 const context=vm.createContext({window:{},document:{documentElement:root,querySelectorAll(q){return q==='.mykos-theme-select'?selectors:controls}},localStorage:storage});
 vm.runInContext(scripts.find(s=>s.includes('Apply the saved theme')),context);vm.runInContext(scripts.find(s=>s.includes('MYKOS THEME CONTROL')),context);
 const initial=['dark','light'].includes(saved)?saved:'dark';assert.equal(root.dataset.theme,initial,'Saved Receiver cannot flash before identity verification');assert.ok(selectors.every(s=>s.receiver.hidden&&s.receiver.disabled));
 for(let i=0;i<2;i++)controls[i].click();assert.equal(root.dataset.theme,initial,'Anonymous cycle has only Dark and Light');selectors[0].value='receiver';selectors[0].change();assert.equal(root.dataset.theme,initial,'Anonymous direct Receiver selection rejected');
 context.window.mykosSetThemeIdentity('operator');assert.ok(selectors.every(s=>s.receiver.hidden));selectors[0].value='receiver';selectors[0].change();assert.notEqual(root.dataset.theme,'receiver','Other admin identity has no Receiver');
 context.window.mykosSetThemeIdentity('mgreen');assert.ok(selectors.every(s=>!s.receiver.hidden&&!s.receiver.disabled));selectors[0].value='receiver';selectors[0].change();assert.equal(root.dataset.theme,'receiver');assert.equal(storage.values['mykos-theme:mgreen'],'receiver');
 const generation=context.window.mykosThemeIdentityGeneration;context.window.mykosClearThemeIdentity();assert.equal(context.window.mykosThemeIdentityGeneration,generation+1);assert.equal(root.dataset.theme,'dark');assert.ok(selectors.every(s=>s.receiver.hidden));
 context.window.mykosSetThemeIdentity('reader');selectors[0].value='light';selectors[0].change();assert.equal(storage.values['mykos-theme:reader'],'light');context.window.mykosSetThemeIdentity('mgreen');assert.equal(root.dataset.theme,'receiver','Owner preference survives another user choosing Light');context.window.mykosSetThemeIdentity('reader');assert.equal(root.dataset.theme,'light');
 for(const name of ['MGREEN','mgreen2','admin','peer:mgreen','',null])assert.equal(context.mykosAvailableThemes(name).includes('receiver'),false);assert.equal(context.mykosAvailableThemes('mgreen').includes('receiver'),true);
}
assert.match(html,/themeGeneration===window\.mykosThemeIdentityGeneration/);assert.match(html,/r\.status === 401[\s\S]*?mykosClearThemeIdentity/);
// Receiver meter values must be real, bounded visually, stale-aware and independent of lifecycle controls.
const face={dataset:{},needle:{style:{}},output:{},svg:{setAttribute(k,v){this[k]=v}},querySelector(q){return q==='.mykos-receiver-needle'?this.needle:q==='output'?this.output:this.svg}};
const context=vm.createContext({window:{},document:{documentElement:{dataset:{theme:'receiver'}},getElementById(id){return id==='mykos-receiver-cpu'?face:null}}});
const meter=scripts.find(s=>s.includes('MYKOS THEME CONTROL')).split('// Receiver meters')[1];
vm.runInContext('// Receiver meters'+meter,context);
context.window.mykosReceiverReading('cpu',45);assert.equal(face.output.textContent,'45%');assert.equal(face.needle.style.transform,'rotate(-6deg)');
context.window.mykosReceiverReading('cpu',150);assert.equal(face.output.textContent,'150%');assert.equal(face.needle.style.transform,'rotate(60deg)');
context.window.mykosReceiverReading('cpu',null);assert.equal(face.dataset.available,'false');assert.equal(face.output.textContent,'—');
assert.match(html,/input\[type="range"\]::-webkit-slider-thumb/);assert.match(html,/input\[type="range"\]::-moz-range-thumb/);
console.log('Personal Receiver: identity-only availability, signed-out fallback, selector rejection, per-user preference restoration, logout generation and real VU readings passed.');
