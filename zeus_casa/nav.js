"use strict";
/* ZEUS v1.7.5 – one persistent application shell; every business page remains independent. */
(function(){
  const routes=new Set(['/home.html','/casa','/index.html','/bollette','/nuova-bolletta','/percorso','/letture','/analisi','/documenti','/gestione','/immobili','/activity','/settings','/backup','/manutenzione','/debiti','/finance','/bilancio','/vault']);
  const inShell=document.documentElement.dataset.zeusShell==='true';
  const embedded=window.parent!==window;
  if(embedded){
    /* The parent shell owns the only real menu and header. The embedded page
       retains its original styles, JS, API calls, forms, modals and downloads. */
    document.body.classList.add('zeus-navigation','zeus-embedded-page');
    const report=()=>{try{window.parent.postMessage({type:'zeus-page-change',path:location.pathname+location.search+location.hash},location.origin)}catch(_e){}};
    window.addEventListener('hashchange',report);
    window.addEventListener('zeus-navigation-change',report);
    window.addEventListener('popstate',report);
    document.addEventListener('click',ev=>{
      const a=ev.target instanceof Element ? ev.target.closest('a[href]') : null;if(!a||ev.defaultPrevented||ev.button!==0||ev.ctrlKey||ev.metaKey||ev.altKey||ev.shiftKey||a.hasAttribute('download')||a.target==='_blank')return;
      const u=new URL(a.href,location.href);
      if(u.origin!==location.origin||!routes.has(u.pathname))return;
      ev.preventDefault();window.parent.postMessage({type:'zeus-request-navigation',path:u.pathname+u.search+u.hash},location.origin);
    });
    // The legacy Casa React screen needs its hidden sidebar button to open hash panels.
    if(document.getElementById('root')){
      const panels={'#overview':'Panoramica','#tenants':'Anagrafica inquilini','#arrears':'Insoluti e scadenze','#reports':'Rendiconti PDF','#settings':'Impostazioni','#bills':'Bollette'};
      let last='';
      const select=()=>{
        const title=panels[location.hash];if(!title||last===location.hash)return;
        const button=Array.from(document.querySelectorAll('#root .sidebar nav button')).find(el=>el.getAttribute('aria-label')===title||el.getAttribute('title')===title);
        if(button){last=location.hash;button.click()}
      };
      window.addEventListener('hashchange',()=>{last='';select()});
      const root=document.getElementById('root');new MutationObserver(()=>{if(!last&&location.hash)select()}).observe(root,{childList:true,subtree:true});
      select();
    }
    report();return;
  }
  if(!inShell && routes.has(location.pathname)){
    /* A saved legacy deep link enters the persistent shell too. */
    const path=location.pathname==='/index.html'?'/casa':location.pathname==='/home.html'?'/home.html':location.pathname;
    location.replace('/?view='+encodeURIComponent(path+location.search+location.hash));return;
  }
  const modules = [
    {key:'casa',home:'/casa', title:'ZEUS Casa', icon:'⌂', sections:[
      {title:'GESTIONE CASA',items:[
        ['/bollette','Bollette','▣'],
        ['/letture','Contatori e consumi','◫'],
        ['/gestione?tab=rendiconti','Rendiconti','▤'],
        ['/casa#arrears','Pagamenti e scadenze','◷'],
        ['/casa#tenants','Immobili e inquilini','⌂']
      ]}
    ]},
    {key:'lending',home:'/debiti', title:'ZEUS Lending', icon:'◈', sections:[
      {title:'PRESTITI E AMMORTAMENTI',items:[['/debiti#loans','Finanziamenti e piani','▤'],['/debiti#movements','Bonifici e movimenti','⇄'],['/debiti#interest','Prospetti mensili','▥']]}
    ]},
    {key:'finance',home:'/finance',title:'ZEUS Finance',icon:'▤',sections:[
      {title:'GESTIONE FINANZIARIA',items:[['/bilancio','Bilancio e categorie','▦'],['/finance#f-chart','Andamento annuale','▥'],['/finance#f-ledger','Movimenti effettivi','⇄'],['/finance#f-schedules','Programmazioni','◷'],['/finance#f-due','Scadenze e rate','▤']]}
    ]},
    {key:'vault',home:'/vault',title:'ZEUS Vault',icon:'▣',sections:[
      {title:'ARCHIVIO PERSONALE',items:[['/vault#documents','Documenti','▤'],['/vault#folders','Fascicoli','▱'],['/vault#deadlines','Scadenze','◷'],['/vault#trash','Cestino','▧']]}
    ]}
  ];
  const currentPath=location.pathname.replace(/\.html$/,'')||'/';
  const resolvedPath=currentPath==='/index'?'/casa':currentPath;
  const currentModule=resolvedPath==='/debiti'?'lending':(resolvedPath==='/finance'||resolvedPath==='/bilancio')?'finance':resolvedPath==='/vault'?'vault':(resolvedPath==='/'||['/settings','/immobili','/activity','/backup','/manutenzione'].includes(resolvedPath)?null:'casa');
  const sidebar=document.createElement('aside');
  sidebar.className='zeus-appnav';sidebar.id='zeus-appnav';sidebar.setAttribute('aria-label','Menu ZEUS Casa');
  const brand=document.createElement('a');brand.href='/';brand.className='zeus-appnav-brand';brand.title='Torna alla Home generale ZEUS';
  brand.innerHTML='<span class="zeus-appnav-icon" aria-hidden="true">⌂</span><span>ZEUS <span class="zeus-appnav-light">Suite</span><small>CASA · LENDING · FINANCE · VAULT</small></span>';
  sidebar.appendChild(brand);
  const navigation=document.createElement('nav');navigation.className='zeus-appnav-links';navigation.setAttribute('aria-label','Moduli ZEUS');
  const homeMenu=document.createElement('a');homeMenu.href='/';homeMenu.className='zeus-appnav-link zeus-appnav-globalhome';homeMenu.innerHTML='<span class="zeus-appnav-symbol" aria-hidden="true">⌂</span><span>Home ZEUS</span>';navigation.appendChild(homeMenu);
  if(resolvedPath==='/'){homeMenu.classList.add('zeus-appnav-active');homeMenu.setAttribute('aria-current','page')}
  const accordion=new Map();
  let expanded=currentModule;
  const NAV_SCROLL_KEY='zeus-nav-scroll-v174';
  const NAV_MODULE_KEY='zeus-nav-expanded-v174';
  if(!currentModule){try{expanded=sessionStorage.getItem(NAV_MODULE_KEY)||null;}catch(_e){}}
  // Keep the current module visible; the global Home starts with all modules collapsed.
  function sectionHref(route){
    const url=new URL(route,location.origin),p=url.pathname,t=url.searchParams.get('tab');
    if(p==='/settings'||['/immobili','/activity','/backup','/manutenzione'].includes(p))return '/settings';
    if(p==='/casa'&&url.hash==='#arrears')return '/casa#arrears';
    if(p==='/casa'&&url.hash==='#tenants')return '/casa#tenants';
    if(p==='/casa'&&url.hash==='#reports')return '/gestione?tab=rendiconti';
    if(p==='/gestione')return t==='rendiconti'?'/gestione?tab=rendiconti':t==='inquilini'?'/casa#tenants':(['scadenze','contabilita','conguagli'].includes(t)?'/casa#arrears':'/bollette');
    if(p==='/percorso'&&url.searchParams.get('source')==='letture')return '/letture';
    if(['/nuova-bolletta','/percorso','/documenti'].includes(p))return '/bollette';
    if(p==='/analisi')return '/letture';
    return p==='/casa'?'/casa':p;
  }
  function currentHref(){return resolvedPath+location.search+(location.hash||'')}
  function activeFor(href){
    const value=sectionHref(currentHref());
    if(href==='/casa')return value==='/casa'||value==='/casa#overview';
    return href===value || (!href.includes('#')&&href===resolvedPath&&!location.hash);
  }
  function refreshActive(){
    for(const a of navigation.querySelectorAll('a.zeus-appnav-modulebtn')){
      const on=a.getAttribute('href')===({casa:'/casa',lending:'/debiti',finance:'/finance',vault:'/vault'}[currentModule]);
      a.classList.toggle('zeus-appnav-module-selected',on);
    }
    for(const a of navigation.querySelectorAll('a.zeus-appnav-link')){
      const on=activeFor(a.getAttribute('href'));
      a.classList.toggle('zeus-appnav-active',on);
      if(on)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
    }
  }
  function expand(key,remember=true){
    expanded=key;
    for(const [k,{button,content}] of accordion){
      const open=k===key;
      button.setAttribute('aria-expanded',String(open));
      button.classList.toggle('zeus-appnav-module-open',open);
      content.hidden=!open;
    }
    if(remember){try{sessionStorage.setItem(NAV_MODULE_KEY,key||'')}catch(_e){}}
  }
  for(const mod of modules){
    const block=document.createElement('section');block.className='zeus-appnav-module';
    const button=document.createElement('a');button.href=mod.home;button.className='zeus-appnav-modulebtn';
    button.setAttribute('aria-controls','zeus-nav-'+mod.key);button.setAttribute('aria-expanded','false');
    const symbol=document.createElement('span');symbol.className='zeus-appnav-module-icon';symbol.textContent=mod.icon;symbol.setAttribute('aria-hidden','true');
    const name=document.createElement('span');name.className='zeus-appnav-modulename';name.textContent=mod.title;
    button.append(symbol,name);
    const children=document.createElement('div');children.id='zeus-nav-'+mod.key;children.className='zeus-appnav-children';children.hidden=true;
    for(const section of mod.sections){
      const heading=document.createElement('p');heading.className='zeus-appnav-heading';heading.textContent=section.title;children.appendChild(heading);
      for(const [href,label,iconText] of section.items){
        const link=document.createElement('a');link.href=href;link.className='zeus-appnav-link';
        const icon=document.createElement('span');icon.className='zeus-appnav-symbol';icon.setAttribute('aria-hidden','true');icon.textContent=iconText;
        const txt=document.createElement('span');txt.textContent=label;link.append(icon,txt);children.appendChild(link);
      }
    }
    // A click navigates to the module overview and expands the operations.
    button.addEventListener('click',()=>expand(mod.key));
    block.append(button,children);navigation.appendChild(block);accordion.set(mod.key,{button,content:children});
  }
  expand(expanded,false);refreshActive();
  if(inShell){window.ZEUSNav={setRoute(route){
    const path=route.split('#')[0].split('?')[0];
    const module=path==='/debiti'?'lending':(path==='/finance'||path==='/bilancio')?'finance':path==='/vault'?'vault':path==='/home.html'?null:['/settings','/immobili','/activity','/backup','/manutenzione'].includes(path)?null:'casa';
    expand(module,false);
    for(const [key,group] of accordion){
      group.button.classList.toggle('zeus-appnav-module-selected',key===module);
    }
    for(const a of navigation.querySelectorAll('a.zeus-appnav-link')){
      const href=a.getAttribute('href');const on=href===sectionHref(route) || (href==='/'&&path==='/home.html');
      a.classList.toggle('zeus-appnav-active',on);if(on)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
    }
    const settingsLink=document.querySelector('.zeus-appnav-settings');if(settingsLink){const on=sectionHref(route)==='/settings';settingsLink.classList.toggle('zeus-appnav-active',on);if(on)settingsLink.setAttribute('aria-current','page');else settingsLink.removeAttribute('aria-current')}
    const tt=document.querySelector('.zeus-shellbar-title');if(tt)tt.textContent=routeTitle(route);
    const bc=document.querySelector('.zeus-shellbar-trail');if(bc){bc.querySelector('.zeus-shellbar-separator')?.remove();bc.querySelector('a')?.remove();if(path!=='/home.html'){const a=document.createElement('a');a.href='/';a.textContent='Home ZEUS';bc.prepend(a);const d=document.createElement('span');d.className='zeus-shellbar-separator';d.textContent='›';a.after(d)}}
    const homeAction=document.querySelector('.zeus-shellbar-home');if(homeAction)homeAction.hidden=path==='/home.html';
  },close(){setOpen(false)},sidebar};}

  function saveScroll(){try{sessionStorage.setItem(NAV_SCROLL_KEY,String(sidebar.scrollTop));}catch(_e){}}
  sidebar.addEventListener('scroll',saveScroll,{passive:true});
  navigation.addEventListener('click',e=>{if(e.target.closest('a'))saveScroll();});
  window.addEventListener('pagehide',saveScroll);
  window.addEventListener('hashchange',refreshActive);
  window.addEventListener('zeus-navigation-change',refreshActive);
  sidebar.appendChild(navigation);
  const footer=document.createElement('div');footer.className='zeus-appnav-foot';
  const settings=document.createElement('a');settings.href='/settings';settings.className='zeus-appnav-link zeus-appnav-settings';
  settings.innerHTML='<span class="zeus-appnav-symbol" aria-hidden="true">⚙</span><span>Impostazioni</span>';
  footer.appendChild(settings);
  if(resolvedPath==='/settings')settings.classList.add('zeus-appnav-active');
  const version=document.createElement('small');version.className='zeus-appnav-version';version.textContent='ZEUS · 1.7.17-rc4-nav1';footer.appendChild(version);
  sidebar.appendChild(footer);
  const logoutButton=document.getElementById('zeus-logout');if(logoutButton)footer.appendChild(logoutButton);
  const mobileBar=document.createElement('div');mobileBar.className='zeus-appnav-mobilebar';
  const toggle=document.createElement('button');toggle.type='button';toggle.className='zeus-appnav-toggle';
  toggle.setAttribute('aria-label','Apri menu di navigazione');toggle.setAttribute('aria-expanded','false');
  toggle.setAttribute('aria-controls','zeus-appnav');toggle.textContent='☰';
  const homeLink=document.createElement('a');homeLink.href='/';homeLink.className='zeus-appnav-mobilehome';homeLink.textContent='⌂  ZEUS';
  mobileBar.append(toggle,homeLink);
  const backdrop=document.createElement('button');backdrop.type='button';backdrop.className='zeus-appnav-backdrop';backdrop.setAttribute('aria-label','Chiudi il menu');
  document.body.classList.add('zeus-navigation');
  document.body.prepend(backdrop);document.body.prepend(mobileBar);document.body.prepend(sidebar);
  requestAnimationFrame(()=>{try{sidebar.scrollTop=Math.max(0,Number(sessionStorage.getItem(NAV_SCROLL_KEY))||0);}catch(_e){}});
  /* The single header belongs to the shared shell. Old per-page headers
     are hidden by zeus-nav.css and are retained only for backward-compatible markup. */
  const pageTitles = {
    '/':'Home ZEUS','/home.html':'Home ZEUS','/casa':'Panoramica', '/bollette':'Bollette',
    '/nuova-bolletta':'Nuova bolletta', '/percorso':'Verifica e ripartisci',
    '/letture':'Contatori e consumi', '/analisi':'Analisi consumi',
    '/documenti':'Documenti bollette', '/gestione':'Rendiconti e gestione',
    '/immobili':'Configurazione immobili', '/activity':'Registro attività', '/settings':'Impostazioni', '/backup':'Backup e ripristino', '/manutenzione':'Manutenzione dati',
    '/debiti':'ZEUS Lending', '/finance':'ZEUS Finance', '/bilancio':'ZEUS Finance · Bilancio', '/vault':'ZEUS Vault'
  };
  function routeTitle(route){
    const url=new URL(route,location.origin),path=url.pathname,tab=url.searchParams.get('tab');
    if(path==='/casa')return ({'#overview':'Panoramica','#tenants':'Immobili e inquilini','#arrears':'Pagamenti e scadenze','#reports':'Esportazione storica (non numerata)','#settings':'Impostazioni','#bills':'Bollette'})[url.hash]||'Panoramica';
    if(path==='/gestione')return ({panoramica:'Gestione appartamenti',fatture:'Documenti PDF',inquilini:'Storico conduttori',contabilita:'Contabilità',conguagli:'Conguagli',chiusure:'Chiusura contabile',rendiconti:'Rendiconti',ripartizioni:'Criteri di ripartizione',scadenze:'Scadenze',statistiche:'Statistiche'})[tab]||pageTitles[path];
    if(path==='/settings'&&url.hash==='#settings-reading')return 'Impostazioni · ZEUS Reading';
    return pageTitles[path]||'Gestione';
  }
  const shellBar=document.createElement('header');
  shellBar.className='zeus-shellbar';
  shellBar.setAttribute('aria-label','Intestazione ZEUS Casa');
  const trail=document.createElement('div');trail.className='zeus-shellbar-trail';
  const trailHome=document.createElement('a');trailHome.href='/';trailHome.textContent='Home ZEUS';
  const trailDivider=document.createElement('span');trailDivider.className='zeus-shellbar-separator';trailDivider.setAttribute('aria-hidden','true');trailDivider.textContent='›';
  const trailTitle=document.createElement('span');trailTitle.className='zeus-shellbar-title';
  trailTitle.textContent=routeTitle(currentHref());
  if(resolvedPath==='/')trail.append(trailTitle);else trail.append(trailHome,trailDivider,trailTitle);
  const barRight=document.createElement('div');barRight.className='zeus-shellbar-end';
  const homeAction=document.createElement('a');homeAction.href='/';homeAction.className='zeus-shellbar-home';homeAction.textContent='Vai alla Home';
  if(resolvedPath!=='/')barRight.appendChild(homeAction);
  const barStatus=document.createElement('span');barStatus.className='zeus-shellbar-status';barStatus.textContent='Pannello amministrativo';
  barRight.appendChild(barStatus);
  shellBar.append(trail,barRight);
  /* Preserve buttons from legacy headers (notably Refresh). Moving the live
     element retains its click listener and avoids hiding a working action. */
  const legacyHeader=document.querySelector('body > header.head');
  if(legacyHeader){
    const refreshButtons=legacyHeader.querySelectorAll('.head-links button');
    for(const original of refreshButtons){
      original.classList.add('zeus-shellbar-action');
      barRight.prepend(original);
      /* Mobile has no desktop shellbar: use an accessible proxy that fires
         the same action without duplicating element IDs or business logic. */
      const intro=document.querySelector('body > .wrapper .intro');
      if(intro){
        let actionSlot=intro.querySelector('.zeus-mobile-actions');
        if(!actionSlot){actionSlot=document.createElement('div');actionSlot.className='zeus-mobile-actions';intro.appendChild(actionSlot)}
        const proxy=document.createElement('button');proxy.type='button';
        proxy.className='zeus-mobile-action';proxy.textContent=original.textContent;
        proxy.addEventListener('click',()=>original.click());actionSlot.appendChild(proxy);
      }
    }
  }
  /* Always before the original main content, independent of the page template. */
  const contentStart=document.getElementById('root') || document.querySelector('header.head') || document.querySelector('body > header:not(.zeus-shellbar)') || document.querySelector('body > main') || document.querySelector('body > .wrapper');
  if(contentStart)document.body.insertBefore(shellBar,contentStart);
  else document.body.appendChild(shellBar);

  function setOpen(open) {
    document.body.classList.toggle('zeus-appnav-open',open);
    toggle.setAttribute('aria-expanded',String(open));
    toggle.setAttribute('aria-label',open?'Chiudi menu di navigazione':'Apri menu di navigazione');
    toggle.textContent=open?'✕':'☰';
  }
  toggle.addEventListener('click',()=>setOpen(!document.body.classList.contains('zeus-appnav-open')));
  backdrop.addEventListener('click',()=>setOpen(false));
  navigation.addEventListener('click',e=>{if(e.target.closest('a'))setOpen(false)});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')setOpen(false)});

  /* The older Home is a React single-page app. Its internal navigation state
     is reached through the existing hidden buttons; no billing/state logic is changed. */
  const inHome = !!document.getElementById('root');
  const panelKeys = {
    '#overview':'Panoramica','#tenants':'Anagrafica inquilini',
    '#arrears':'Insoluti e scadenze','#reports':'Rendiconti PDF',
    '#settings':'Impostazioni', '#bills':'Bollette'
  };
  let lastPanel='';
  function showHomePanel() {
    if (!inHome)return;
    const target=panelKeys[location.hash];
    if (!target || location.hash===lastPanel)return;
    const buttons=document.querySelectorAll('#root .sidebar nav button');
    const button=Array.from(buttons).find(el=>el.getAttribute('aria-label')===target || el.getAttribute('title')===target);
    if (!button)return;
    lastPanel=location.hash;
    button.click();
    const oldActive=navigation.querySelector('[aria-current="page"]');
    if(oldActive){oldActive.removeAttribute('aria-current');oldActive.classList.remove('zeus-appnav-active')}
    const active=navigation.querySelector('a[href="/casa'+location.hash+'"]');
    if(active){active.classList.add('zeus-appnav-active');active.setAttribute('aria-current','page')}
  }
  window.addEventListener('hashchange',()=>{lastPanel='';showHomePanel()});
  if(inHome){
    const root=document.getElementById('root');
    const observer=new MutationObserver(()=>{
      if (!lastPanel && location.hash)showHomePanel();
      const oldBrand=root.querySelector('.sidebar a.brand');
      if(oldBrand && oldBrand.getAttribute('href')!=='/')oldBrand.setAttribute('href','/');
    });
    observer.observe(root,{childList:true,subtree:true});
    showHomePanel();
  }

  /* The header logo is also a real link, on every separate administration page. */
  if (!inHome) {
    const oldBrand=document.querySelector('header.head .brand');
    if(oldBrand){
      const a=document.createElement('a');a.href='/';a.className='zeus-appnav-headerhome';
      a.title='Torna alla Home di ZEUS Casa';
      while(oldBrand.firstChild)a.appendChild(oldBrand.firstChild);
      oldBrand.appendChild(a);
    }
    const documentsBrand=document.querySelector('body > header:not(.head) > div > strong');
    if (documentsBrand){
      const a=document.createElement('a');a.href='/';a.className='zeus-appnav-headerhome';
      a.title='Torna alla Home di ZEUS Casa';a.textContent=documentsBrand.textContent;
      documentsBrand.replaceWith(a);
    }
  }
})();
