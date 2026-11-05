const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../services/web/peer-ui.js'),'utf8');
assert.match(source,/<button type="button" data-peer="cancel">Cancel<\/button>/);
const handlers={},invitation={value:'private invitation'},output={textContent:''};let closes=0,requests=0;
const dialog={querySelector:s=>s==='[name=invitation]'?invitation:output,querySelectorAll:()=>[],close(){closes++;handlers.close();},addEventListener:(name,fn)=>handlers[name]=fn};
const context=vm.createContext({dialog,request:()=>{requests++;throw Error('Cancel must not send requests');},window:{},refresh:()=>{}});
vm.runInContext('let busy=false,pendingDialogPeer=null;'+source.slice(source.indexOf("    dialog.addEventListener('close'"),source.indexOf('    function syncAppearance(')),context);
(async()=>{
 await handlers.click({target:{dataset:{peer:'cancel'}}});assert.equal(closes,1);assert.equal(invitation.value,'');assert.equal(requests,0);
 vm.runInContext('busy=true',context);invitation.value='in-flight';await handlers.click({target:{dataset:{peer:'cancel'}}});assert.equal(closes,1);assert.equal(invitation.value,'in-flight');
 let prevented=0;handlers.cancel({preventDefault:()=>prevented++});handlers.submit({preventDefault:()=>prevented++});assert.equal(prevented,2);
 vm.runInContext('busy=false',context);handlers.cancel({preventDefault:()=>prevented++});assert.equal(prevented,2);
 await handlers.click({target:{dataset:{peer:'cancel'}}});assert.equal(closes,2);assert.equal(invitation.value,'');assert.equal(requests,0);
 console.log('Peer Cancel closes without a pairing request, clears the invitation and preserves busy/Escape guards.');
})().catch(e=>{console.error(e);process.exitCode=1;});
