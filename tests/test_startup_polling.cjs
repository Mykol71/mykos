const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(require('node:path').join(__dirname, '../services/web/index.html'),'utf8');
const source = html.slice(html.indexOf('    async function mykosStagePoll(shop)'),html.indexOf('    mykosStageButton.addEventListener'));
async function check(state) {
    let tick, interval, clears=0, loads=0, stats=0, resolve;
    const result={textContent:'old output',hidden:false};
    const button={disabled:true,dataset:{requestedOs:"ubuntu24"}};
    const ctx=vm.createContext({mykosWatchCreatedInstance:(shop,os,pre)=>{assert.equal(shop,"12345678");assert.equal(os,"ubuntu24");pre.textContent="Created successfully; confirming inventory";},mykosStageTimer:null,mykosStageResult:result,mykosStageButton:button,
        mykosStageShow:message=>{result.textContent=message;result.hidden=false;},
        clearInterval:()=>{clears++;},setInterval:(fn,ms)=>{tick=fn;interval=ms;return 1;},
        mykosStageJSON:()=>new Promise(r=>{resolve=r;}),
        mykosRefreshStartupInventory:async()=>{loads++;stats++;},load:async()=>{loads++;},mykosRefreshListStats:async()=>{stats++;}});
    vm.runInContext(source,ctx);
    await ctx.mykosStagePoll('12345678');
    assert.equal(interval,1000);
    const pending=tick();
    const originalResolve=resolve;
    await tick();
    assert.equal(resolve,originalResolve,'Do not overlap pending status requests');
    resolve({state,output:'diagnostic output'});await pending;
    assert.equal(button.disabled,false);
    assert.equal(clears,2);
    assert.equal(result.hidden,false);
    assert.equal(loads,0,'Startup completion must not trigger a full inventory collection');
    assert.equal(stats,0,'Normal polling supplies details separately from state-only completion');
    if(state==='done') assert.match(result.textContent,/Created successfully/);
    else assert.match(result.textContent,/diagnostic output/);
}
(async()=>{await check('done');await check('failed');console.log('Startup polling: 1s, no overlaps, state-only completion, successful output retained and failures retained.');})().catch(error=>{console.error(error);process.exitCode=1;});
