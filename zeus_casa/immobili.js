'use strict';
(()=>{
 let archive=null,revision=null;
 const output=document.getElementById('apartment-status');
 function status(message,error=false){output.textContent=message;output.classList.toggle('error',error)}
 async function load(){
  const r=await fetch('/api/state',{credentials:'same-origin'});
  if(r.status===401){location.assign('/login?next=%2Fimmobili');return;}
  const data=await r.json();if(!r.ok)throw new Error(data.error||'Impossibile caricare');
  archive=data.archive;revision=data.revision;
  document.getElementById('name0').value=archive.names[0];
  document.getElementById('name1').value=archive.names[1];
 }
 document.getElementById('apartment-form').addEventListener('submit',async(e)=>{
  e.preventDefault();const button=document.getElementById('save-names');
  if(!archive)return;
  const names=[document.getElementById('name0').value.trim(),document.getElementById('name1').value.trim()];
  if(names.some(n=>!n||n.length>60)){status('Inserisci due nomi validi.',true);return;}
  button.disabled=true;status('Salvataggio in corso…');
  try{
   const response=await fetch('/api/state',{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Casa-Consumi':'1'},body:JSON.stringify({revision,archive:{...archive,names}})});
   if(!response.ok){const err=await response.json();throw new Error(err.error||'Salvataggio non riuscito');}
   await load();status('Nomi aggiornati. Gli altri dati sono stati conservati.');
  }catch(error){status(error.message+(error.message.includes('altro dispositivo')?' Aggiorna la pagina e riprova.':''),true)}
  finally{button.disabled=false;}
 });
 load().catch(e=>status(e.message,true));
})();
