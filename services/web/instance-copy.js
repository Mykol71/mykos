/* Source-preserving peer copy controls; only the owning local dashboard offers them. */
(() => {
    'use strict';
    if (window.MYKOS_PEER_CONTEXT) return;
    const prefix = '/mykos/api/web/';
    window.mykosCopyAllowed = false;
    fetch(prefix + 'instance-transfers/permission', {cache:'no-store'})
        .then(response => response.ok ? response.json() : {allowed:false})
        .then(result => { window.mykosCopyAllowed = result.allowed === true; if (typeof window.mykosRenderInventory === 'function') window.mykosRenderInventory(); })
        .catch(() => { window.mykosCopyAllowed = false; });
    async function request(path, value) {
        const response = await fetch(prefix + path, {method: value === undefined ? 'GET' : 'POST',
            headers: value === undefined ? {} : {'Content-Type':'application/json'},
            body: value === undefined ? undefined : JSON.stringify(value), cache:'no-store'});
        const result = await response.json();
        if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Copy request failed');
        return result;
    }
    window.mykosCopyInstance = async function(code) {
        const dialog = document.createElement('dialog');
        dialog.className='mykos-dialog';
        dialog.style.cssText = 'max-width:32rem;width:90%';
        const heading = document.createElement('h2'); heading.textContent = 'Copy instance ' + code + ' to a peer';
        const description = document.createElement('p'); description.textContent = 'The source stays in place. The new copy starts with guest networking isolated to protect both instances from duplicate IP addresses and VPN identities.';
        const peerLabel = document.createElement('label'); peerLabel.textContent = 'Destination peer ';
        const select = document.createElement('select'); select.setAttribute('aria-label','Destination peer'); peerLabel.append(select);
        const idLabel = document.createElement('label'); idLabel.textContent = ' Destination instance ID ';
        const id = document.createElement('input'); id.value = code; id.inputMode = 'numeric'; id.maxLength = 8; id.pattern = '[0-9]{8}'; id.setAttribute('aria-label','Destination instance ID'); idLabel.append(id);
        const history = document.createElement('p');
        const status = document.createElement('p'); status.setAttribute('role','status'); status.setAttribute('aria-live','polite');
        const submit = document.createElement('button'); submit.textContent = 'Check copy'; submit.type = 'button'; submit.disabled = true;
        const close = document.createElement('button'); close.textContent = 'Close'; close.type = 'button'; close.style.marginLeft = '12px'; close.onclick = () => dialog.close();
        dialog.append(heading,description,peerLabel,document.createElement('br'),idLabel,history,status,submit,close);
        document.body.append(dialog); dialog.addEventListener('close', () => dialog.remove(), {once:true}); dialog.showModal();
        let plan = null;
        const changed = () => { plan = null; submit.textContent = 'Check copy'; status.textContent = ''; };
        select.addEventListener('change',changed); id.addEventListener('input',changed);
        try {
            const [listing,recent] = await Promise.all([request('peers'),request('instance-transfers')]);
            const jobs = (recent.transfers || []).filter(job => job.code === code).slice(0,3);
            if (jobs.length) history.textContent = 'Recent copies: ' + jobs.map(job => job.destination + ' — ' + job.state).join('; ');
            for (const peer of listing.peers || []) {
                if (peer.state !== 'active' || !peer.connected) continue;
                const option = document.createElement('option'); option.value = peer.id; option.textContent = peer.name + ' (' + peer.ip + ')'; select.append(option);
            }
            submit.disabled = !select.options.length;
            if (submit.disabled) status.textContent = 'Connect a peer before copying an instance.';
        } catch (error) { status.textContent = error.message; }
        submit.onclick = async () => {
            submit.disabled = true;
            try {
                if (!/^[0-9]{8}$/.test(id.value)) throw new Error('Enter an eight-digit destination ID.');
                if (!plan) {
                    status.textContent = 'Checking destination and compatibility…';
                    plan = await request('instance-transfers/' + code + '/plan', {peer:select.value,destination:id.value});
                    status.textContent = plan.explanation;
                    submit.textContent = plan.mode === 'memory' ? 'Copy with RAM state' : 'Copy and restart';
                    return;
                }
                select.disabled = true; id.disabled = true;
                const job = await request('instance-transfers', {plan}); plan = null;
                submit.hidden = true;
                status.textContent = 'Copy queued. You can close this dialog; the copy continues on the host.';
                while (dialog.open) {
                    await new Promise(resolve => setTimeout(resolve,2000));
                    if (!dialog.open) break;
                    const current = await request('instance-transfers/' + job.token);
                    const percent = current.size ? ' ' + Math.floor(100*(current.offset||0)/current.size) + '%' : '';
                    status.textContent = current.state === 'ready' ? 'Copy verified on the peer. Its guest networking is isolated; the source remains unchanged.' : current.state === 'failed' ? 'Copy failed: ' + current.error + (current.destination_outcome_unknown ? ' The destination may still finish; check its status before starting another copy.' : '') : 'Copy: ' + current.state + percent + '. The source stays in place.';
                    if (['ready','failed','cancelled','expired'].includes(current.state)) break;
                }
            } catch (error) { plan = null; status.textContent = error.message; submit.textContent = 'Check copy'; }
            finally { if (dialog.open && !submit.hidden) submit.disabled = false; }
        };
    };
})();
