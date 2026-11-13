const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../services/web/index.html'),'utf8');
function extract(name){let start=html.indexOf('function '+name+'(');assert.ok(start>=0,name);let brace=html.indexOf('{',start),depth=1,end=brace+1;while(depth&&end<html.length){if(html[end]==='{')depth++;if(html[end]==='}')depth--;end++;}return html.slice(start,end);}
const context={};vm.createContext(context);vm.runInContext(extract('mykosImageFamily')+'\n'+extract('mykosImageVersionLabel'),context);
for(const family of ['oracle','centos','scientific','springdale'])for(const major of [6,7]){
 const key=family+major;assert.equal(context.mykosImageFamily(key),family);assert.match(context.mykosImageVersionLabel(key),/legacy/);
}
assert.equal(context.mykosImageFamily('rh6'),'linux');assert.equal(context.mykosImageFamily('alma6'),null);assert.equal(context.mykosImageFamily('rocky7'),null);
for(const family of ['scientific','springdale'])for(const suffix of ['mykos-containers-pane','mykos-container-panel','mykos-stage-os','rows','mykos-image-builds']){
 assert.equal(html.split('id="'+family+'-'+suffix+'"').length-1,1,'Exactly one '+family+' '+suffix);
}
console.log('Legacy family/version/pane wiring passed');
