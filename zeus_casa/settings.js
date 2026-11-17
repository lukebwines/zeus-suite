'use strict';
(()=>{
 const status=document.getElementById('settings-status');
 const qrForm=document.getElementById('zeus-reading-pin-form');
 if(qrForm){
  qrForm.addEventListener('submit',async event=>{
   event.preventDefault();const pin=document.getElementById('zeus-reading-pin').value;
   const result=document.getElementById('zeus-reading-pin-status');
   if(!/^\d{4}$/.test(pin)){result.textContent='Il PIN deve contenere quattro cifre.';return;}
   try{const response=await fetch('/api/qr-admin/pin',{method:'POST',headers:{'Content-Type':'application/json','X-Casa-Consumi':'1'},credentials:'same-origin',body:JSON.stringify({pin})});
    const data=await response.json();if(!response.ok)throw Error(data.error||'Errore salvataggio PIN');
    qrForm.reset();result.textContent='PIN comune aggiornato. I QR già attivi restano validi.';
   }catch(err){result.textContent=err.message;}
  });
 }

 const loginPhotoInput=document.getElementById('zeus-login-photo-file');
 const loginPhotoButton=document.getElementById('zeus-login-photo-upload');
 const loginPhotoStatus=document.getElementById('zeus-login-photo-status');
 if(loginPhotoButton && loginPhotoInput && loginPhotoStatus){
  loginPhotoButton.addEventListener('click',async()=>{
   const file=loginPhotoInput.files?.[0];
   if(!file){loginPhotoStatus.textContent='Seleziona prima una fotografia JPEG.';return;}
   if(file.size<128 || file.size>8*1024*1024){
    loginPhotoStatus.textContent='La fotografia deve essere JPEG, fino a 8 MB.';return;
   }
   loginPhotoButton.disabled=true;loginPhotoStatus.textContent='Caricamento e verifica della fotografia…';
   try{
    const response=await fetch('/api/login/photo',{
     method:'POST',credentials:'same-origin',
     headers:{'Content-Type':'image/jpeg','X-Casa-Consumi':'1'},
     body:file
    });
    const out=await response.json();
    if(!response.ok)throw new Error(out.error||'Impossibile salvare la fotografia');
    loginPhotoInput.value='';
    loginPhotoStatus.textContent='Fotografia della casa aggiornata. Puoi verificarla dalla pagina di login.';
   }catch(e){loginPhotoStatus.textContent=e.message;}
   finally{loginPhotoButton.disabled=false;}
  });
 }

 if(location.pathname==='/backup')requestAnimationFrame(()=>document.getElementById('zeus-backup')?.scrollIntoView({block:'start'}));
 function announce(text,error=false){status.textContent=text;status.classList.toggle('error',error);}
 async function state(){const r=await fetch('/api/state',{credentials:'same-origin'});if(r.status===401){location.assign('/login?next=%2Fsettings');throw new Error('Accesso richiesto')}
  const obj=await r.json();if(!r.ok)throw new Error(obj.error||'Errore lettura archivio');return obj;}
 document.getElementById('settings-export-json').addEventListener('click',async()=>{
  try{const obj=await state();const blob=new Blob([JSON.stringify({format:'casa-consumi-v2',archive:obj.archive},null,2)],{type:'application/json'});
   const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='zeus-archivio.json';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);announce('Archivio JSON esportato.');}
  catch(e){announce(e.message,true)}
 });
 document.getElementById('settings-restore-json').addEventListener('change',async(ev)=>{
  const file=ev.target.files?.[0];if(!file)return;
  try{
   if(file.size>4*1024*1024)throw new Error('File JSON troppo grande');
   const imported=JSON.parse(await file.text());
   if(!['casa-consumi-v1','casa-consumi-v2'].includes(imported.format)||!imported.archive||typeof imported.archive!=='object')throw new Error('Non è un archivio JSON ZEUS valido');
   if(!confirm('ATTENZIONE: ripristinare il JSON sostituirà l’anagrafica e le bollette attuali. Non ripristina prestiti, Finance, Vault né gli allegati. Hai eseguito prima un backup completo?'))return;
   const original=await state();const r=await fetch('/api/state',{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Casa-Consumi':'1'},body:JSON.stringify({revision:original.revision,archive:imported.archive})});
   const out=await r.json();if(!r.ok)throw new Error(out.error||'Ripristino non riuscito');announce('Archivio JSON ripristinato. Ricarica le altre pagine.');
  }catch(e){announce(e.message,true)}finally{ev.target.value='';}
 });

 const restoreFile=document.getElementById('settings-restore-zip');
 const restoreStatus=document.getElementById('settings-restore-status');
 const restoreSummary=document.getElementById('settings-restore-summary');
 const confirmArea=document.getElementById('settings-confirm-area');
 const confirmation=document.getElementById('settings-confirm-text');
 function restoreMessage(message,error=false){restoreStatus.textContent=message;restoreStatus.classList.toggle('error',error);}
 async function readStatus(){
  try{const r=await fetch('/api/backup/restore/status',{credentials:'same-origin'});if(!r.ok)return;
   const value=await r.json();if(value.armed){restoreMessage('Ripristino programmato. Riavvia manualmente l’add-on ZEUS da Home Assistant.');}
   else if(value.last_result){restoreMessage(value.last_result.message,!value.last_result.ok);}
  }catch(_){}
 }
 readStatus();
 document.getElementById('settings-check-zip').addEventListener('click',async()=>{
  const file=restoreFile.files?.[0];if(!file){restoreMessage('Seleziona prima un backup ZIP completo.',true);return;}
  if(file.size>512*1024*1024){restoreMessage('File troppo grande (massimo 512 MB).',true);return;}
  confirmArea.hidden=true;restoreSummary.hidden=true;restoreMessage('Caricamento e verifica in corso. Non chiudere questa pagina.');
  try{
   const r=await fetch('/api/backup/restore/upload',{method:'POST',credentials:'same-origin',headers:{'X-Casa-Consumi':'1','Content-Type':'application/zip'},body:file});
   const answer=await r.json();if(!r.ok)throw new Error(answer.error||'Backup non valido');
   const q=answer.summary;
   restoreSummary.textContent=`Backup verificato — data (UTC): ${q.created_at_utc || 'non presente'}, allegati: ${q.attachments}, file: ${q.files}, dimensione: ${(q.size_bytes/1048576).toFixed(2)} MB.`;
   restoreSummary.hidden=false;confirmArea.hidden=false;restoreMessage('Verifica completata. Il database attuale non è stato modificato.');
  }catch(e){restoreMessage(e.message,true)}
 });
 document.getElementById('settings-confirm-zip').addEventListener('click',async()=>{
  if(confirmation.value.trim()!=='RIPRISTINA ZEUS'){restoreMessage('Scrivi esattamente RIPRISTINA ZEUS.',true);return;}
  if(!confirm('ATTENZIONE: al prossimo riavvio tutti i dati di ZEUS saranno sostituiti dal backup selezionato. Proseguire?'))return;
  try{const r=await fetch('/api/backup/restore/confirm',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Casa-Consumi':'1'},body:JSON.stringify({confirm:'RIPRISTINA ZEUS'})});
   const a=await r.json();if(!r.ok)throw new Error(a.error||'Programmazione non riuscita');
   restoreMessage(a.message);confirmArea.hidden=true;restoreFile.value='';confirmation.value='';
  }catch(e){restoreMessage(e.message,true)}
 });
})();
