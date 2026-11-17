'use strict';
(function(){
  if(window.parent!==window)return; // Single logout belongs to persistent application shell.
  async function logout(){
    const button=document.getElementById('zeus-logout');if(button)button.disabled=true;
    try{const r=await fetch('/api/session/logout',{method:'POST',headers:{'Content-Type':'application/json','X-Casa-Consumi':'1'},body:'{}'});if(!r.ok)throw Error('Logout non riuscito');location.replace('/login');}
    catch(e){alert(e.message);if(button)button.disabled=false;}
  }
  function add(){
    const existing=document.getElementById('zeus-logout');
    if(existing){const target=document.querySelector('.zeus-appnav-foot')||document.querySelector('.head-links')||document.querySelector('.topbar')||document.querySelector('.sidebar-foot');if(target&&existing.parentNode!==target)target.appendChild(existing);return;}
    const b=document.createElement('button');b.type='button';b.id='zeus-logout';b.textContent='Esci';b.title='Termina la sessione ZEUS Casa';
    b.className='zeus-logout-action';b.addEventListener('click',logout);
    const where=document.querySelector('.zeus-appnav-foot')||document.querySelector('.head-links')||document.querySelector('.topbar')||document.querySelector('.sidebar-foot')||document.body;
    where.appendChild(b);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',add);else add();
  const root=document.getElementById('root');if(root){const ob=new MutationObserver(add);ob.observe(root,{subtree:true,childList:true});}
})();
