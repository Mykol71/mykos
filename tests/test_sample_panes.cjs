const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
class Element {
    constructor(tag='tbody') { this.tag=tag; this.children=[];this.style={};this.attrs={}; }
    append(child) { child.parent=this;this.children.push(child); }
    remove() { this.parent.children=this.parent.children.filter(child=>child!==this); }
    setAttribute(key,value) { this.attrs[key]=value; }
    querySelectorAll(selector) { return this.children.filter(child=>child.className===selector.slice(1)); }
}
const elements=new Map(), el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const nodes=[],mykosData={backups:{accounts:[]},repos:{repos:[]}};
const context=vm.createContext({assert,el,nodes,mykosData,document:{createElement:tag=>new Element(tag)},
    mykosOsFamilies:['linux','rocky','alma','oracle','centos','fedora','ubuntu','windows','windows-server','macos','mssql'],
    mykosPreviewOsFamilies:new Set(['linux','rocky','alma','oracle','centos']),mykosCanPreviewFamily:family=>['linux','rocky','alma','oracle','centos'].includes(family),
    mykosNodeFamily:node=>node.family,mykosOsId:(family,id)=>family+'-'+id});
const helpers=html.slice(html.indexOf('function mykosCell('),html.indexOf('function mykosStorage('));
const samples=html.slice(html.indexOf('function mykosSampleValues('),html.indexOf('const mykosPaneVisibility'));
vm.runInContext('let mykosShowAllPanes=false;'+helpers+samples,context);
const run=code=>vm.runInContext(code,context);
run('mykosRefreshSampleRows()');
assert.ok([...elements.values()].every(element=>element.children.length===0));
run('mykosShowAllPanes=true;mykosRefreshSampleRows()');
assert.equal(elements.size,13);
for(const [id,tbody] of elements.entries()) {
    if (!['linux-rows','rocky-rows','alma-rows','oracle-rows','centos-rows','mykos-backups-rows','mykos-repos-rows'].includes(id)) {
        assert.equal(tbody.children.length,0,'No non-EL sample rows');continue;
    }
    assert.equal(tbody.children.length,2);
    for(const row of tbody.children) {
        assert.match(row.children[0].textContent,/Sample/);
        const buttons=row.children.at(-1).children[0].children;
        assert.ok(buttons.length>0 && buttons.every(button=>button.disabled===true));
        assert.ok(buttons.every(button=>!button.onclick && !button.attrs.href));
    }
}
nodes.push({family:'linux',id:'12345678'});mykosData.backups.accounts.push({code:'12345678'});
run('mykosRefreshSampleRows()');
assert.equal(el('linux-rows').children.length,0);
assert.equal(el('mykos-backups-rows').children.length,0);
assert.equal(el('windows-rows').children.length,0);
assert.equal(el('rocky-rows').children.length,2);
run('mykosShowAllPanes=false;mykosRefreshSampleRows()');
assert.ok([...elements.values()].every(element=>element.children.length===0));
assert.equal(nodes.length,1);assert.equal(mykosData.backups.accounts.length,1);
console.log('Sample pane checks passed: two labeled rows per empty pane, disabled actions, populated panes preserved, and no inventory mutation.');

context.mykosCanPreviewFamily=()=>true;run('mykosShowAllPanes=true;mykosRefreshSampleRows()');assert.equal(el('windows-rows').children.length,2);assert.equal(el('macos-rows').children.length,2);assert.equal(el('ubuntu-rows').children.length,2);
