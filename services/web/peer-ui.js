/* Reciprocal nodes retain independent Host panes, charts and control targets. */
(() => {
    'use strict';
    const hostPane = document.getElementById('mykos-host-pane');
    if (!hostPane || window.MYKOS_PEER_CONTEXT) return;
    let allowed = false, busy = false;
    const region = document.createElement('div');
    region.id = 'mykos-peer-hosts'; hostPane.after(region);
    const add = document.createElement('button'); add.type = 'button';
    add.id = 'mykos-host-add-peer';
    add.className = 'mykos-icon mykos-add-vpn-button';
    add.dataset.hostAction = 'add-vpn';
    add.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
    add.title = add.ariaLabel = 'Add another peer'; add.hidden = true;
    hostPane.querySelector('.mykos-row-actions').append(add);
    const status = document.createElement('p'); status.role = 'status';
    status.className = 'mykos-status'; status.hidden = true;
    region.append(status);
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
    function attachActions(item) {
        const doc = item.frame.contentDocument;
        const actions = doc?.querySelector('#mykos-host-status-row .mykos-row-actions');
        if (!actions || doc.getElementById('mykos-peer-open-node')) return;
        const link = doc.createElement('button'); link.type = 'button';
        link.id = 'mykos-peer-open-node'; link.className = 'mykos-icon';
        link.title = link.ariaLabel = 'Open full node · ' + item.peer.name;
        const flower = document.querySelector('header .mykos-brand-flower')?.cloneNode(true);
        if (flower) {
            flower.setAttribute('class', 'mykos-peer-flower');
            flower.style.cssText = 'width:15px;height:21px;display:block';
            link.append(flower);
        }
        link.onclick = event => {
            event.stopPropagation();
            // Open through the owning dashboard, preserving the selected node.
            window.open('/mykos/api/web/peers/' + item.peer.id + '/view?full=true', '_blank', 'noopener');
        };
        const remove = doc.createElement('button'); remove.type = 'button';
        remove.id = 'mykos-peer-disconnect'; remove.className = 'mykos-icon'; remove.style.color = '#ef4444';
        remove.title = remove.ariaLabel = 'Disconnect peer · ' + item.peer.name;
        remove.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m3 3 18 18M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>';
        remove.onclick = async event => {
            event.stopPropagation();
            const peer = item.peer;
            if (!confirm('Disconnect peer ' + peer.name + '? This removes only its MYKOS peer VPN and shared controls.')) return;
            remove.disabled = true;
            try { await request('/' + peer.id, {method:'DELETE'}); await refresh(); }
            catch (error) { item.state.hidden = false; item.state.textContent = error.message; remove.disabled = false; }
        };
        const addPeer = doc.createElement('button'); addPeer.type = 'button';
        addPeer.id = 'mykos-peer-add-another'; addPeer.className = 'mykos-icon mykos-add-vpn-button';
        addPeer.title = addPeer.ariaLabel = 'Add another peer on ' + item.peer.name;
        addPeer.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
        addPeer.hidden = !item.peer.origin;
        addPeer.onclick = event => {
            event.stopPropagation();
            // Peer enrollment keeps the existing host-local administrator approval.
            const target = new URL('/mykos/#add-peer', item.peer.origin);
            if (target.protocol === 'https:') window.open(target.href, '_blank', 'noopener');
        };
        actions.append(addPeer, link, remove);
    }
    function render(peer) {
        let item = frames.get(peer.id);
        if (!item) {
            const section = document.createElement('section');
            section.setAttribute('aria-label', 'Peer host · ' + peer.name);
            const state = document.createElement('p'); state.className = 'mykos-status'; state.role = 'status';
            const frame = document.createElement('iframe'); frame.title = 'Complete Host pane · ' + peer.name;
            frame.style.cssText = 'display:block;width:100%;height:420px;border:0;background:transparent';
            section.append(state, frame); region.append(section);
            item = {section,state,frame,peer}; frames.set(peer.id, item);
            frame.addEventListener('load', () => attachActions(item));
        }
        item.peer = peer;
        item.state.hidden = peer.state === 'active' && peer.connected;
        item.state.textContent = peer.name + ' · ' + (peer.expired ? 'Invitation expired' : peer.state === 'pending' ? 'Awaiting acceptance' : 'Offline');
        if (peer.state === 'active' && peer.connected) {
            item.frame.hidden = false;
            if (!item.frame.getAttribute('src')) item.frame.src = '/mykos/api/web/peers/' + peer.id + '/view';
            else attachActions(item);
        } else {
            // Destroy offline documents so stale controls cannot be clicked.
            item.frame.hidden = true; item.frame.removeAttribute('src');
        }
    }
    async function refresh() {
        try {
            const data = await request('');
            allowed = true;
            add.hidden = document.getElementById('mykos-host-row-vpn')?.dataset.hostAction === 'add-vpn';
            status.hidden = true; status.textContent = '';
            const identifiers = new Set(data.peers.map(peer => peer.id));
            for (const [id,item] of frames) if (!identifiers.has(id)) { item.section.remove(); frames.delete(id); }
            data.peers.forEach(render);
            if (location.hash === '#add-peer') {
                history.replaceState(null, '', location.pathname + location.search);
                open();
            }
        } catch (error) {
            status.textContent = allowed ? 'Peer status unavailable' : ''; status.hidden = !allowed;
            for (const item of frames.values()) { item.state.hidden = false; item.state.textContent = item.peer.name + ' · Status unavailable'; item.frame.hidden = true; item.frame.removeAttribute('src'); }
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
