// Run with node tests/test_os_panes.cjs; no external dependencies.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../services/web/index.html'), 'utf8');
for (const script of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(script[1]);
const slice = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));
const elements = new Map();
const el = id => {
    if (!elements.has(id)) elements.set(id, {value:'', options:[], hidden:true,
        replaceChildren(...options) { this.options = options; }, add(option) { this.options.push(option); }, setAttribute() {}});
    return elements.get(id);
};
const context = vm.createContext({setInterval(){},assert, el, Date, URL, html, window:{location:{origin:'https://testserver'}}, Option:function(text, value) { this.text = text; this.value = value; },
    document:{querySelector:selector => el(selector)}});
const source = slice('const mykosOsFamilies', '\nfunction drawMap') + '\n' +
    slice('const mykosPaneVisibility', '\nfunction mykosRenderMykos') + '\n' +
    slice('let mykosStageImagesLoading', '\nasync function mykosStageJSON');
vm.runInContext(`
let nodes = [], mykosListDetails = {}, mykosListMemory = {}, mykosTerminalAllowed = true;
const escape = value => String(value ?? '');
const statusIcon = (_, value) => value;
const mykosOsLabel = value => value;
const mykosUptime = () => '1d';
const mykosRefreshHeaderTotals = () => {};
const mykosLoadCountMetrics = () => {};
const mykosRefreshSampleRows = () => {};
const mykosRenderSnapshots = () => {};
let responseImages = [], responseBuilds = [], fail = false;
async function mykosStageJSON() { if (fail) throw new Error('Unavailable'); return {images:responseImages, builds:responseBuilds}; }
` + source, context);
(async () => {
    await vm.runInContext(`(async () => {
        for (const [family,versions] of Object.entries({rocky:[8,9,10],alma:[8,9,10],oracle:[8,9,10],centos:[8,9,10],fedora:[43,44]})) {
            for (const version of versions) assert.equal(mykosImageFamily(family+version), family);
            assert.equal(mykosNodeFamily({id:'test',os_type:family,image:'localhost/'+family+versions[0]+':latest'}),family);
        }
        assert.equal(mykosImageFamily('centos7'),null);
        assert.equal(mykosImageFamily('fedora45'),null);
        for (const os of ['rh7','rh8','rh9','rh10']) assert.equal(mykosImageFamily(os), 'linux');
        for (const os of ['ubuntu','ubuntu22','ubuntu24','ubuntu24.04','ubuntu26.04']) assert.equal(mykosImageFamily(os), 'ubuntu');
        for (const os of ['win10','win11','win8e','win7u','winxp','winme','win2k','win98']) assert.equal(mykosImageFamily(os), 'windows');
        for (const os of ['wns2025','wns2022','wns2019','wns2016']) assert.equal(mykosImageFamily(os), 'windows-server');
        for (const os of ['mac15','mac14','mac13','mac12']) assert.equal(mykosImageFamily(os), 'macos');
        assert.equal(mykosImageFamily('mssql2022'), 'mssql');
        assert.equal(mykosImageFamily('mssql2025'), 'mssql');
        assert.equal(mykosNodeFamily({id:'sql',os_type:'Red Hat Enterprise Linux',image:'localhost/mssql2022:latest'}), 'mssql');
        assert.equal(mykosImageFamily('other'), null);
        el('rocky-mykos-image-builds').querySelectorAll=()=>[];
        mykosCapturedImages=[{os:'rocky9',version:'9.10',size_bytes:2147483648,created_at:1793060009,delete_state:'idle'}];
        mykosRenderImageBuilds();
        assert.match(el('rocky-mykos-image-builds').innerHTML,/Prepared image/);
        assert.ok(el('rocky-mykos-image-builds').innerHTML.includes('Size: 2.00 GiB'));
        assert.ok(el('rocky-mykos-image-builds').innerHTML.includes('Produced: '+new Date(1793060009*1000).toLocaleString()));
        mykosCapturedImages[0].delete_state='failed';mykosCapturedImages[0].delete_error='Dependency';
        mykosRenderImageBuilds();
        assert.ok(el('rocky-mykos-image-builds').innerHTML.includes('Size: 2.00 GiB | Produced:'));
        mykosCapturedImages[0].delete_state='idle';
        assert.match(el('rocky-mykos-image-builds').innerHTML,/9\.10/);
        mykosTerminalAllowed=false;mykosRenderImageBuilds();
        assert.doesNotMatch(el('rocky-mykos-image-builds').innerHTML,/Prepared image/);
        mykosTerminalAllowed=true;mykosCapturedImages=[];

        for (const images of [[], ['rh9'], ['ubuntu24.04'], ['win11'], ['wns2022'], ['mac15'], ['mssql2022'], ['rocky9'], ['alma10'], ['oracle8'], ['centos10'], ['fedora44'], ['rh10','ubuntu24','win10','wns2025','mac14']]) {
            responseImages = images.map(os => ({os, image:'image-'+os})); mykosStageImagesLoadedAt = 0;
            await mykosLoadStageImages();
            for (const family of mykosOsFamilies) {
                const expected = images.filter(os => mykosImageFamily(os) === family);
                assert.equal(el(mykosOsId(family, 'mykos-container-panel')).hidden, !expected.length);
                assert.equal(el(mykosOsId(family, 'mykos-containers-pane')).hidden, !expected.length);
                assert.equal(JSON.stringify(el(mykosOsId(family, 'mykos-stage-os')).options.slice(1).map(o => o.value)), JSON.stringify(expected));
            }
        }
        responseImages = []; responseBuilds = [{os:'ubuntu24.04', name:'ubuntu24.04', console_url:'/mykos/console/ubuntu24.04/'}];
        mykosStageImagesLoadedAt = 0; await mykosLoadStageImages();
        assert.equal(el('ubuntu-mykos-container-panel').hidden, false);
        assert.match(el('ubuntu-mykos-image-builds').innerHTML, /Installation console/);
        assert.match(el('ubuntu-mykos-image-builds').innerHTML, /<tr class="mykos-image-build-row">/);
        assert.match(el('ubuntu-mykos-image-builds').innerHTML, /<td colspan="4">Finish installation/);
        assert.match(el('ubuntu-mykos-image-builds').innerHTML, /<td class="mykos-actions-cell"><div class="mykos-row-actions"[^>]*><a /);
        assert.ok(html.includes('<tbody id="ubuntu-rows" class="mykos-container-rows"></tbody><tbody id="ubuntu-mykos-image-builds" class="mykos-image-build-rows" hidden>'));
        assert.ok(el('ubuntu-mykos-image-builds').innerHTML.includes('https://testserver/mykos/console/ubuntu24.04/'));
        assert.equal(el('ubuntu-mykos-stage-os').options.length, 1, 'Unfinished builds must not be startable');
        mykosTerminalAllowed = false; mykosRenderImageBuilds();
        assert.doesNotMatch(el('ubuntu-mykos-image-builds').innerHTML, /href=/);
        mykosTerminalAllowed = true;
        responseBuilds = []; mykosStageImagesLoadedAt = 0; await mykosLoadStageImages();
        assert.equal(el('ubuntu-mykos-container-panel').hidden, true);
        assert.equal(el('ubuntu-mykos-image-builds').hidden, true);
        nodes = [{id:'11111111',status:'offline',vpn:'up'}, {id:'22222222',status:'unknown',vpn:'up'}, {id:'33333333',status:'online',vpn:'not_configured'}];
        mykosListDetails = {'11111111':{os_type:'Windows'}, '22222222':{image:'localhost/mac15:latest'}, '33333333':{os_type:'Red Hat Enterprise Linux'}};
        table();
        assert.match(el('windows-rows').innerHTML, /11111111/);
        assert.doesNotMatch(el('windows-rows').innerHTML, /22222222|33333333|mykos-ipsec-link|mykos-add-vpn-button/);
        assert.match(el('macos-rows').innerHTML, /22222222/);
        assert.doesNotMatch(el('macos-rows').innerHTML, /11111111|33333333|mykos-ipsec-link|mykos-add-vpn-button/);
        assert.match(el('rows').innerHTML, /33333333/);
        assert.match(el('rows').innerHTML, /mykos-add-vpn-button/);
        nodes.push({id:'44444444',status:'online',vpn:'up'});
        mykosListDetails['44444444'] = {os_type:'Windows', image:'localhost/wns2025:latest'};
        table();
        assert.match(el('windows-server-rows').innerHTML, /44444444/);
        assert.doesNotMatch(el('windows-server-rows').innerHTML, /mykos-ipsec-link|mykos-add-vpn-button/);
        assert.doesNotMatch(el('windows-rows').innerHTML, /44444444/);
        assert.equal(mykosNodeFamily({id:'server',os_type:'Microsoft Windows Server 2022'}), 'windows-server');
        nodes.push({id:'55555555',status:'offline',vpn:'not_configured'});
        mykosListDetails['55555555'] = {os_type:'Ubuntu', image:'localhost/ubuntu24:latest'};
        table();
        assert.match(el('ubuntu-rows').innerHTML, /55555555/);
        assert.doesNotMatch(el('rows').innerHTML, /55555555/);
        assert.equal(mykosNodeFamily({id:'ubuntu',image:'localhost/ubuntu24.04:latest'}), 'ubuntu');
        for (const [id, family] of [['11111111','windows'], ['22222222','macos'], ['44444444','windows-server']]) {
            mykosListDetails[id].ips = [{network:'podman',address:'10.88.0.42'}];
            mykosListDetails[id].desktop_host = '192.168.222.5';
            mykosListMemory[id] = {eth1_ip:'192.0.2.99'};
            table();
            assert.match(el(mykosOsId(family, 'rows')).innerHTML, /10\.88\.0\.42/);
            assert.doesNotMatch(el(mykosOsId(family, 'rows')).innerHTML, /192\.0\.2\.99|192\.168\.222\.5/);
            assert.equal(mykosNodeIp(family, {}, {eth1_ip:'192.0.2.99'}), 'Unknown');
        }
        assert.equal(mykosNodeIp('linux', {}, {eth1_ip:'192.0.2.99'}), '192.0.2.99');
        assert.equal(mykosNodeIp('ubuntu', {}, {eth1_ip:'192.0.2.99'}), 'Unknown');
        assert.equal(mykosNodeIp('ubuntu', {ips:[{address:'192.168.22.232'}]}, {eth1_ip:'192.0.2.99'}), '192.168.22.232');
        fail = true; mykosStageImagesLoadedAt = 0; await mykosLoadStageImages();
        for (const family of mykosOsFamilies) assert.equal(el(mykosOsId(family, 'mykos-container-panel')).hidden, true);
        el('mykos-show-all-panes').onclick({preventDefault(){}});
        for (const family of mykosOsFamilies) assert.equal(el(mykosOsId(family, 'mykos-container-panel')).hidden, !mykosPreviewOsFamilies.has(family));
        for (const family of mykosOsFamilies) {
            const select = el(mykosOsId(family, 'mykos-stage-os'));
            assert.equal(select.disabled, !mykosPreviewOsFamilies.has(family), 'Only EL preview dropdowns open without images');
            assert.equal(select.options.length, mykosPreviewOsFamilies.has(family) ? mykosOsChoices[family].length + 1 : 1);
            assert.ok(select.options.slice(1).every(option => option.disabled), 'Unavailable preview images must be disabled');
        }
        fail = false;
        responseImages = ['fedora44','ubuntu24.04','win11','wns2022','mac15','mssql2022'].map(os=>({os,image:'image-'+os}));
        mykosStageImagesLoadedAt=0;await mykosLoadStageImages();
        for (const family of mykosOsFamilies.filter(family=>!mykosPreviewOsFamilies.has(family))) {
            assert.equal(el(mykosOsId(family,'mykos-container-panel')).hidden,false,'Real non-EL images remain visible with the leaf on');
            assert.equal(el(mykosOsId(family,'mykos-stage-os')).options.length,2,'Non-EL choices contain actual images only');
        }
        responseImages=[];mykosStageImagesLoadedAt=0;await mykosLoadStageImages();
        for (const family of mykosOsFamilies) {
            assert.equal(el(mykosOsId(family,'mykos-container-panel')).hidden,!mykosPreviewOsFamilies.has(family));
        }
        fail = false; responseImages = [{os:'rh9',image:'image-rh9'}]; mykosStageImagesLoadedAt = 0;
        await mykosLoadStageImages();
        assert.equal(el('mykos-stage-os').options.find(option => option.value === 'rh9').disabled, false);
        for (const family of mykosOsFamilies) {
            for (const option of el(mykosOsId(family, 'mykos-stage-os')).options.slice(1)) {
                assert.equal(option.text, option.value.replace(/^(?:mssql|fedora|centos|oracle|rocky|alma|ubuntu|wns|win|mac|rh)/i, ''));
            }
        }
        assert.equal(el('mykos-stage-os').options.find(option => option.value === 'rh10').disabled, true);
        el('mykos-stage-os').value = 'rh9';
        mykosRefreshOsChoices();
        assert.equal(el('mykos-stage-os').value, 'rh9', 'Preserve the available selection');
        fail = true;
        for (const kind of ['backups','repos']) assert.equal(el('mykos-'+kind+'-pane').hidden,false);
        mykosStageImagesLoadedAt = 0; await mykosLoadStageImages();
        for (const family of mykosOsFamilies) assert.equal(el(mykosOsId(family, 'mykos-container-panel')).hidden, !mykosPreviewOsFamilies.has(family), 'Failed refresh preserves EL-only preview');
        mykosSetPaneVisible('backups', true);
        el('mykos-show-all-panes').onclick({preventDefault(){}});
        for (const family of mykosOsFamilies) assert.equal(el(mykosOsId(family, 'mykos-container-panel')).hidden, true);
        assert.equal(el('mykos-backups-pane').hidden,false,'Turning override off restores the latest automatic state');
        assert.equal(el('mykos-repos-pane').hidden,true);
    })()`, context);
    for (const family of ['windows','windows-server','macos']) {
        const section = html.slice(html.indexOf(`<div id="${family}-mykos-container-panel"`));
        assert.doesNotMatch(section.slice(0, section.indexOf('</table>')), /<th>VPN<\/th>/);
    }
    console.log('OS pane checks passed: family choices, independent visibility, stopped/unknown rows, VPN exclusion and errors.');
})().catch(error => { console.error(error); process.exitCode = 1; });
