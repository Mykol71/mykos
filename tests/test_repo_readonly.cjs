const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../services/web/index.html'),'utf8');
const node=()=>({children:[],append(...v){this.children.push(...v)},replaceChildren(){this.children=[]}});
const els=new Map(),el=id=>{if(!els.has(id))els.set(id,node());return els.get(id)};
const ctx={URL,Date,Boolean,Number,encodeURIComponent,window:{location:{origin:'http://127.0.0.1:8799'}},document:{createElement:node,querySelectorAll:()=>[]},el,mykosTerminalAllowed:true,mykosData:{repos:{download_base_url:'http://192.0.2.80/',repos:[{name:'updateos',access:'LAN-only',read_only:true},{name:'mykos-1.5-for-x86_64-el9'}],storage:{},collected_at:1}},mykosSetPaneVisible(){},mykosStorage(){return ''},mykosHostBytes(){return ''},mykosRefreshSampleRows(){},mykosCell(row,text){const n=node();n.textContent=text;row.append(n);return n},mykosActionGroup(row){const n=node();row.append(n);return n},mykosButton(label){return {label}}};
vm.createContext(ctx);vm.runInContext(html.slice(html.indexOf('function mykosRenderMykos()'),html.indexOf('async function mykosRefreshMykos')),ctx);ctx.mykosRenderMykos();
let rows=el('mykos-repos-rows').children;assert.equal(rows.length,2);assert.equal(rows[0].children.at(-1).children.length,0);assert.equal(rows[1].children.at(-1).children.length,1);assert.equal(rows[0].children[0].children[0].title,'LAN-only repository');assert.equal(rows[0].children[0].children[0].href,'http://192.0.2.80/updateos/');
ctx.mykosData.repos.read_only=true;ctx.mykosRenderMykos();assert(el('mykos-repos-rows').children.every(r=>r.children.at(-1).children.length===0));console.log('Read-only updateos has LAN link and no Delete action; managed/remote repository controls retained.');

// A generic bootstrap hostname serves repositories from its document root.
ctx.mykosData.repos.read_only=false;delete ctx.mykosData.repos.download_base_url;
ctx.window.location.origin='https://packages.example.test';ctx.mykosRenderMykos();
assert.equal(el('mykos-repos-rows').children[1].children[0].children[0].href,'https://packages.example.test/mykos-1.5-for-x86_64-el9/');

const controls=['enable','disable','sync'].map(action=>({dataset:{repoAction:action}}));
ctx.document.querySelectorAll=()=>controls;ctx.mykosData.repos.repos=[];ctx.mykosRenderMykos();
assert.equal(controls[0].hidden,true);assert.equal(controls[1].hidden,true);assert.equal(controls[2].hidden,false);
assert.match(el('mykos-repos-rows').children[0].children[0].textContent,/No RPM repositories/);
