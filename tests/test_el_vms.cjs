const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../services/web/index.html'),'utf8');
function extract(name){const start=html.indexOf('function '+name+'(');assert(start>=0,name);let i=html.indexOf('{',start),depth=1,end=i+1;while(depth&&end<html.length){if(html[end]==='{')depth++;if(html[end]==='}')depth--;end++;}return html.slice(start,end);}
const c={};vm.createContext(c);vm.runInContext(['mykosElVmInfo','mykosImageFamily','mykosImageVersionLabel'].map(extract).join('\n'),c);
let count=0;
for(const prefix of ['rh','rocky','alma','oracle','centos'])for(const major of ['8','9','10'])for(const desktop of [false,true]){
 const os=prefix+major+(desktop?'desktopvm':'vm'),profile=c.mykosElVmInfo(os);count++;
 assert.equal(profile.family,prefix==='rh'?'linux':prefix);assert.equal(profile.native,prefix+major);assert.equal(profile.desktop,desktop);
 assert.equal(profile.defaultRamMiB,desktop?4096:2048);
 assert.equal(c.mykosImageFamily(os),profile.family);assert.equal(c.mykosImageVersionLabel(os),major+(desktop?' GNOME Desktop VM':' Server VM'));
 assert(html.includes("'"+os+"'"),'Selectable in existing pane: '+os);
}
assert.equal(count,30);
for(const bad of ['rh7vm','rh6desktopvm','alma11vm','rh9rtivm','oracle9vm;id','rocky9desktop'])assert.equal(c.mykosElVmInfo(bad),null);
assert.match(html,/ram_mib%256/);assert.match(html,/!check\.exists/);assert.match(html,/Future memory snapshots retain this size/);
console.log('30 public Server/Desktop family/version/default-RAM choices and RAM prompt guards passed');
