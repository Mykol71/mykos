/* Host-local maintenance; data is rendered as text, never HTML. */
(() => {
  let dialog, timer, lastRendered, sessionId;
  const restartSelection = new Set();
  function scheduleRefresh() {
    clearTimeout(timer);
    if(dialog?.open) timer=setTimeout(()=>refresh().catch(()=>scheduleRefresh()),3000);
  }
  const api = (path, options) => mykosStageJSON('/mykos/api/web/host/maintenance/' + path, options);
  const post = (path, value) => api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(value || {})});
  function node(tag, text, parent) {
    const el = document.createElement(tag); if (text !== undefined) el.textContent = text;
    parent?.append(el); return el;
  }
  function button(label, parent, action) {
    const el = node('button', label, parent); el.type='button'; el.style.margin='8px';
    el.addEventListener('click', async () => {
      el.disabled=true;
      try { const result=await action(); if(result!==false) await refresh(); }
      catch (error) { alert(error.message); }
      finally { el.disabled=false; }
    }); return el;
  }
  async function refresh() {
    if (!dialog?.open) return;
    clearTimeout(timer);
    const state = await api('status');
    if(sessionId!==state.id){sessionId=state.id;restartSelection.clear();}
    const signature=JSON.stringify(state);
    if(lastRendered===signature){scheduleRefresh();return;}
    lastRendered=signature;
    const body=dialog.querySelector('[data-maintenance-body]'); body.replaceChildren();
    node('h2', 'Prepare host for reboot', body);
    node('p', 'State: '+state.state, body);
    if (state.error) node('p', state.error, body);
    if (state.state === 'idle' || state.state === 'recovered' || state.state === 'cancelled') {
      if (state.instances?.length) {
        node('h3', 'Last recovery report', body);
        for (const item of state.instances) node('p', item.name+': '+item.phase+(item.error?' — '+item.error:''), body);
      }
      button('Check readiness', body, async () => {
        const plan = await api('plan'); body.replaceChildren();
        node('h2', 'Readiness: '+plan.hostname, body);
        node('p', plan.warning, body);
        node('p', 'Free: '+Math.floor(plan.free_bytes/1024**3)+' GiB · Required headroom: '+Math.ceil(plan.required_free_bytes/1024**3)+' GiB', body);
        const consent=[];
        for (const item of plan.instances) {
          node('p', item.name+' — '+(item.mode==='memory'?'Save RAM':item.mode==='restart'?'Restart required':'Blocked')+(item.issues.length?' · '+item.issues.join('; '):''),body);
          if (item.mode==='restart' || item.mode==='memory') {
            const label=node('label', undefined, body);const check=node('input',undefined,label);check.type='checkbox';
            label.append(document.createTextNode((item.mode==='memory'?' Optional: restart ':' Required: restart ')+item.name+' without its running memory.'));
            consent.push({code:item.code,check,required:item.mode==='restart'});
          }
        }
        for (const reason of plan.blockers) node('p','Blocked: '+reason,body);
        node('p','Saving will stop these instances. Reboot is a separate action. Keep this page open for progress or return here later.',body);
        const start=button('Save instances and prepare',body,async()=>{
          if(consent.some(x=>x.required && !x.check.checked))throw Error('Confirm every required restart first.');
          const answer=prompt('Type '+plan.hostname+' to save and stop its instances.');
          if(answer!==plan.hostname)throw Error('Hostname confirmation did not match.');
          await post('prepare',{hostname:answer,restart:consent.filter(x=>x.check.checked).map(x=>x.code),plan_token:plan.plan_token});
        });start.disabled=plan.blockers.length>0;
        return false;
      });
      return;
    }
    for (const item of state.instances || []) node('p', item.name+': '+item.phase+(item.mode==='restart'?' (restart accepted)':'')+(item.error?' — '+item.error:''),body);
    if (state.state === 'verifying-reboot') node('p','Rechecking saved snapshots. The host will reboot after verification succeeds. Closing this page does not cancel the request.',body);
    if (state.state === 'ready') {
      node('p','All instances are accounted for and stopped. Saved RAM archives have passed checksum and compression verification.',body);
      button('Reboot prepared host',body,async()=>{
        const answer=prompt('Type '+state.hostname+' to reboot now.');
        if(answer!==state.hostname)throw Error('Hostname confirmation did not match.');
        await mykosStageJSON('/mykos/api/web/host/reboot',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hostname:answer})});
      });
    }
    if (['ready','attention','queued','saving','recovering','resume-requested'].includes(state.state)) button('Resume without reboot / retry recovery',body,()=>post('resume'));
    if(state.state==='attention') {
      const choices=[];
      for(const item of state.instances || []) if(item.phase==='restore-failed' && item.mode==='memory') {
        const label=node('label',undefined,body); const check=node('input',undefined,label);check.type='checkbox';
        check.checked=restartSelection.has(item.code);
        check.addEventListener('change',()=>check.checked?restartSelection.add(item.code):restartSelection.delete(item.code));
        label.append(document.createTextNode(' Accept restart without RAM for '+item.name+' after failed recovery. ')); choices.push({code:item.code,check});
      }
      if(choices.length) button('Recover with selected restarts',body,async()=>{
        const restart=choices.filter(x=>x.check.checked).map(x=>x.code);
        if(!restart.length)throw Error('Select a failed instance and accept losing its saved running memory.');
        const hostname=prompt('Type '+state.hostname+' to accept the selected restarts.');
        if(hostname!==state.hostname)throw Error('Hostname confirmation did not match.');
        await post('resume',{hostname,restart});
      });
    }
    node('p','Recovery snapshots remain available. Failed recovery never silently starts an instance cold.',body);
    scheduleRefresh();
  }
  window.mykosOpenHostMaintenance = async () => {
    if (window.MYKOS_PEER_CONTEXT) { alert('Open the host’s own dashboard to prepare it for reboot.'); return; }
    if(!dialog) {
      dialog=node('dialog',undefined,document.body);
      Object.assign(dialog.style,{maxWidth:'760px',width:'90vw',maxHeight:'85vh',overflow:'auto',color:'#20334a',background:'#f4f7fb',borderRadius:'12px',padding:'24px'});
      dialog.setAttribute('aria-label','Host maintenance');node('div',undefined,dialog).dataset.maintenanceBody='';
      button('Close',dialog,async()=>dialog.close());
      dialog.addEventListener('close',()=>clearTimeout(timer));
    }
    lastRendered=null;
    dialog.showModal();
    try { await refresh(); } catch(error) { dialog.close();alert(error.message); }
  };
})();
