const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../services/web/peer-ui.js'),'utf8');
function setup(){
 const handlers={},fields={invitation:{value:'example-only',focus(){},select(){}},local:{value:'192.0.2.1'},remote:{value:'192.0.2.2'}},output={textContent:''};let closes=0,failure=false,response={id:'new-peer',invitation:'synthetic invitation',expires:2000000000,name:'Test host'};
 const dialog={open:true,querySelector:s=>s==='[data-peer=status]'?output:fields[s.match(/name=(\w+)/)[1]],querySelectorAll:()=>[],addEventListener:(name,fn)=>handlers[name]=fn,close(){closes++;this.open=false;handlers.close();}};
 const context=vm.createContext({dialog,window:{},refresh:async()=>{},request:async()=>{if(failure)throw Error('Pairing failed');return response;}});
 vm.runInContext('let busy=false,pendingDialogPeer=null;'+source.slice(source.indexOf("    dialog.addEventListener('close'"),source.indexOf('    function syncAppearance(')),context);
 return {context,dialog,fields,output,get closes(){return closes;},fail(){failure=true;},click:action=>handlers.click({target:{dataset:{peer:action}}})};
}
(async()=>{
 const accepted=setup();await accepted.click('accept');assert.equal(accepted.closes,1);assert.equal(accepted.fields.invitation.value,'');assert.equal(vm.runInContext('pendingDialogPeer',accepted.context),null);
 const invitation=setup();await invitation.click('invite');assert.equal(invitation.closes,0);assert.equal(invitation.fields.invitation.value,'synthetic invitation');
 invitation.context.closeConnectedPairing([{id:'other-peer',state:'active',connected:true}]);assert.equal(invitation.closes,0,'Other existing peers do not close a new invitation');
 invitation.context.closeConnectedPairing([{id:'new-peer',state:'pending',connected:false}]);assert.equal(invitation.closes,0);
 invitation.context.closeConnectedPairing([{id:'new-peer',state:'active',connected:true}]);assert.equal(invitation.closes,1);assert.equal(invitation.fields.invitation.value,'');
 const failed=setup();failed.fail();await failed.click('accept');assert.equal(failed.closes,0);assert.equal(failed.output.textContent,'Pairing failed');assert.equal(vm.runInContext('busy',failed.context),false);
 console.log('Peer dialog: successful accept closes; invitation stays for copying until matching connection; unrelated/pending/failure states remain open.');
})().catch(e=>{console.error(e);process.exitCode=1;});
