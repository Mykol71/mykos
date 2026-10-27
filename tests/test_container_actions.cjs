// Verify OS stop icons and their requests without starting or stopping real containers.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../services/web/index.html'), 'utf8');
const source = html.slice(html.indexOf('async function mykosRunContainerAction('), html.indexOf('// Override the existing table renderer.'));
class Element {
    constructor() { this.children = []; this.style = {}; this.dataset = {}; this.listeners = {}; this.classList = {add(){}}; }
    append(...children) { this.children.push(...children); }
    setAttribute(key, value) { this[key] = value; }
    addEventListener(event, listener) { this.listeners[event] = listener; }
    querySelector() { return null; }
}
const nodes = [];
for (const family of ['windows', 'windows-server', 'macos']) {
    for (const status of ['online', 'offline', 'unknown']) nodes.push({id:String(10000000 + nodes.length), family, status});
}
const rows = nodes.map(node => {
    const row = new Element(); row.cells = [{textContent:node.id}]; return row;
});
const requests = [], prompts = [];
const desktopClicks = [], terminalClicks = [];
let reloads = 0;
const context = vm.createContext({nodes, mykosBusyContainers:new Map(), mykosSnapshotData:[], mykosRefreshSnapshots:async()=>{}, mykosListDetails:{}, mykosTerminalAllowed:true, mykosTerminalPanel:null,
    mykosSelectedShop:null, mykosIpsecShop:null, mykosOriginalTable(){},
    mykosNodeFamily:node => node.family, mykosRefreshSampleRows(){},
    document:{querySelectorAll:() => rows, createElement:() => new Element()},
    window:{confirm:message => { prompts.push(message); return true; }},
    alert(){}, mykosRenderSnapshots(){},mykosRefreshStartupInventory:async()=>{}, mykosRefreshListStats:async()=>{},load:async () => { reloads++; },
    mykosOpenTerminal:shop=>terminalClicks.push(shop),
    mykosOpenDesktop:(shop,action)=>desktopClicks.push([shop,action]),
    mykosStageJSON:async (url, options) => { requests.push({url, options}); return {state:'done'}; }});
vm.runInContext(source + '\nmykosRenderInventory();', context);
(async () => {
    for (let i = 0; i < rows.length; i++) {
        const buttons = rows[i].children[0].children[0].children;
        const stop = buttons.find(button => button.className === 'mykos-stop-button');
        assert.ok(buttons.find(button => button.className === 'mykos-delete-button'), 'A distinct trash action must be present');
        assert.ok(stop, `${nodes[i].family} ${nodes[i].status} must show a stop icon`);
        assert.equal(stop.disabled, nodes[i].status !== 'online');
        assert.match(stop.innerHTML, /rect/);
        const start = buttons.find(button => button.className === 'mykos-start-button');
        const snapshot = buttons.find(button => button.className === 'mykos-snapshot-button');
        assert.equal(start.disabled, nodes[i].status !== 'offline');
        assert.equal(snapshot.disabled, nodes[i].status !== 'online');
        if (nodes[i].status === 'online') {
            for (const name of ['mykos-logs-button','mykos-terminal-button']) {
                await buttons.find(button=>button.className===name).listeners.click({stopPropagation(){}});
            }
        }
        if (!stop.disabled) await stop.listeners.click({stopPropagation(){}});
    }
    assert.equal(requests.length, 3);
    assert.equal(reloads, 3);
    assert.equal(prompts.length, 3);
    assert.deepEqual(desktopClicks,nodes.filter(node=>node.status==='online').map(node=>[node.id,'console']));
    assert.deepEqual(terminalClicks,nodes.filter(node=>node.status==='online').map(node=>node.id));
    for (let i = 0; i < requests.length; i++) {
        assert.equal(requests[i].url, `/mykos/api/web/containers/${nodes[i * 3].id}/stop`);
        assert.equal(requests[i].options.method, 'POST');
    }
    console.log('Container action checks passed: stop icons for all three OS panes, disabled states, confirmation and stop requests.');
})().catch(error => { console.error(error); process.exitCode = 1; });
