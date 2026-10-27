const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
const context=vm.createContext({});
vm.runInContext(html.slice(html.indexOf('function mykosReleaseName('),html.indexOf('function mykosRenderSnapshots()')),context);
for(const [os,version,wanted] of [['Red Hat Enterprise Linux 8.10 (Ootpa)','8.10','8.10 Ootpa'],['rhel','9.8','9.8 Plow'],['macOS','15','15 Sequoia'],['Ubuntu 22.04 LTS','22.04','22.04 Jammy Jellyfish'],['Windows','11','11']]){
 assert.equal(context.mykosReleaseName(os,version),wanted);
}
console.log('OS codenames render independently of preview/leaf state.');

assert.equal(context.mykosBackupOsLabel('Red Hat Enterprise Linux release 8.10 (Ootpa)'), 'rhel 8.10 Ootpa');
assert.equal(context.mykosBackupOsLabel('Ubuntu 22.04.5 LTS'), 'Ubuntu 22.04.5 LTS');
