const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../services/web/index.html'),'utf8');
const source=html.slice(html.indexOf('async function mykosOpenDesktop('),html.indexOf('// MYKOS READ ONLY CONTAINER LOGS V1'));
const locations=[],requests=[];
let current={running:true,console_url:'/mykos/console/01234567/',protocol:'rdp',login_supported:true};
const context=vm.createContext({mykosTerminalAllowed:true,
    window:{open:()=>({location:{replace:url=>locations.push(url)},close(){}})},
    mykosStageJSON:async url=>{requests.push(url);return current;},alert:message=>{throw Error(message);}});
vm.runInContext(source,context);
(async()=>{
    await context.mykosOpenDesktop('01234567','console');
    assert.equal(locations.pop(),'/mykos/console/01234567/');
    await context.mykosOpenDesktop('01234567','login');
    assert.equal(locations.pop(),'/mykos/api/web/desktop/01234567/rdp');
    current={running:true,console_url:'/mykos/console/87654321/',protocol:'vnc',login_url:'vnc://192.0.2.10:23016'};
    await context.mykosOpenDesktop('87654321','console');
    assert.equal(locations.pop(),'/mykos/console/87654321/');
    await context.mykosOpenDesktop('87654321','login');
    assert.equal(locations.pop(),'vnc://192.0.2.10:23016');
    assert.deepEqual(requests,['01234567','01234567','87654321','87654321'].map(code=>'/mykos/api/web/desktop/'+code));
    console.log('Desktop console and login links target the correct instance and protocol.');
})().catch(error=>{console.error(error);process.exitCode=1;});
