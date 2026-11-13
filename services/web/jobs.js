/* Host-local job history and recovery. All server data is rendered as text. */
(() => {
  const $ = id => document.getElementById(id);
  let timer, busy = false, pending = false, opened = false, allowed = false, lastGood = null, expanded = false, epoch = 0, staleData = false, dialog = null, closeButton = null, positionNavigation = () => {};
  const root = '/mykos/api/web/jobs';
  function node(tag, text, parent) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = String(text);
    parent?.append(el); return el;
  }
  function clear() {
    epoch++;
    allowed = false; lastGood = null; opened = false; staleData = false; positionNavigation();
    document.querySelectorAll('.mykos-jobs-dialog').forEach(item => { item.close(); item.remove(); });
    if(dialog?.open) dialog.close();
    $('mykos-jobs-link').hidden = true; $('mykos-jobs-title').hidden = true; $('mykos-jobs-pane').hidden = true;
    $('mykos-jobs-rows').replaceChildren(); $('mykos-jobs-count').textContent = ''; $('mykos-jobs-status').textContent = 'Jobs status unavailable';
  }
  function button(label, parent, action) {
    const el = node('button', label, parent); el.type = 'button';
    el.addEventListener('click', action); return el;
  }
  function age(value) {
    if (!Number.isFinite(value)) return 'Activity time unavailable';
    const seconds = Math.max(0, Math.floor(Date.now()/1000-value));
    return seconds < 60 ? seconds+'s ago' : seconds < 3600 ? Math.floor(seconds/60)+'m ago' : Math.floor(seconds/3600)+'h ago';
  }
  function label(job) {
    return (job.kind === 'repository-sync' ? 'Repository sync' : job.kind === 'vm-build' ? 'VM image build' : 'Native image build')+' · '+job.target;
  }
  function confirmRetry(job, discard = false) {
    if (pending) return;
    const dialog = document.createElement('dialog'); dialog.className = 'mykos-dialog mykos-jobs-dialog';
    node('h2', discard ? 'Discard saved downloads' : job.kind === 'vm-build' ? 'Resume image build' : 'Retry job', dialog);
    node('p', label(job), dialog);
    node('p', discard ? 'Remove only the private failed-download cache for this job. Live repositories, images and user data are preserved. A later retry downloads missing files again.' : job.kind === 'repository-sync' ? 'Retry the failed selection. Saved downloads are checked again before reuse; incomplete repositories stay private.' : job.kind === 'vm-build' ? 'Resume the saved unfinished installation using its existing disks and credentials. This does not replace a prepared image.' : 'Retry this native build using its original image selection. An existing image will not be replaced.', dialog);
    const error = node('p', '', dialog); error.setAttribute('role','alert');
    const controls = node('div', undefined, dialog); controls.className = 'mykos-jobs-controls';
    const cancel = button('Cancel', controls, () => dialog.close());
    const submit = button(discard ? 'Discard saved downloads' : job.kind === 'vm-build' ? 'Resume saved build' : 'Retry failed job', controls, async () => {
      submit.disabled = true; cancel.disabled = true; pending = true;
      try {
        await mykosStageJSON(root+'/'+encodeURIComponent(job.id)+(discard ? '/saved-downloads/discard' : '/retry'), {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(discard ? {revision:String(job.revision),confirmation:'DISCARD'} : {revision:String(job.revision)})});
        dialog.close(); await refresh();
      } catch (problem) { error.textContent = problem.message; }
      finally { pending = false; submit.disabled = false; cancel.disabled = false; }
    });
    const trigger = document.activeElement;
    dialog.setAttribute('aria-label', discard ? 'Discard saved downloads' : 'Retry job');
    dialog.addEventListener('cancel', event => { if(pending) event.preventDefault(); });
    dialog.addEventListener('close', () => { dialog.remove(); if(trigger?.isConnected) trigger.focus(); }, {once:true});
    document.body.append(dialog); dialog.showModal(); cancel.focus();
  }
  function render(data, stale = staleData) {
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    const active = jobs.filter(job => ['running','queued'].includes(job.state));
    const attention = jobs.filter(job => ['failed','interrupted','unknown'].includes(job.state) && !job.retry_child);
    $('mykos-jobs-link').hidden = !allowed; positionNavigation();
    $('mykos-jobs-count').textContent = active.length || attention.length ? ' ('+active.length+' running'+(attention.length?', '+attention.length+' need attention':'')+')' : '';
    const visible = allowed && opened;
    $('mykos-jobs-title').hidden = !visible; $('mykos-jobs-pane').hidden = !visible;
    $('mykos-jobs-status').textContent = stale ? 'Status unavailable · showing the last successful update; actions disabled' : jobs.length+' recent jobs · '+active.length+' running · '+attention.length+' need attention';
    const rows = $('mykos-jobs-rows');
    const openDetails = new Set([...rows.querySelectorAll('article[data-job-id]')].filter(card => card.querySelector('details[open]')).map(card => card.dataset.jobId));
    const focused = document.activeElement?.closest('article[data-job-id]');
    const focusId = focused?.dataset.jobId;
    const focusLabel = document.activeElement?.textContent;
    rows.replaceChildren();
    const sorted = [...jobs].sort((a,b) => {
      const rank = v => ['running','queued'].includes(v.state) ? 0 : ['failed','interrupted','unknown'].includes(v.state) && !v.retry_child ? 1 : 2;
      return rank(a)-rank(b) || (b.created_at || b.activity_at || 0)-(a.created_at || a.activity_at || 0);
    });
    const shown = expanded ? sorted : sorted.slice(0,10);
    for (const job of shown) {
      const card = node('article', undefined, rows); card.className = 'mykos-job'; card.dataset.state = job.state; card.dataset.jobId = job.id;
      const heading = node('div', undefined, card); heading.className = 'mykos-job-heading';
      node('strong', label(job), heading);
      const state = node('span', job.retry_child ? 'Retried' : job.state, heading); state.className = 'mykos-job-state';
      const body = node('div', undefined, card); body.className = 'mykos-job-body';
      node('p', job.message || 'Waiting for job status', body);
      const counts = job.progress;
      if (counts && Number.isFinite(counts.completed) && Number.isFinite(counts.total) && counts.total > 0 && counts.completed <= counts.total) {
        const bar = node('progress', undefined, body); bar.max = counts.total; bar.value = counts.completed; bar.setAttribute('aria-label', 'Current component: '+counts.completed+' of '+counts.total+' packages');
        node('small', counts.completed+' / '+counts.total+' packages in this component', body);
      } else if (['running','queued'].includes(job.state)) {
        const bar = node('progress', undefined, body); bar.removeAttribute('value'); bar.setAttribute('aria-label','Working; total progress is not yet available');
      }
      const activity = node('small', 'Last activity: '+age(job.activity_at), body);
      if (Number.isFinite(job.activity_at)) activity.title = new Date(job.activity_at*1000).toLocaleString();
      const detail = node('details', undefined, body); detail.open = openDetails.has(job.id); node('summary', 'Details', detail);
      node('p', 'Phase: '+job.phase, detail);
      if(job.component) node('p', 'Repository component: '+job.component, detail);
      if(Number.isFinite(job.cache_verified)) node('p', 'Saved packages checked: '+job.cache_verified, detail);
      if(Number.isFinite(job.cache_rejected) && job.cache_rejected) node('p', 'Invalid saved packages removed for download: '+job.cache_rejected, detail);
      if(job.saved_downloads) node('p', 'Saved download candidates: '+job.saved_downloads.packages+' packages · '+(job.saved_downloads.bytes/1024/1024).toFixed(1)+' MiB. Signatures and fresh metadata are checked on retry.', detail);
      if(job.cache_unavailable) node('p', 'Saved-download status unavailable', detail);
      if (Number.isFinite(job.exit_code)) node('p', 'Worker exit status: '+job.exit_code, detail);
      if (job.created_at) node('p', 'Started: '+new Date((job.started_at || job.created_at)*1000).toLocaleString(), detail);
      if (job.finished_at) node('p', 'Finished: '+new Date(job.finished_at*1000).toLocaleString(), detail);
      if (job.observed) node('p', 'Status comes from an existing worker. Recovery controls are offered only when its state is known.', detail);
      for (const event of (Array.isArray(job.events) ? job.events.slice(-20) : [])) node('p', event.message, detail);
      if(job.saved_downloads && ['failed','interrupted','succeeded'].includes(job.state)) {
        const discard = button('Discard saved downloads', detail, () => confirmRetry(job,true));discard.disabled = stale || pending;
      }
      if (job.retry_allowed) {
        const control = button(job.kind === 'vm-build' ? 'Resume' : 'Retry', body, () => confirmRetry(job));
        control.disabled = stale || pending;
      }
    }
    if(focusId) {
      const card=[...rows.querySelectorAll('article[data-job-id]')].find(el => el.dataset.jobId===focusId);
      const control=card && [...card.querySelectorAll('button,summary')].find(el => el.textContent===focusLabel);
      control?.focus({preventScroll:true});
    }
    if (!jobs.length) node('p', 'No recorded builds or repository syncs yet. New jobs appear here automatically.', rows);
    $('mykos-jobs-more').hidden = sorted.length <= 10;
    $('mykos-jobs-more').textContent = expanded ? 'Show fewer jobs' : 'Show all recent jobs';
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 5000); }
  async function refresh() {
    if (busy) return;
    if (window.MYKOS_PEER_CONTEXT?.compact || document.hidden || $('dashboard')?.hidden) { schedule(); return; }
    busy = true; const requestEpoch = epoch;
    $('mykos-jobs-refresh').disabled = true;
    $('mykos-jobs-rows').setAttribute('aria-busy','true');
    if(opened) $('mykos-jobs-status').textContent = lastGood ? 'Refreshing jobs… showing the last update' : 'Loading job status…';
    const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(root, {cache:'no-store',signal:controller.signal});
      if (requestEpoch !== epoch || $('dashboard')?.hidden) return;
      if ([401,403].includes(response.status)) { clear(); return; }
      if (!response.ok) throw new Error('Jobs status unavailable');
      const data = await response.json(); if(requestEpoch !== epoch || $('dashboard')?.hidden) return; allowed = true; staleData = false; lastGood = data; render(data);
    } catch (_) {
      if (requestEpoch !== epoch) return;
      staleData = true;
      if (lastGood) render(lastGood, true);
      else if (allowed) $('mykos-jobs-status').textContent = 'Jobs status unavailable. Retry refresh.';
    } finally { clearTimeout(timeout); busy = false; $('mykos-jobs-refresh').disabled = false; $('mykos-jobs-rows').removeAttribute('aria-busy'); schedule(); }
  }
  function init() {
    if (!$('mykos-jobs-link')) return;
    // A modal stays anchored while inventory panes above it grow during startup.
    const link = $('mykos-jobs-link');
    const home = document.createComment('Jobs navigation home'); link.before(home);
    dialog = node('dialog'); dialog.className = 'mykos-dialog mykos-jobs-window';
    dialog.id = 'mykos-jobs-window'; dialog.setAttribute('aria-labelledby','mykos-jobs-title');
    const toolbar = node('div', undefined, dialog); toolbar.className = 'mykos-jobs-window-controls';
    closeButton = button('Close jobs', toolbar, () => dialog.close());
    dialog.append($('mykos-jobs-title'), $('mykos-jobs-pane')); document.body.append(dialog);
    dialog.addEventListener('close', () => { opened = false; if(lastGood) render(lastGood); if(allowed && !$('dashboard')?.hidden) link.focus({preventScroll:true}); });
    const mobileQuery = window.matchMedia('(max-width:767px), (hover:none) and (pointer:coarse)');
    const mobileNav = node('nav'); mobileNav.className = 'mykos-jobs-mobile-nav'; mobileNav.hidden = true; mobileNav.setAttribute('aria-label','Jobs status');
    document.querySelector('#dashboard main').prepend(mobileNav);
    function navigationTheme() {
      const compactNavigation = mobileQuery.matches || document.documentElement.dataset.theme === 'receiver';
      mobileNav.hidden = !allowed || !compactNavigation;
      if(compactNavigation) mobileNav.append(link);
      else home.after(link);
    }
    positionNavigation = navigationTheme; mobileQuery.addEventListener('change',navigationTheme);
    new MutationObserver(navigationTheme).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']}); navigationTheme();
    link.addEventListener('click', event => {
      event.preventDefault(); if(!allowed) return;
      opened = true;
      if(lastGood) render(lastGood, staleData);
      else {
        $('mykos-jobs-title').hidden = false; $('mykos-jobs-pane').hidden = false;
        $('mykos-jobs-status').textContent = 'Loading job status…';
      }
      if(!dialog.open) dialog.showModal(); closeButton.focus();
      if(busy) $('mykos-jobs-status').textContent = lastGood ? 'Refreshing jobs… showing the last update' : 'Loading job status…';
      refresh();
    });
    $('mykos-jobs-refresh').addEventListener('click', refresh);
    $('mykos-jobs-more').addEventListener('click', () => { expanded = !expanded; if(lastGood) render(lastGood); });
    $('logout').addEventListener('click', () => { opened = false; clear(); document.querySelectorAll('.mykos-jobs-dialog').forEach(dialog => dialog.close()); });
    const dashboard = $('dashboard');
    if(dashboard) new MutationObserver(() => { if(dashboard.hidden) clear(); else refresh(); }).observe(dashboard,{attributes:true,attributeFilter:['hidden']});
    window.addEventListener('pagehide',clear);
    document.addEventListener('visibilitychange', () => { if(!document.hidden) refresh(); });
    refresh();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true}); else init();
})();
