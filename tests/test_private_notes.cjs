const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
const buttons=[{},{}],text={value:'private text'},dialog={open:true,close(){this.open=false}};
const context=vm.createContext({document:{querySelectorAll:()=>buttons},mykosNotesText:text,mykosNotesDialog:dialog});
vm.runInContext(html.slice(html.indexOf('let mykosNotesAllowed'),html.indexOf('function mykosNotesSetBusy')),context);
context.mykosSetNotesAccess(true);assert.ok(buttons.every(b=>!b.hidden));
context.mykosSetNotesAccess(false);assert.ok(buttons.every(b=>b.hidden));assert.equal(text.value,'');assert.equal(dialog.open,false);
assert.ok(html.includes('button.hidden = !mykosNotesAllowed'));
console.log('Notes hidden by default; revocation hides icons and clears displayed private text.');

vm.runInContext(html.slice(html.indexOf('function mykosNotesURL()'),html.indexOf('async function mykosNotesLoad()')),context);
vm.runInContext("mykosNotesPane='personal'",context);assert.equal(context.mykosNotesURL(),'/mykos/api/web/personal-notes');
vm.runInContext("mykosNotesPane='host'",context);assert.equal(context.mykosNotesURL(),'/mykos/api/web/notes/host');
assert.ok(html.includes("const mykosPersonalNotes = mykosNotesButton('personal', 'Personal')"),'Personal notes reuse the exact existing icon factory');
console.log('Personal notes use a separate endpoint and the same icon as pane notes.');
