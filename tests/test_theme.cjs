const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
for(const saved of [null,'dark','light','invalid']) {
 const control={attrs:{},setAttribute(k,v){this.attrs[k]=v},addEventListener(k,fn){this[k]=fn}};
 const root={dataset:{}}; const storage={value:saved,getItem(){return this.value},setItem(k,v){this.value=v}};
 const context=vm.createContext({document:{documentElement:root,getElementById(){return control}},localStorage:storage});
 vm.runInContext(scripts.find(s=>s.includes('Apply the saved theme')),context);
 vm.runInContext(scripts.find(s=>s.includes('MYKOS THEME CONTROL')),context);
 assert.equal(root.dataset.theme,saved==='light'?'light':'dark');
 const previous=root.dataset.theme;control.click();assert.notEqual(root.dataset.theme,previous);
 assert.equal(storage.value,root.dataset.theme);assert.equal(control.attrs['aria-pressed'],String(root.dataset.theme==='light'));
 control.click();assert.equal(root.dataset.theme,previous);
}
assert.ok(html.indexOf('id="mykos-theme-toggle"')<html.indexOf('class="mykos-footer-email"'));
console.log('Theme control: dark default, saved preference, toggle, accessible label and footer order passed.');
