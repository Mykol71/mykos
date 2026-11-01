const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
const source=html.slice(html.indexOf('function mykosHardwareSummary('),html.indexOf('let mykosHostStatusData'));
const c=vm.createContext({assert});
vm.runInContext(source+`assert.equal(mykosHardwareSummary({manufacturer:'Dell',model:'T350',serial:'ABC'}),'Dell · Model: T350 · Serial: ABC');assert.equal(mykosHardwareSummary(null),'');assert.equal(mykosHardwareSummary({serial:17}),'');assert.equal(mykosHardwareSummary({model:'<test>'}),'Model: <test>');`,c);
assert.match(html,/mykosHostHardware.textContent=mykosHardwareSummary/);
assert.match(html,/font-size:8px;line-height:1.4;color:#9098a3/);
console.log('Hardware summary: safe text, optional fields, small grey host-pane footer.');
vm.runInContext(`
assert.equal(mykosHardwareSummary({manufacturer:'Dell'},{download_mbps:912.34,upload_mbps:103.45,stale:false}), 'Dell · Download: 912.3 Mbps · Upload: 103.5 Mbps');
assert.equal(mykosHardwareSummary(null,{download_mbps:0,upload_mbps:0,stale:true}), 'Download: 0.0 Mbps (stale) · Upload: 0.0 Mbps (stale)');
assert.equal(mykosHardwareSummary({manufacturer:'Dell'},{download_mbps:null,upload_mbps:3}), 'Dell');
assert.equal(mykosHardwareSummary({manufacturer:'Dell'},{download_mbps:Infinity,upload_mbps:3}), 'Dell');
`,c);
