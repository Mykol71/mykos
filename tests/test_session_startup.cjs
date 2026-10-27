const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '../services/web/index.html'), 'utf8');
assert.match(html, /<section id="login" hidden>/);
assert.match(html, /<section id="dashboard" hidden>/);
const source = html.slice(html.indexOf('async function load(){'), html.indexOf('\nfunction vpnLabel'));
function setup() {
    const elements = new Map(['login','dashboard','mykos-session-status','error'].map(id => [id, {hidden:id !== 'mykos-session-status',textContent:''}]));
    const el = id => { if (!elements.has(id)) elements.set(id, {}); return elements.get(id); };
    let respond, reject;
    const noop = () => {};
    const context = vm.createContext({el, nodes:[], AbortController, Date,
        setTimeout:() => 1, clearTimeout:noop,
        fetch:() => new Promise((resolve, fail) => {respond=resolve;reject=fail;}),
        mykosSetNotesAccess:noop, mykosLoadCurrentUser:noop, mykosLoadHostVersion:noop, mykosLoadHostMetrics:noop,
        mykosLoadCountMetrics:noop, mykosLoadStageImages:noop, mykosRefreshMykos:noop,
        mykosRefreshHeaderTotals:noop, table:noop, drawMap:noop, topology:noop});
    vm.runInContext(source, context);
    return {el,load:()=>vm.runInContext('load()',context),respond:r=>respond(r),reject:e=>reject(e)};
}
(async () => {
    for (const outcome of ['authenticated','signed-out','offline']) {
        const page=setup(), pending=page.load();
        assert.equal(page.el('login').hidden,true,'Login must stay hidden until the session check finishes');
        assert.equal(page.el('dashboard').hidden,true);
        if (outcome==='offline') page.reject(new Error('Offline'));
        else page.respond({status:outcome==='signed-out'?401:200,ok:outcome==='authenticated',json:async()=>({nodes:[]})});
        await pending;
        assert.equal(page.el('login').hidden,outcome==='authenticated');
        assert.equal(page.el('dashboard').hidden,outcome!=='authenticated');
        assert.equal(page.el('mykos-session-status').hidden,true);
    }
    console.log('Session startup checks passed: no login flash, signed-out login, and network failure recovery.');
})().catch(error=>{console.error(error);process.exitCode=1;});
