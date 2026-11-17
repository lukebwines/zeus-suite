'use strict';
window.zeusQrAdmin=(()=>{
let data=null;
const h=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names={electricity:'Elettricità',gas:'Gas',water:'Acqua',main:'Principale',sub:'Sottocontatore · appartamento 1',sub2:'Sottocontatore · appartamento 2'};
const api=async(url,body)=>{const response=await fetch(url,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-Casa-Consumi':'1'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});const result=await response.json();if(!response.ok)throw Error(result.error||'Operazione non riuscita');return result};
async function load(){data=await api('/api/qr-admin');renderInPage();}
function render(){
 if(!data){setTimeout(()=>load().catch(e=>notify(e.message,true)),0);return '<section class="reading-card"><h2>ZEUS Reading · Configurazione QR</h2><p>Caricamento…</p></section>'}
 const cards=data.devices.map(d=>`<div class="qr-device"><h3>${h(names[d.utility])} — ${h(names[d.meter])}</h3>${d.configured?`<p class="hint">${d.active?'QR attivo':'QR sospeso'}</p><a href="/api/qr-admin/qr/${d.utility}/${d.meter}" target="_blank" rel="noopener noreferrer" download="ZEUS_${d.utility}_${d.meter}.svg">Scarica QR con nome contatore (SVG)</a><p class="hint">L’URL rimane privato: non condividerlo online.</p><button class="btn light" data-action="rotate" data-utility="${d.utility}" data-meter="${d.meter}">Rigenera QR</button> <button class="btn light" data-action="toggle" data-active="${d.active}" data-utility="${d.utility}" data-meter="${d.meter}">${d.active?'Disattiva':'Riattiva'}</button>`:`<p class="hint">QR non ancora generato</p><button class="btn" data-action="generate" data-utility="${d.utility}" data-meter="${d.meter}" ${!data.has_pin?'disabled':''}>Genera QR</button>`}</div>`).join('');
 const entries=data.submissions.map(r=>{
   const id=h(r.id);
   const status=h({pending:'Da approvare',approved:'Approvata',rejected:'Rifiutata'}[r.status]||r.status);
   const actions=r.status==='pending'
      ? `<button class="btn" data-action="approve" data-id="${id}">Approva</button> <button class="btn light" data-action="reject" data-id="${id}">Rifiuta</button>`
      : r.status==='rejected'
      ? `<button class="btn light" data-action="restore" data-id="${id}">Ripristina</button> <button class="btn danger" data-action="delete" data-id="${id}">Elimina definitivamente</button>`
      : '<span class="hint">Lettura approvata · non eliminabile da qui</span>';
   return `<tr><td>${h(names[r.utility])}<br><small>${h(names[r.meter])}</small></td><td>${h(r.reading_date)}<br><small>${h(r.received_at)}</small></td><td>${h(r.value)}${r.utility==='electricity'&&r.meter==='main'&&r.register_f1!==''?`<br><small>A1/F1: ${h(r.register_f1)} · A2/F2: ${h(r.register_f2)} · A3/F3: ${h(r.register_f3)}</small>`:''}</td><td>${r.has_photo?`<a href="/api/qr-admin/photo/${id}" target="_blank" rel="noopener">Foto timbrata</a>`:'Nessuna foto prevista'}</td><td>${status}${r.review_note?`<br><small>Motivo: ${h(r.review_note)}</small>`:''}</td><td>${actions}</td></tr>`;
 }).join('');
 const reasonDialog=`<dialog id="qr-reject-dialog" class="qr-reject-dialog" aria-labelledby="qr-reject-title"><form method="dialog" id="qr-reject-form"><h3 id="qr-reject-title">Rifiuta la lettura</h3><p class="hint">La lettura e l’eventuale foto rimangono archiviate. Potrai ripristinarle oppure eliminarle definitivamente.</p><label>Motivo del rifiuto<select name="reason" required><option value="Fotografia illeggibile">Fotografia illeggibile</option><option value="Lettura digitata errata">Lettura digitata errata</option><option value="Contatore non corrispondente">Contatore non corrispondente</option><option value="Lettura duplicata">Lettura duplicata</option><option value="Altro motivo">Altro motivo</option></select></label><label>Dettaglio (facoltativo)<input name="detail" maxlength="200" placeholder="Aggiungi una nota..."></label><div class="actions"><button type="button" class="btn light" data-cancel-reject>Annulla</button><button type="submit" class="btn danger">Conferma rifiuto</button></div></form></dialog>`;
 return `<section class="reading-card"><h2>QR e letture rapide</h2><p class="hint">Il PIN comune si gestisce da <a href="/settings#settings-reading">Impostazioni → ZEUS Reading</a>. I codici esistenti restano validi.</p></section><section class="reading-card"><h2>QR dei sette contatori</h2><p class="hint">I QR sono distinti e casuali. Stampali ed applicali solo ai contatori corrispondenti. Rigenerare un QR revoca quello vecchio.</p><div class="qr-devices">${cards}</div></section><section class="reading-card"><h2>Letture da approvare e archivio QR</h2><p class="hint">Per il contatore elettrico generale si registrano A1, A2, A3 senza foto; per gli altri contatori la fotografia è obbligatoria e viene timbrata. Rifiuta conserva i dati e le eventuali foto; Elimina definitivamente rimuove la lettura rifiutata dall'archivio attivo. Le letture approvate rimangono protette.</p><div class="reading-table-wrap"><table class="reading-table"><thead><tr><th>Contatore</th><th>Data</th><th>Lettura</th><th>Foto</th><th>Stato e motivo</th><th>Azioni</th></tr></thead><tbody>${entries||'<tr><td colspan="6">Nessuna lettura inviata.</td></tr>'}</tbody></table></div></section>${reasonDialog}`;
}
function notify(t,error){const el=document.querySelector('#message');if(el){el.hidden=false;el.textContent=t;el.className='message'+(error?' error':'');}else alert(t)}
function renderInPage(){if(document.querySelector('[data-tab="qr"]')?.classList.contains('active')){const panel=document.querySelector('#reading-panel');panel.innerHTML=render();bind()}}
function bind(){const panel=document.querySelector('#reading-panel');const form=panel.querySelector('#qr-pin-form');if(form)form.onsubmit=async e=>{e.preventDefault();e.stopPropagation();const pin=String(form.elements.namedItem('pin').value);if(!/^\d{4}$/.test(pin)){notify('Inserisci quattro cifre',true);return;}try{await api('/api/qr-admin/pin',{pin});form.reset();await load();notify('PIN comune salvato: ora è valido per tutti i contatori')}catch(err){notify(err.message,true)}};
 const rejectDialog=panel.querySelector('#qr-reject-dialog');
 const rejectForm=panel.querySelector('#qr-reject-form');
 panel.querySelector('[data-cancel-reject]').onclick=()=>rejectDialog.close();
 rejectForm.onsubmit=async event=>{
   event.preventDefault();event.stopPropagation();
   const id=rejectForm.dataset.recordId;
   const reason=rejectForm.elements.namedItem('reason').value;
   const detail=rejectForm.elements.namedItem('detail').value.trim();
   if(!reason)return notify('Seleziona il motivo del rifiuto',true);
   const note=reason+(detail?' — '+detail:'');
   const button=rejectForm.querySelector('button[type=submit]');button.disabled=true;
   try{await api('/api/qr-admin/review',{id,action:'reject',note});rejectDialog.close();await load();notify('Lettura rifiutata e dati conservati');}
   catch(err){notify(err.message,true)}finally{button.disabled=false}
 };
 panel.querySelectorAll('[data-action]').forEach(button=>button.onclick=async()=>{
   const action=button.dataset.action;
   if(action==='reject'){
       rejectForm.reset();rejectForm.dataset.recordId=button.dataset.id;rejectDialog.showModal();return;
   }
   let payload={utility:button.dataset.utility,meter:button.dataset.meter};
   try{
      if(action==='rotate'&&!confirm('Rigenerare il QR? Il precedente non funzionerà più.'))return;
      if(action==='toggle')payload.active=button.dataset.active!=='true';
      if(action==='approve'||action==='restore'||action==='delete'){
          const messages={approve:'Approvare la lettura? Entrerà nello storico dei consumi.',restore:'Ripristinare la lettura rifiutata, riportandola tra quelle da approvare?',delete:'ELIMINARE DEFINITIVAMENTE la lettura e l’eventuale fotografia? Non potrai recuperarle dall’archivio attivo. Potrebbero restare nei backup già creati.'};
          if(!confirm(messages[action]))return;
          payload={id:button.dataset.id,...(action==='approve'?{action:'approve'}:{})};
      }
      await api('/api/qr-admin/'+(['approve'].includes(action)?'review':action),payload);
      await load();
      notify(action==='delete'?'Lettura rifiutata eliminata dall’archivio attivo':'Operazione completata');
   }catch(err){notify(err.message,true)}
 });
}
return {render,bind,load};
})();
