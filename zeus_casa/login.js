"use strict";
// The private photo is optional; retain the original gradient when it is absent.
const housePhoto=document.getElementById("zeus-login-photo");
if(housePhoto){housePhoto.addEventListener("error",()=>{housePhoto.hidden=true;});}

const form=document.querySelector('#login');
const userField=form.elements.namedItem('user');
const remember=document.getElementById('remember-user');
try{const saved=localStorage.getItem('zeus-login-username');if(saved){userField.value=saved;remember.checked=true;}}catch(_error){}
const toggle=document.getElementById('toggle-password');
toggle.addEventListener('click',()=>{
  const field=form.elements.namedItem('password');const reveal=field.type==='password';
  field.type=reveal?'text':'password';toggle.setAttribute('aria-pressed',String(reveal));toggle.setAttribute('aria-label',reveal?'Nascondi password':'Mostra password');toggle.dataset.visible=String(reveal);
});
form.addEventListener('submit',async ev=>{
  ev.preventDefault();const btn=form.querySelector('button[type=submit]');btn.disabled=true;
  const message=document.querySelector('#message');message.textContent='';
  const fd=new FormData(form);
  try{
    const r=await fetch('/api/session/login',{method:'POST',cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Casa-Consumi':'1'},body:JSON.stringify({username:fd.get('user'),password:fd.get('password')})});
    const o=await r.json();if(!r.ok)throw Error(o.error||'Accesso non riuscito');
    try{if(remember.checked)localStorage.setItem('zeus-login-username',String(fd.get('user')));else localStorage.removeItem('zeus-login-username')}catch(_error){}
    const qs=new URLSearchParams(location.search);const next=qs.get('next')||'/';
    location.replace(next.startsWith('/')&&!next.startsWith('//')&&!next.includes('\\')?next:'/');
  }catch(ex){message.textContent=ex.message;btn.disabled=false;}
});
