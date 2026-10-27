const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
class Element {
 constructor(){this.children=[];this.style={};this.dataset={};this.attrs={};this.value='';}
 append(...items){for(const item of items){this.children.push(item);if(item.value&&!this.value)this.value=item.value;}}
 replaceChildren(){this.children=[];}
 setAttribute(k,v){this.attrs[k]=v;}
}
const elements=new Map(),el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
let visible;
const context=vm.createContext({el,document:{createElement:()=>new Element()},mykosTerminalAllowed:true,
 mykosShowAllPanes:false,mykosHostBytes:value=>String(value),mykosBusyContainers:new Map(),mykosSetPaneVisible:(kind,value)=>visible=value,
 mykosCell:(row,value)=>{const cell=new Element();cell.textContent=value;row.append(cell);return cell;},
 mykosActionGroup:(row)=>{const cell=new Element(),group=new Element();cell.append(group);row.append(cell);return group;},
 window:{confirm:()=>false},mykosRenderInventory(){},mykosRunContainerAction(){throw Error('Sample must not act');},alert(){}});
const source=html.slice(html.indexOf('let mykosSnapshotData'),html.indexOf('async function mykosRunContainerAction'));
vm.runInContext(source,context);
const run=s=>vm.runInContext(s,context);
run('mykosRenderSnapshots()');assert.equal(visible,false);assert.equal(el('mykos-snapshots-rows').children.length,0);
run('mykosShowAllPanes=true;mykosRenderSnapshots()');assert.equal(visible,false); // Automatic state remains false; shared visibility applies the toggle.
let rows=el('mykos-snapshots-rows').children;assert.equal(rows.length,2);
for(const row of rows){const select=row.children[3].children[0],button=row.children[5].children[0].children[0];assert.equal(select.disabled,true);assert.equal(select.children[0].disabled,true);assert.equal(button.disabled,true);assert.equal(row.children[5].children[0].children.length,2);assert.equal(row.children[5].children[0].children[1].disabled,true);}
run(`mykosSnapshotData=[{code:'12345678',os:'win11',id:'1792900000-abcdef123456',created_at:1792900000,capture_mode:'memory'},{code:'12345678',os:'win11',id:'1792900001-abcdef123457',created_at:1792900001,capture_mode:'memory'},{code:'87654321',os:'mac15',id:'1792900002-abcdef123458',created_at:1792900002,capture_mode:'memory'}];mykosRenderSnapshots();`);
rows=el('mykos-snapshots-rows').children;assert.equal(rows.length,2);assert.equal(visible,true);
assert.equal(rows[0].children[1].textContent,'Windows 11');assert.equal(rows[1].children[1].textContent,'macOS 15 Sequoia');
assert.equal(rows[0].children[2].textContent,new Date(1792900000*1000).toLocaleString());
let select=rows[0].children[3].children[0];assert.equal(select.children.length,2);
assert.equal(select.children[0].textContent,new Date(1792900000*1000).toLocaleString());
assert.ok(select.children.every(option=>!option.textContent.includes('memory')));
select.value='1792900001-abcdef123457';select.onchange();assert.equal(rows[0].children[2].textContent,new Date(1792900001*1000).toLocaleString());run('mykosRenderSnapshots()');
assert.equal(el('mykos-snapshots-rows').children[0].children[3].children[0].value,'1792900001-abcdef123457');
run('mykosTerminalAllowed=false;mykosRenderSnapshots()');
assert.equal(el('mykos-snapshots-rows').children[0].children[5].children[0].children[0].disabled,true);
console.log('Snapshot pane checks passed: grouping, per-container choices, preserved selections, disabled samples and read-only actions.');

(async()=>{
 const requests=[];
 context.mykosRunContainerAction=async (...args)=>requests.push(args);
 context.window.confirm=()=>true;
 run('mykosTerminalAllowed=true;mykosRenderSnapshots()');
 const row=el('mykos-snapshots-rows').children[0];
 await row.children[5].children[0].children[1].onclick();
 assert.deepEqual(requests,[['12345678','snapshots/1792900001-abcdef123457','DELETE']]);
 run('mykosShowAllPanes=false;mykosTerminalAllowed=false;mykosRenderSnapshots()');
 assert.equal(el('mykos-snapshots-rows').children[0].children[5].children[0].children[0].disabled,true);
 context.fetch=async()=>({ok:true,json:async()=>({allowed:true})});
 context.mykosRenderMykos=()=>{};context.mykosCloseTerminal=()=>{};
 const imageRefreshes=[];context.mykosLoadStageImages=force=>imageRefreshes.push(force);
 const permission=html.slice(html.indexOf('async function mykosCheckTerminalPermission()'),html.indexOf('setTimeout(mykosCheckTerminalPermission'));
 vm.runInContext(permission,context);
 await run('mykosCheckTerminalPermission()');
 for(const row of el('mykos-snapshots-rows').children){
  assert.equal(row.children[3].children[0].disabled,false);
  assert.equal(row.children[5].children[0].children[0].disabled,false);
  assert.equal(row.children[5].children[0].children[1].disabled,false);
 }
 context.fetch=async()=>({ok:true,json:async()=>({allowed:false})});
 await run('mykosCheckTerminalPermission()');
 assert.equal(el('mykos-snapshots-rows').children[0].children[5].children[0].children[0].disabled,true);
 assert.deepEqual(imageRefreshes,[true,true]);
 console.log('Snapshot permission arrival enables real actions with preview off; revocation disables them.');
 console.log('Snapshot delete sends the selected ID with DELETE; sample actions remain disabled.');
})().catch(error=>{console.error(error);process.exitCode=1;});

assert.ok(html.indexOf('id="macos-mykos-container-panel"') < html.indexOf('id="mykos-snapshots-title"'));
assert.ok(html.indexOf('id="mykos-snapshots-title"') < html.indexOf('id="mykos-backups-title"'));
