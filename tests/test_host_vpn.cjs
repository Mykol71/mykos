const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
const elements=new Map();
function el(id) {
    if(!elements.has(id)) elements.set(id,{dataset:{},classes:new Set(),setAttribute(k,v){this[k]=v;},
        classList:{toggle(name,on){if(on)elements.get(id).classes.add(name);else elements.get(id).classes.delete(name);}}});
    return elements.get(id);
}
const context=vm.createContext({el,document:{getElementById:id=>el(id)},Date,statusIcon:(_,status)=>status,mykosUptime:()=>'',mykosHostBytes:()=>'',
    mykosHostStatusData:null,mykosTerminalAllowed:true,mykosHostStatusTable:{querySelector:selector=>el(selector)}});
vm.runInContext(html.slice(html.indexOf('function mykosReleaseName('),html.indexOf('function mykosRenderSnapshots()')),context);
vm.runInContext(html.slice(html.indexOf('function mykosRenderHostStatus('),html.indexOf('let mykosHostDiagnosticRequest')),context);
context.mykosRenderHostStatus({host:{vpn:'not_configured'}});
const button=el('mykos-host-row-vpn');
assert.equal(button.dataset.hostAction,'add-vpn');assert.equal(button.disabled,false);
assert.ok(button.classes.has('mykos-add-vpn-button'));assert.match(button.innerHTML,/M12 5v14/);
context.mykosTerminalAllowed=false;context.mykosRenderHostStatus({host:{vpn:'not_configured'}});
assert.equal(button.disabled,true);
for(const status of ['up','down','unknown']) {
    context.mykosRenderHostStatus({host:{vpn:status}});
    assert.equal(button.dataset.hostAction,'ipsec');assert.equal(button.disabled,false);
    assert.equal(button.classes.has('mykos-add-vpn-button'),false);assert.equal(button.innerHTML,status);
}
context.mykosRenderHostStatus({host:{hostname:'prod.example.com',certificate_domain:'example.com',vpn:'unknown'}});
assert.ok(!elements.has('mykos-host-pane-name'));
assert.doesNotMatch(html,/id="mykos-host-title"|mykos-logo-host-apple/);
assert.match(html,/id="mykos-host-pane"[^>]*aria-label="Host performance and status"/);
assert.equal(el('mykos-host-row-name').textContent,'prod.example.com');
context.mykosRenderHostStatus({host:{hostname:'dev.example.com',vpn:'unknown'}});
assert.equal(el('mykos-host-row-name').textContent,'dev.example.com');
console.log('Host VPN actions and status-row hostname retained without a title heading.');

context.mykosRenderHostStatus({host:{os:'Red Hat Enterprise Linux 8.10 (Ootpa)',version:'8.10'}});assert.equal(el('mykos-host-row-version').textContent,'8.10 Ootpa');
