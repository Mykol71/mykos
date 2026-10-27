const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(require('node:path').join(__dirname, '../services/web/index.html'),'utf8');
const source = html.slice(html.indexOf('    async function mykosStagePoll(shop)'),html.indexOf('    mykosStageButton.addEventListener'));
async function check(state) {
    let tick, interval, clears=0, loads=0, stats=0, resolve;
    const result={textContent:'old output',hidden:false};
    const button={disabled:true};
    const ctx=vm.createContext({mykosStageTimer:null,mykosStageResult:result,mykosStageButton:button,
        mykosStageShow:message=>{result.textContent=message;result.hidden=false;},
        clearInterval:()=>{clears++;},setInterval:(fn,ms)=>{tick=fn;interval=ms;return 1;},
        mykosStageJSON:()=>new Promise(r=>{resolve=r;}),
        load:async()=>{loads++;},mykosRefreshListStats:async()=>{stats++;}});
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
    assert.equal(result.hidden,state==='done');
    assert.equal(loads,state==='done'?1:0);
    assert.equal(stats,loads);
    if(state==='done') assert.equal(result.textContent,'');
    else assert.match(result.textContent,/diagnostic output/);
}
(async()=>{await check('done');await check('failed');console.log('Startup polling: 1s, no overlaps, immediate refresh, successful output cleared and failures retained.');})().catch(error=>{console.error(error);process.exitCode=1;});
