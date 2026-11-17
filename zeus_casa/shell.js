"use strict";
/* Persistent menu and header: the iframe reloads the chosen business page,
   never the application navigation. Same-origin only; legacy deep links work. */
(function(){
 const frame=document.getElementById('zeus-workspace-frame');if(!frame)return;
 const validPaths=new Set(['/home.html','/casa','/bollette','/nuova-bolletta','/percorso','/letture','/analisi','/documenti','/gestione','/immobili','/activity','/settings','/backup','/manutenzione','/debiti','/finance','/bilancio','/vault']);
 let current='/home.html';let syncing=false;
 const contextNav=document.createElement('nav');contextNav.className='zeus-contextnav';contextNav.setAttribute('aria-label','Operazioni della sezione');
 const contextLinks={
  bills:[['/bollette','Elenco bollette'],['/nuova-bolletta','Nuova bolletta'],['/percorso','Verifica e ripartisci'],['/documenti','Documenti'],['/gestione?tab=ripartizioni','Criteri di ripartizione'],['/gestione?tab=chiusure','Chiusura contabile']],
  readings:[['/letture','Letture e QR'],['/analisi','Analisi consumi'],['/percorso?source=letture','Riconciliazioni']],
  reports:[['/gestione?tab=rendiconti','Emetti e consulta'],['/casa#reports','Esportazione storica (non numerata)']],
  dues:[['/casa#arrears','Insoluti'],['/gestione?tab=scadenze','Scadenze'],['/gestione?tab=contabilita','Contabilità'],['/gestione?tab=conguagli','Conguagli']],
  tenants:[['/casa#tenants','Anagrafica inquilini'],['/gestione?tab=inquilini','Storico conduttori']],
  settings:[['/settings','Generali'],['/settings#settings-reading','ZEUS Reading · PIN'],['/immobili','Configura immobili'],['/settings#zeus-backup','Backup e ripristino'],['/manutenzione','Manutenzione dati'],['/activity','Registro attività']]
 };
 function contextGroup(route){
   const u=new URL(route,location.origin),p=u.pathname,t=u.searchParams.get('tab');
   if(['/settings','/immobili','/backup','/manutenzione','/activity'].includes(p))return 'settings';
   if(p==='/casa')return u.hash==='#tenants'?'tenants':u.hash==='#arrears'?'dues':u.hash==='#reports'?'reports':null;
   if(p==='/gestione')return t==='rendiconti'?'reports':t==='inquilini'?'tenants':['scadenze','contabilita','conguagli'].includes(t)?'dues':'bills';
   if(p==='/percorso'&&u.searchParams.get('source')==='letture')return 'readings';
    if(['/bollette','/nuova-bolletta','/percorso','/documenti'].includes(p))return 'bills';
   if(['/letture','/analisi'].includes(p))return 'readings';
   return null;
 }
 function refreshContext(path){
   const group=contextGroup(path),items=contextLinks[group]||[];
   contextNav.replaceChildren();
   for(const [href,label] of items){const a=document.createElement('a');a.href=href;a.textContent=label;
     const selected=path===href||(href==='/settings'&&path==='/settings');
     if(selected){a.classList.add('zeus-context-active');a.setAttribute('aria-current','page');}
     contextNav.appendChild(a);
   }
   contextNav.hidden=!items.length;
   document.body.classList.toggle('zeus-has-context',items.length>0);
 }
 const shellHeader=document.querySelector('.zeus-shellbar');
 if(shellHeader)shellHeader.after(contextNav);else document.body.querySelector('.zeus-workspace')?.before(contextNav);

 function normalize(input){
   try{const u=new URL(input,location.origin);if(u.origin!==location.origin)return null;
   const path=u.pathname==='/'||u.pathname==='/index.html'?u.pathname==='/'?'/home.html':'/casa':u.pathname;
   return validPaths.has(path)?path+u.search+u.hash:null;
   }catch(_e){return null}
 }
 function urlFor(path){return path==='/home.html'?'/':'/?view='+encodeURIComponent(path)}
 let unavailableNotice=null;
 function unavailable(){
   if(!unavailableNotice){
     unavailableNotice=document.createElement('section');
     unavailableNotice.className='zeus-unavailable';unavailableNotice.setAttribute('role','alert');
     const heading=document.createElement('h2');heading.textContent='Sezione non disponibile';
     const details=document.createElement('p');details.textContent='Il collegamento richiesto non corrisponde a una sezione di ZEUS. Nessun dato è stato modificato.';
     const home=document.createElement('a');home.href='/';home.textContent='Torna alla Home ZEUS';
     unavailableNotice.append(heading,details,home);frame.before(unavailableNotice);
   }
   current='';frame.hidden=true;unavailableNotice.hidden=false;
   window.ZEUSNav?.setRoute('/home.html');
   const selected=document.querySelector('.zeus-appnav-globalhome');
   selected?.classList.remove('zeus-appnav-active');selected?.removeAttribute('aria-current');
   const title=document.querySelector('.zeus-shellbar-title');if(title)title.textContent='Sezione non disponibile';
   refreshContext('/home.html');document.title='Sezione non disponibile · ZEUS Casa';
 }
 function setUI(path){current=path;frame.hidden=false;if(unavailableNotice)unavailableNotice.hidden=true;window.ZEUSNav?.setRoute(path);refreshContext(path);document.title=(document.querySelector('.zeus-shellbar-title')?.textContent||'ZEUS')+' · ZEUS Casa'}
 function open(input,mode='push'){
   let path=normalize(input);if(!path){unavailable();return false;}
   const old=current;
   // React Casa uses #overview to return from Tenants/Arrears/Reports.
   if(path==='/casa' && old.split(/[?#]/)[0]==='/casa' && old.includes('#'))path='/casa#overview';
   setUI(path);
   if(mode==='push'&&path!==old)history.pushState({view:path},'',urlFor(path));
   if(mode==='replace')history.replaceState({view:path},'',urlFor(path));
   const oldBase=old.split(/[?#]/)[0],newBase=path.split(/[?#]/)[0];
   if(newBase===oldBase){
     const inside=frame.contentWindow;
     try{if(inside&&inside.location.pathname===newBase){const desired=new URL(path,location.origin);if(inside.location.search!==desired.search){frame.src=path;return true;}if(inside.location.hash!==desired.hash)inside.location.hash=desired.hash;return true;}}catch(_e){}
   }
   frame.src=path;return true;
 }
 document.addEventListener('click',ev=>{
   const anchor=ev.target instanceof Element ? ev.target.closest('a[href]') : null;if(!anchor||ev.defaultPrevented||ev.button!==0||ev.ctrlKey||ev.metaKey||ev.shiftKey||ev.altKey||anchor.target==='_blank'||anchor.hasAttribute('download'))return;
   const url=new URL(anchor.href,location.origin);if(url.origin!==location.origin)return;
   const path=normalize(url.href);if(!path)return;
   ev.preventDefault();window.ZEUSNav?.close();open(path);
 },true);
 window.addEventListener('message',ev=>{
   if(ev.origin!==location.origin||ev.source!==frame.contentWindow||!ev.data||typeof ev.data!=='object')return;
   if(ev.data.type==='zeus-request-navigation'){open(ev.data.path);return}
   if(ev.data.type==='zeus-page-change'){
     const path=normalize(ev.data.path);if(!path)return;
     if(path!==current){setUI(path);history.pushState({view:path},'',urlFor(path))}
   }
 });
 frame.addEventListener('load',async()=>{
   try{
     const path=frame.contentWindow.location.pathname;
     if(path==='/login'||path==='/login.html'){
       location.replace('/login?next='+encodeURIComponent(urlFor(current)));return;
     }
     const resolved=normalize(path+frame.contentWindow.location.search+frame.contentWindow.location.hash);
     if(resolved&&resolved!==current){setUI(resolved);history.replaceState({view:resolved},'',urlFor(resolved));}
   }catch(_e){}
   try{const r=await fetch('/api/session/status',{cache:'no-store'});const auth=await r.json();if(!auth.logged_in)location.replace('/login?next='+encodeURIComponent(urlFor(current)));}catch(_e){}
   const bar=document.querySelector('.zeus-shellbar-end');
   if(bar){bar.querySelectorAll('.zeus-frame-proxy').forEach(el=>el.remove());const mob=document.querySelector('.zeus-appnav-mobilebar');mob?.querySelectorAll('.zeus-frame-proxy').forEach(el=>el.remove());
     try{for(const btn of frame.contentDocument.querySelectorAll('header.head .head-links button')){
       const make=()=>{const proxy=document.createElement('button');proxy.type='button';proxy.className='zeus-frame-proxy zeus-shellbar-action';proxy.textContent=btn.textContent;proxy.addEventListener('click',()=>btn.click());return proxy;};
       bar.prepend(make());if(mob){const m=make();m.classList.add('zeus-mobile-action');mob.appendChild(m)}
     }}catch(_e){}
   }
 });
 window.addEventListener('popstate',()=>{const view=new URLSearchParams(location.search).get('view')||'/home.html';open(view,'none')});
 const initial=new URLSearchParams(location.search).get('view')||'/home.html';
 const target=normalize(initial);
 if(target){setUI(target);history.replaceState({view:target},'',urlFor(target));frame.src=target;}
 else unavailable();
})();
