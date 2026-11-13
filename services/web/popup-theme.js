/* Detached windows share saved appearance without retaining an opener. */
(() => {
 'use strict';
 const root=document.documentElement;
 if(!root.hasAttribute('data-mykos-popup-window'))return;
 let identity='';
 function apply(){
  let theme='dark';
  try{theme=(identity&&localStorage.getItem('mykos-theme:'+identity))||localStorage.getItem('mykos-theme')||'dark';}catch(_){}
  if(!['dark','light','receiver'].includes(theme)||(theme==='receiver'&&identity!=='mgreen'))theme='dark';
  root.dataset.theme=theme;
 }
 window.mykosPopupThemeIdentity=username=>{identity=typeof username==='string'?username:'';apply();};
 window.addEventListener('storage',event=>{if(event.key===null||event.key==='mykos-theme'||event.key==='mykos-theme:'+identity)apply();});
 apply();
 const controller=new AbortController();
 window.addEventListener('pagehide',()=>controller.abort(),{once:true});
 fetch('/mykos/api/terminal/permission?include_user=true',{cache:'no-store',signal:controller.signal})
  .then(response=>response.ok?response.json():null)
  .then(state=>window.mykosPopupThemeIdentity(state?.allowed?state.username:''))
  .catch(()=>{});
})();
