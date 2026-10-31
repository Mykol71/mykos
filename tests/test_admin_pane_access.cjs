const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../services/web/index.html'),'utf8');
const slice=(a,b)=>html.slice(html.indexOf(a),html.indexOf(b,html.indexOf(a)));
const items=new Map();
const el=id=>{if(!items.has(id))items.set(id,{hidden:true,attrs:{},setAttribute(k,v){this.attrs[k]=v;}});return items.get(id);};
const context=vm.createContext({assert,el,document:{readyState:'complete',querySelector:el},localStorage:{getItem:()=> 'true'},
 setInterval(){},setTimeout(){},mykosRefreshOsChoices(){},mykosRefreshSampleRows(){},mykosRenderSnapshots(){},mykosLoadStageImages(){},mykosCloseTerminal(){},mykosRenderMykos(){},mykosRenderInventory(){}});
vm.runInContext(slice('const mykosOsFamilies','\nfunction drawMap')+'\n'+slice('const mykosPaneVisibility','\nfunction mykosRenderMykos')+'\n'+slice('let mykosTerminalAllowed = false;','\nsetTimeout(mykosCheckTerminalPermission'),context);
(async()=>{
 context.fetch=async()=>({ok:true,json:async()=>({allowed:true})});
 // Admin checks must show every enabled family even without an identity response.
 await vm.runInContext('mykosSetPanePreviewAccess(false);mykosCheckTerminalPermission()',context);
 vm.runInContext(`for(const family of mykosOsFamilies){assert.equal(mykosCanPreviewFamily(family),true);assert.equal(el(mykosOsId(family,'mykos-container-panel')).hidden,false);}`,context);
 // Recover preview access even when the terminal's cached admin flag is unchanged.
 await vm.runInContext('mykosSetPanePreviewAccess(false);mykosCheckTerminalPermission()',context);
 vm.runInContext("assert.equal(mykosCanPreviewFamily('windows'),true);",context);
 vm.runInContext("el('mykos-show-all-panes').onclick({preventDefault(){}});for(const family of mykosOsFamilies)assert.equal(el(mykosOsId(family,'mykos-container-panel')).hidden,true);",context);
 context.fetch=async()=>({ok:true,json:async()=>({allowed:false})});
 await vm.runInContext('mykosSetPanePreviewAccess(false);mykosTerminalAllowed=false;mykosCheckTerminalPermission()',context);
 vm.runInContext("assert.equal(mykosCanPreviewFamily('windows'),false);assert.equal(mykosCanPreviewFamily('linux'),true);",context);
 console.log('Admin permission refresh shows all enabled image panes without identity fetch; leaf-off and non-admin behavior retained.');
})().catch(e=>{console.error(e);process.exitCode=1;});
