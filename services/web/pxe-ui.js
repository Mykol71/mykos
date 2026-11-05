(()=>{
'use strict';
if(window.MYKOS_PEER_CONTEXT?.compact)return;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={windows11:'Windows'};
let current={enabled:false,jobs:[],profiles:['windows11']};
const panes={windows11:'windows',...{}};
window.mykosPXEFamilyVisible=family=>current.enabled&&Object.values(panes).includes(family);
const sections=new Map();
for(const [profile,family] of Object.entries(panes)){
 const panel=document.getElementById(family+'-mykos-container-panel');if(!panel)continue;
 const area=document.createElement('section');area.className='mykos-pxe-desktops';area.style.cssText='margin-top:8px;border-top:1px solid #334155;padding-top:8px';
 area.innerHTML='<div class="row"><strong>PXE desktops</strong><span><button type="button" data-pxe="refresh">Refresh</button> <button type="button" data-pxe="arm">Provision desktop</button></span></div><p class="pxe-status" role="status"></p><div class="mykos-table-wrap"><table><thead><tr><th>ID</th><th>Status</th><th>Version</th><th>MAC</th><th>Hostname</th><th>Type</th><th>Actions</th></tr></thead><tbody></tbody></table></div>';
 panel.append(area);sections.set(profile,area);
 area.querySelector('[data-pxe=refresh]').onclick=()=>refresh();
 area.querySelector('[data-pxe=arm]').onclick=()=>open(profile);
}
const dialog=document.createElement('dialog');dialog.className='mykos-dialog';dialog.setAttribute('aria-label','Provision a PXE desktop');
dialog.innerHTML='<form><h2>Provision desktop</h2><p>Installs Windows 11 on the identified desktop. A matching MAC and an approved staging subnet are required.</p><label>MAC <input name="mac" required pattern="[0-9A-Fa-f:-]{17}" maxlength="17"></label><label>Customer ID <input name="customer" required pattern="[0-9]{8}" maxlength="8"></label><label>Hostname <input name="hostname" maxlength="15" placeholder="Automatic"></label><label>Local password <input name="password" type="password" autocomplete="new-password" required minlength="12" maxlength="128"></label><label>Lifetime (minutes) <input name="minutes" type="number" min="5" max="1440" value="120" required></label><label>WIM index <input name="image_index" readonly type="number" min="1" max="100" value="1" required></label><label><input name="allow_wipe" type="checkbox"> Allow erasing existing partitions on this target</label><label>Repeat MAC to authorize erase <input name="confirmation" maxlength="17"></label><p role="status"></p><button type="submit">Arm this desktop</button> <button type="button" data-close>Cancel</button></form>';
document.body.append(dialog);let chosen='windows11',busy=false,loadError='',readinessMessage='';
const requiredAssets=['boot.wim','install.wim','wimboot','curl.exe','curl-ca-bundle.crt','mykos-pe-guard.exe','chrome.msi'];
function readiness(){if(loadError)return loadError;if(!current.enabled)return 'PXE server setup is incomplete. An administrator must import approved Windows 11 media and configure the staging LANs on MYKOS.';if(current.readiness_error)return current.readiness_error;const missing=requiredAssets.filter(name=>!current.assets?.[name]);return missing.length?'PXE server is missing required assets: '+missing.join(', '):'';}
function updateReadiness(){const reason=readiness();dialog.querySelector('[type=submit]').disabled=busy||!!reason;const status=dialog.querySelector('[role=status]');if(reason||status.textContent===readinessMessage)status.textContent=reason;readinessMessage=reason;}
dialog.querySelector('[data-close]').onclick=()=>{if(!busy){dialog.querySelector('form').reset();dialog.close();}};
function open(profile){chosen=profile;dialog.querySelector('form').reset();dialog.querySelector('[name=image_index]').value=current.media_index||1;dialog.querySelector('h2').textContent='Provision '+labels[profile];dialog.querySelector('[role=status]').textContent='';updateReadiness();dialog.showModal();}
async function api(path,options){const response=await fetch('/mykos/api/web/pxe'+path,{cache:'no-store',...options});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'PXE request failed');return data;}
dialog.querySelector('form').onsubmit=async event=>{
 event.preventDefault();if(busy)return;const reason=readiness();if(reason){updateReadiness();return;}const form=event.target;const data=Object.fromEntries(new FormData(form));data.profile=chosen;data.allow_wipe=form.elements.allow_wipe.checked;data.minutes=Number(data.minutes);data.image_index=Number(data.image_index);data.hostname=data.hostname||null;
 busy=true;form.querySelector('[type=submit]').disabled=true;
 try{await api('/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});form.reset();dialog.close();await refresh();}
 catch(error){dialog.querySelector('[role=status]').textContent=error.message;}
 finally{delete data.password;form.elements.password.value='';busy=false;updateReadiness();}
};
async function refresh(){
 try{
  current=await api('');loadError='';if(dialog.open)updateReadiness();
  for(const [profile,area] of sections){
   area.hidden=false;const missing=requiredAssets.filter(name=>!current.assets?.[name]);
   area.querySelector('[data-pxe=arm]').disabled=false;area.querySelector('[data-pxe=arm]').title=readiness()||'Stage an explicitly identified physical Windows desktop';
   area.querySelector('.pxe-status').textContent=current.enabled?(missing.length?'Import required PXE assets: '+missing.join(', '):'PXE ready · Windows 11 · WIM index '+(current.media_index||1)+'. Physical power and RAM state are not monitored.'):'PXE is not configured. Import approved Windows 11 media and configure the staging LAN using sudo /usr/local/sbin/mykos-pxe.';
   const jobs=current.jobs.filter(job=>job.profile===profile);const rows=area.querySelector('tbody');rows.replaceChildren();
   for(const job of jobs){
    const tr=document.createElement('tr');tr.innerHTML='<td>'+esc(job.customer)+'</td><td>'+esc(job.state==='complete'?'Provisioned':job.state)+'</td><td>Windows 11</td><td>'+esc(job.mac)+'</td><td>'+esc(job.hostname||'—')+'</td><td>Physical · PXE</td><td></td>';
    if(['armed','booting','applying','firstboot'].includes(job.state)){const button=document.createElement('button');button.textContent='Revoke';button.title='Revoke this staging lease';button.onclick=async()=>{if(!confirm('Revoke PXE staging for '+job.mac+'?'))return;button.disabled=true;try{await api('/jobs/'+encodeURIComponent(job.id)+'/revoke',{method:'POST'});await refresh();}catch(error){area.querySelector('.pxe-status').textContent=error.message;button.disabled=false;}};tr.lastElementChild.append(button);}
    rows.append(tr);
   }
   if(!jobs.length){const tr=document.createElement('tr');tr.innerHTML='<td colspan="7">No '+esc(labels[profile])+' PXE desktops.</td>';rows.append(tr);}
   if(current.enabled){const family=panes[profile];const heading=document.getElementById(family+'-mykos-containers-pane');if(heading&&document.documentElement.dataset.role!=='rti'){heading.hidden=false;area.closest('.panel').hidden=false;const link=document.querySelector('.mykos-section-links a[href="#'+family+'-mykos-containers-pane"]');if(link)link.hidden=false;}}
  }
 }catch(error){loadError=error.message;if(dialog.open)updateReadiness();for(const area of sections.values()){if(/administrator|login|session/i.test(error.message))area.hidden=true;else area.querySelector('.pxe-status').textContent=error.message;}}
}
setInterval(()=>{if(!document.hidden)refresh();},15000);refresh();
})();
