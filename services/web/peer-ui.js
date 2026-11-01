/* Reciprocal nodes retain independent Host panes, charts and control targets. */
(() => {
    'use strict';
    const hostPane = document.getElementById('mykos-host-pane');
    if (!hostPane || window.MYKOS_PEER_CONTEXT) return;
    let allowed = false, busy = false;
    const label = document.createElement('h2');
    label.className = 'mykos-pane-label'; label.textContent = 'Host · this node';
    hostPane.before(label);
    const region = document.createElement('div');
    region.id = 'mykos-peer-hosts'; hostPane.after(region);
    const controls = document.createElement('div');
    controls.style.cssText = 'display:flex;align-items:center;gap:10px;margin:4px 0';
    const add = document.createElement('button'); add.type = 'button';
    add.className = 'mykos-icon mykos-add-vpn-button'; add.textContent = '+';
    add.title = add.ariaLabel = 'Connect another MYKOS host'; add.hidden = true;
    const status = document.createElement('span'); status.role = 'status';
    controls.append(add, status); label.append(controls);
    const frames = new Map();
    const dialog = document.createElement('dialog');
    dialog.style.cssText = 'max-width:650px;width:calc(100% - 48px);color:inherit;background:#121d30;border:1px solid #64748b;border-radius:10px';
    dialog.innerHTML = `<form method="dialog"><h2>Connect a MYKOS host</h2><p>Create a 15-minute invitation here, then paste it into the other host while logged in as its administrator.</p>
<label>This host’s reachable IPv4 <input name="local" required inputmode="decimal" autocomplete="off"></label>
<label>Other host’s reachable IPv4 <input name="remote" required inputmode="decimal" autocomplete="off"></label>
<button type="button" data-peer="invite">Create invitation</button><p>Or paste an invitation from the other host:</p>
<textarea name="invitation" rows="5" style="width:100%;box-sizing:border-box" autocomplete="off" spellcheck="false" aria-label="Private pairing invitation"></textarea>
<p>Keep invitations private. They grant reciprocal administrator controls to the named host.</p>
<button type="button" data-peer="accept">Accept and connect</button> <button value="close">Close</button><p data-peer="status" role="status"></p></form>`;
    dialog.querySelectorAll('label').forEach(item => item.style.cssText = 'display:block;margin:12px 0');
    document.body.append(dialog);
    async function request(path, options={}) {
        const response = await fetch('/mykos/api/web/peers' + path, {cache:'no-store', ...options});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Peer request failed');
        return data;
    }
    function open() {
        if (!allowed) return;
        dialog.querySelector('[name=local]').value = document.getElementById('mykos-host-row-ip')?.textContent || '';
        dialog.querySelector('[data-peer=status]').textContent = '';
        if (!dialog.open) dialog.showModal();
    }
    window.mykosOpenPeerDialog = open;
    add.onclick = open;
    dialog.addEventListener('close', () => { dialog.querySelector('[name=invitation]').value = ''; });
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    dialog.addEventListener('submit', event => { if (busy) event.preventDefault(); });
    dialog.addEventListener('click', async event => {
        const action = event.target.dataset.peer;
        if (!['invite','accept'].includes(action) || busy) return;
        const output = dialog.querySelector('[data-peer=status]');
        busy = true; dialog.querySelectorAll('button').forEach(button => button.disabled = true);
        output.textContent = action === 'invite' ? 'Preparing the peer VPN…' : 'Connecting and verifying both nodes…';
        try {
            const invitation = dialog.querySelector('[name=invitation]');
            const data = await request('/' + action, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(action === 'invite' ? {local_ip:dialog.querySelector('[name=local]').value.trim(), remote_ip:dialog.querySelector('[name=remote]').value.trim()} : {invitation:invitation.value.trim()})});
            if (action === 'invite') {
                invitation.value = data.invitation;
                invitation.focus(); invitation.select();
                output.textContent = 'Copy this private invitation to the other host. Expires ' + new Date(data.expires * 1000).toLocaleTimeString() + '.';
            } else {
                invitation.value = ''; output.textContent = 'Connected to ' + data.name + '.';
            }
            await refresh();
        } catch (error) { output.textContent = error.message; }
        finally { busy = false; dialog.querySelectorAll('button').forEach(button => button.disabled = false); }
    });
    function render(peer) {
        let item = frames.get(peer.id);
        if (!item) {
            const section = document.createElement('section');
            const heading = document.createElement('h2'); heading.className = 'mykos-pane-label';
            const name = document.createElement('span');
            const state = document.createElement('span'); state.role = 'status'; state.style.margin = '0 12px';
            const link = document.createElement('a'); link.textContent = 'Open full node'; link.target = '_blank'; link.rel = 'noopener';
            link.href = '/mykos/api/web/peers/' + peer.id + '/view?full=true';
            const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Disconnect'; remove.style.marginLeft = '12px';
            remove.onclick = async () => {
                if (!confirm('Disconnect peer ' + peer.name + '? This removes only its MYKOS peer VPN and shared controls.')) return;
                remove.disabled = true;
                try { await request('/' + peer.id, {method:'DELETE'}); await refresh(); }
                catch (error) { state.textContent = error.message; remove.disabled = false; }
            };
            heading.append(name, state, link, remove);
            const frame = document.createElement('iframe'); frame.title = 'Complete Host pane · ' + peer.name;
            frame.style.cssText = 'display:block;width:100%;height:420px;border:0;background:transparent';
            section.append(heading, frame); region.append(section);
            item = {section,name,state,frame,link}; frames.set(peer.id, item);
        }
        item.name.textContent = 'Host · ' + peer.name + ' · ' + peer.ip;
        item.state.textContent = peer.expired ? 'Invitation expired' : peer.state === 'pending' ? 'Awaiting acceptance' : peer.connected ? 'Connected' : 'Offline';
        item.link.hidden = peer.state !== 'active';
        if (peer.state === 'active' && peer.connected) {
            item.frame.hidden = false;
            if (!item.frame.getAttribute('src')) item.frame.src = '/mykos/api/web/peers/' + peer.id + '/view';
        } else {
            // Destroy offline documents so stale controls cannot be clicked.
            item.frame.hidden = true; item.frame.removeAttribute('src');
        }
    }
    async function refresh() {
        try {
            const data = await request('');
            allowed = true; add.hidden = false; status.textContent = '';
            label.firstChild.textContent = 'Host · ' + data.name + ' · this node';
            const identifiers = new Set(data.peers.map(peer => peer.id));
            for (const [id,item] of frames) if (!identifiers.has(id)) { item.section.remove(); frames.delete(id); }
            data.peers.forEach(render);
        } catch (error) {
            status.textContent = allowed ? 'Peer status unavailable' : '';
            for (const item of frames.values()) { item.state.textContent = 'Status unavailable'; item.frame.hidden = true; item.frame.removeAttribute('src'); }
        }
    }
    window.addEventListener('message', event => {
        if (event.origin !== location.origin || event.data?.type !== 'mykos-peer-height') return;
        for (const [id,item] of frames) {
            if (event.source === item.frame.contentWindow && event.data.prefix === '/mykos/api/web/peers/' + id + '/proxy' && Number.isFinite(event.data.height))
                item.frame.style.height = Math.max(260, Math.min(1800, event.data.height)) + 'px';
        }
    });
    refresh(); window.setInterval(refresh, 15000);
})();
