'use strict';
(() => {
 const $=id=>document.getElementById(id);
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const safeDate=s=>s?new Date(s+'T12:00:00').toLocaleDateString('it-IT'):'—';
 const monthNames=['GEN','FEB','MAR','APR','MAG','GIU','LUG','AGO','SET','OTT','NOV','DIC'];
 const frq={none:'Una tantum',monthly:'Mensile',yearly:'Annuale'};
 let model={folders:[],documents:[],files:[],deadlines:[],events:[],trash:[],counts:{}}, selection=null,tab='overview',search='',apartmentNames=['Appartamento 1','Appartamento 2'];
 const find=(type,id)=>model[type].find(x=>x.id===id);
 const folderName=id=>find('folders',id)?.title||'—';
 const docName=id=>find('documents',id)?.title||'—';
 const relevant=(...vals)=>!search || vals.some(x=>String(x??'').toLocaleLowerCase('it').includes(search));
 function notice(msg,isError=false){const e=$('vault-alert');e.hidden=false;e.classList.toggle('error',isError);e.textContent=msg;window.scrollTo?.({top:0,behavior:'smooth'});}
 async function request(url,body=null,raw=false){
  const options={credentials:'same-origin',headers:{'X-Casa-Consumi':'1'}};
  if(body!==null){options.method='POST';options.headers['Content-Type']=raw?'application/octet-stream':'application/json';options.body=raw?body:JSON.stringify(body);}
  const response=await fetch(url,options);
  if(response.status===401){location.href='/login?next=%2Fvault';throw new Error('Accesso richiesto');}
  const data=await response.json().catch(()=>({error:'Risposta del server non valida'}));
  if(!response.ok)throw new Error(data.error||'Operazione non riuscita');
  return data;
 }
 async function reload(){model=await request('/api/vault');draw();}
 function dates(d){
  const [year,month,day]=(d.due_date||'0000-01-01').split('-');return `<div class="vault-datebox"><strong>${esc(day)}</strong><small>${monthNames[(Number(month)||1)-1]||''}</small></div>`;
 }
 function status(d){
  if(d.status==='completed')return '<span class="vault-chip">Completata</span>';
  const now=model.today;
  if(d.due_date<now)return '<span class="vault-chip red">Scaduta · da verificare</span>';
  const days=Math.round((Date.parse(d.due_date+'T12:00:00')-Date.parse(now+'T12:00:00'))/86400000);
  return days<=30?'<span class="vault-chip amber">In scadenza</span>':'<span class="vault-chip blue">Programmata</span>';
 }
 function docRow(d){const n=model.files.filter(f=>f.document_id===d.id).length;
  return `<tr><td><button class="vault-link" data-action="select-document" data-id="${d.id}">▤ ${esc(d.title)}</button><small>${esc(d.description.slice(0,65))}</small></td><td>${esc(d.category||'—')}</td><td>${esc(folderName(d.folder_id))}</td><td>${esc(safeDate(d.document_date))}</td><td>♧ ${n}</td><td><div class="vault-row-actions"><button class="vault-mini-btn" data-action="select-document" data-id="${d.id}">Apri</button><button class="vault-mini-btn" data-action="edit-document" data-id="${d.id}">Modifica</button><button class="vault-mini-btn danger" data-action="delete" data-kind="document" data-id="${d.id}">Cestino</button></div></td></tr>`;
 }
 function dueRow(d){return `<tr><td><b>${esc(d.title)}</b><small>${esc(d.description.slice(0,80))}${d.folder_id?' · '+esc(folderName(d.folder_id)):''}</small></td><td>${safeDate(d.due_date)}</td><td>${esc(frq[d.recurrence]||'—')}</td><td>${d.notice_days} giorni</td><td>${status(d)}</td><td><div class="vault-row-actions"><button class="vault-mini-btn" data-action="edit-deadline" data-id="${d.id}">Modifica</button>${d.status!=='completed'?`<button class="vault-mini-btn" data-action="complete-deadline" data-id="${d.id}">Completata</button>`:`<button class="vault-mini-btn" data-action="reopen-deadline" data-id="${d.id}">Riapri</button>`}<button class="vault-mini-btn danger" data-action="delete" data-kind="deadline" data-id="${d.id}">Cestino</button></div></td></tr>`;}
 function upcomingRow(d){return `<div class="vault-due-item">${dates(d)}<div><strong>${esc(d.title)}</strong><small>${esc(folderName(d.folder_id))} · ${esc(frq[d.recurrence]||'Una tantum')}</small></div><div>${status(d)}<div class="vault-row-actions"><button class="vault-mini-btn" data-action="edit-deadline" data-id="${d.id}">Modifica</button><button class="vault-mini-btn" data-action="complete-deadline" data-id="${d.id}">Fatta</button></div></div></div>`;}
 function folderTile(f){const count=model.documents.filter(d=>d.folder_id===f.id).length;const due=model.deadlines.filter(d=>d.folder_id===f.id&&d.status==='pending').length;
  return `<article class="vault-folder-tile"><h3>▱ ${esc(f.title)}</h3><p>${esc(f.category||'Fascicolo generale')} · ${count} documenti · ${due} scadenze</p><p>${esc(f.description.slice(0,115))}</p><div class="vault-row-actions"><button class="vault-mini-btn" data-action="select-folder" data-id="${f.id}">Apri fascicolo</button><button class="vault-mini-btn" data-action="edit-folder" data-id="${f.id}">Modifica</button><button class="vault-mini-btn danger" data-action="delete" data-id="${f.id}" data-kind="folder">Cestino</button></div></article>`;
 }
 function fileRow(f){const url='/api/vault/files/'+f.id;
  const preview=['application/pdf','image/png','image/jpeg'].includes(f.mime);
  return `<div class="vault-file-row"><span>▤</span><div><strong>${esc(f.filename)}</strong><small>${(f.size/1024).toLocaleString('it-IT',{maximumFractionDigits:0})} KB · ${safeDate(f.created_at.slice(0,10))}</small></div><div class="vault-row-actions">${preview?`<a class="vault-mini-btn" href="${url}?preview=1" target="_blank" rel="noopener">Apri</a>`:''}<a class="vault-mini-btn" href="${url}">↓</a><button class="vault-mini-btn danger" data-action="delete-file" data-id="${f.id}">×</button></div></div>`;}
 function renderSelection(){
  const el=$('vault-selection');
  if(!selection){el.innerHTML='<div class="vault-empty">Seleziona un documento o un fascicolo oppure crea il primo elemento dell’archivio. Nessuna scadenza obbligatoria.</div>';return;}
  const {kind,id}=selection;
  const item=find(kind==='folder'?'folders':'documents',id);
  if(!item){selection=null;renderSelection();return;}
  const docs=kind==='folder'?model.documents.filter(d=>d.folder_id===id):[item];
  const files=model.files.filter(f=>docs.some(d=>d.id===f.document_id));
  const deadlines=model.deadlines.filter(d=>kind==='folder'?d.folder_id===id:(d.document_id===id));
  const events=model.events.filter(x=>x.object_id===id).slice(0,12);
  const meta=kind==='folder'?`<dt>Categoria</dt><dd>${esc(item.category||'—')}</dd><dt>Collegamento</dt><dd>${item.link_kind==='apartment'?esc(apartmentNames[Number(item.link_id)]||'Appartamento'): 'Nessuno'}</dd>`:
       `<dt>Categoria</dt><dd>${esc(item.category||'—')}</dd><dt>Data</dt><dd>${safeDate(item.document_date)}</dd><dt>Fascicolo</dt><dd>${esc(folderName(item.folder_id))}</dd>`;
  el.innerHTML=`<div class="vault-detail"><div class="vault-detail-title"><h3>▱ ${esc(item.title)}</h3><button class="vault-mini-btn" data-action="edit-${kind}" data-id="${id}">Modifica</button></div><p>${esc(item.description||'Nessuna descrizione')}</p><dl>${meta}<dt>Creato il</dt><dd>${safeDate(item.created_at.slice(0,10))}</dd></dl><h3>Attività recente</h3><div class="vault-events">${events.length?events.map(x=>`<p>${safeDate(x.created_at.slice(0,10))} · ${esc(x.action)} ${esc(x.details||'')}</p>`).join(''):'Nessuna attività registrata'}</div></div>
  <div class="vault-detail"><div class="vault-detail-title"><h3>▤ Documenti e allegati (${files.length})</h3><button class="vault-mini-btn" data-action="new-document" data-folder="${kind==='folder'?id:(item.folder_id||'')}">＋ Documento</button></div>${kind==='folder'&&docs.length?`<p>Documenti: ${docs.map(d=>`<button class="vault-mini-btn" data-action="select-document" data-id="${d.id}">${esc(d.title)}</button>`).join(' ')}</p>`:''}${files.length?files.map(fileRow).join(''):'<div class="vault-empty">Ancora nessun allegato</div>'}${kind==='document'?`<div class="vault-attachment-tools"><input type="file" id="vault-detail-file" multiple accept=".pdf,.jpg,.jpeg,.png,.txt,.doc,.docx,.xls,.xlsx" aria-label="Scegli allegati"><button class="vault-mini-btn" data-action="upload-files" data-id="${id}">Carica file</button></div>`:'<p>Apri un documento per aggiungere altri file.</p>'}</div>
  <div class="vault-detail"><div class="vault-detail-title"><h3>▦ Scadenze collegate (${deadlines.length})</h3><button class="vault-mini-btn" data-action="new-deadline" data-folder="${kind==='folder'?id:(item.folder_id||'')}" data-document="${kind==='document'?id:''}">＋ Scadenza</button></div>${deadlines.length?deadlines.map(d=>`<div class="vault-due-item">${dates(d)}<div><strong>${esc(d.title)}</strong><small>${safeDate(d.due_date)}</small></div>${status(d)}<button class="vault-mini-btn" data-action="edit-deadline" data-id="${d.id}">Modifica</button></div>`).join(''):'<div class="vault-empty">Nessuna scadenza associata</div>'}</div>`;
 }
 function draw(){
  $('vault-count-documents').textContent=model.counts.documents||0;
  $('vault-count-folders').textContent=model.counts.folders||0;
  $('vault-count-upcoming').textContent=model.counts.upcoming||0;
  $('vault-count-files').textContent=model.counts.attachments||0;
  for(const key of ['overview','documents','folders','deadlines','trash']){$('vault-'+key).hidden=key!==tab;}
  document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('selected',b.dataset.tab===tab);b.setAttribute('aria-pressed',String(b.dataset.tab===tab));});
  const docs=model.documents.filter(d=>relevant(d.title,d.category,d.description,folderName(d.folder_id)));
  $('vault-recent-docs').innerHTML=docs.length?docs.slice(0,7).map(docRow).join(''):'<tr><td colspan="6"><div class="vault-empty">Nessun documento. Inizia caricando un file, anche senza scadenza.</div></td></tr>';
  $('vault-all-docs').innerHTML=docs.length?docs.map(docRow).join(''):'<tr><td colspan="6"><div class="vault-empty">Nessun documento corrispondente.</div></td></tr>';
  const due=model.deadlines.filter(d=>d.status==='pending'&&relevant(d.title,d.description,folderName(d.folder_id),docName(d.document_id)));
  $('vault-next-deadlines').innerHTML=due.length?due.slice(0,6).map(upcomingRow).join(''):'<div class="vault-empty">Nessuna scadenza in sospeso.</div>';
  const allDue=model.deadlines.filter(d=>relevant(d.title,d.description,folderName(d.folder_id),docName(d.document_id)));
  $('vault-all-deadlines').innerHTML=allDue.length?allDue.map(dueRow).join(''):'<tr><td colspan="6"><div class="vault-empty">Nessuna scadenza registrata.</div></td></tr>';
  const folders=model.folders.filter(f=>relevant(f.title,f.description,f.category));
  $('vault-folder-grid').innerHTML=folders.length?folders.map(folderTile).join(''):'<div class="vault-empty">Nessun fascicolo: puoi archiviarne uno oppure salvare documenti senza fascicolo.</div>';
  $('vault-purge-trash').disabled=!model.trash.length;
  $('vault-trash-list').innerHTML=model.trash.length?model.trash.map(t=>`<div class="vault-file-row"><span>▤</span><div><strong>${esc(t.title)}</strong><small>${esc(t.kind)} · ${safeDate(t.deleted_at.slice(0,10))}</small></div><button class="vault-mini-btn" data-action="restore" data-id="${t.id}" data-kind="${t.kind}">Ripristina</button></div>`).join(''):'<div class="vault-empty">Il cestino è vuoto.</div>';
  renderSelection();
 }
 function options(){
  document.querySelectorAll('.vault-folders-select').forEach(e=>{const old=e.value;e.innerHTML='<option value="">Nessun fascicolo</option>'+model.folders.map(f=>`<option value="${f.id}">${esc(f.title)}</option>`).join('');e.value=old||'';});
  const d=$('vault-deadline-doc');const old=d.value;d.innerHTML='<option value="">Nessun documento</option>'+model.documents.map(x=>`<option value="${x.id}">${esc(x.title)}</option>`).join('');d.value=old||'';
 }
 function open(kind,item=null,extra={}){
  const dialog=$('vault-'+kind+'-dialog'),form=$('vault-'+kind+'-form');form.reset();options();
  form.elements.id.value=item?.id||'';
  for(const input of form.elements){if(input.name&&input.name!=='files'&&input.name!=='id'&&item&&Object.prototype.hasOwnProperty.call(item,input.name)){input.value=item[input.name]??'';}}
  if(!item&&extra.folder_id&&form.elements.folder_id)form.elements.folder_id.value=extra.folder_id;
  if(!item&&extra.document_id&&form.elements.document_id)form.elements.document_id.value=extra.document_id;
  if(kind==='folder'){if(!item)form.elements.link_kind.value='';form.elements.link_id.value=item?.link_id||'';}
  $('vault-'+kind+'-dialog-title').textContent=(item?'Modifica ':'Nuovo ')+(kind==='folder'?'fascicolo':kind==='document'?'documento':'promemoria / scadenza');
  dialog.showModal();form.querySelector('input[name=title]').focus();
 }
 function formData(form){const data={};for(const [k,v] of new FormData(form).entries()){if(k!=='files')data[k]=v;}if('notice_days' in data)data.notice_days=Number(data.notice_days);return data;}
 async function uploadFiles(docId,files){let count=0;for(const file of files){if(file.size>15*1024*1024)throw new Error(file.name+': massimo 15 MB per allegato');
   await request('/api/vault/files/upload?document_id='+encodeURIComponent(docId)+'&filename='+encodeURIComponent(file.name),await file.arrayBuffer(),true);count++;}return count;}
 async function save(kind){const dialog=$('vault-'+kind+'-dialog'),form=$('vault-'+kind+'-form');if(!form.reportValidity())return;
  const submit=form.querySelector('[type=submit]');submit.disabled=true;
  try{
   const data=formData(form);
   if(kind==='folder'&&data.link_kind!=='apartment')data.link_id='';
   const result=await request('/api/vault/'+kind+'/save',data);
   let count=0;let uploadError=null;
   if(kind==='document'){
     const files=Array.from(form.elements.files.files);
     try{count=await uploadFiles(result.id,files);}catch(err){uploadError=err;}
   }
   selection={kind,id:result.id};dialog.close();await reload();
   notice(uploadError?`Documento salvato, ma il caricamento degli allegati non è completo (${count} caricati): ${uploadError.message}`:`${kind==='document'?'Documento salvato':kind==='folder'?'Fascicolo salvato':'Scadenza salvata'}${count?' · '+count+' file caricati':''}.`,!!uploadError);
  }catch(err){notice(err.message,true);}finally{submit.disabled=false;}
 }
 async function act(button){const type=button.dataset.action,id=button.dataset.id;
  if(type?.startsWith('new-'))return open(type.slice(4),null,{folder_id:button.dataset.folder||'',document_id:button.dataset.document||''});
  if(type?.startsWith('edit-')){const kind=type.slice(5);return open(kind,find({folder:'folders',document:'documents',deadline:'deadlines'}[kind],id));}
  if(type==='select-document'||type==='select-folder') {selection={kind:type.slice(7),id};renderSelection();$('vault-selected-card').scrollIntoView({behavior:'smooth',block:'start'});return;}
  if(type==='upload-files'){
   const files=Array.from($('vault-detail-file')?.files||[]);
   if(!files.length){notice('Scegli almeno un file da caricare.',true);return;}
   button.disabled=true;
   try{const n=await uploadFiles(id,files);await reload();notice(n+' allegati caricati.');}catch(err){await reload();notice('Caricamento non completato: '+err.message,true);}finally{button.disabled=false;}return;
  }
  if(type==='purge-trash'){
   if(!model.trash.length)return;
   if(!confirm('ATTENZIONE: eliminare definitivamente gli elementi nel cestino e i loro allegati? Questa operazione non può essere annullata.'))return;
   await request('/api/vault/trash/purge',{confirm:'ELIMINA DEFINITIVAMENTE'});selection=null;await reload();notice('Cestino svuotato.');return;
  }
  if(type==='delete-file'){
   if(!confirm('Rimuovere questo allegato dall’elenco? Verrà conservato nel backup e nello storico.'))return;
   await request('/api/vault/file/delete',{id});await reload();notice('Allegato rimosso dalla visualizzazione.');return;
  }
  if(type==='complete-deadline'){
   const d=find('deadlines',id);const suffix=d?.recurrence!=='none'?' Verrà predisposta la prossima ricorrenza.':'';
   if(!confirm('Confermare l’adempimento?'+suffix))return;
   await request('/api/vault/deadline/complete',{id});await reload();notice('Scadenza aggiornata.');return;
  }
  if(type==='reopen-deadline'){
   if(!confirm('Riaprire questa scadenza?'))return;
   await request('/api/vault/deadline/reopen',{id});await reload();notice('Scadenza riaperta.');return;
  }
  if(type==='delete'){
   if(!confirm('Spostare nel cestino questo elemento? I documenti non verranno cancellati fisicamente.'))return;
   await request('/api/vault/item/delete',{id,kind:button.dataset.kind});selection=null;await reload();notice('Elemento spostato nel cestino.');return;
  }
  if(type==='restore'){
   await request('/api/vault/item/restore',{id,kind:button.dataset.kind});await reload();notice('Elemento ripristinato.');return;
  }
 }
 async function init(){
  try{const state=await request('/api/state');if(Array.isArray(state.archive?.names)&&state.archive.names.length===2)apartmentNames=state.archive.names;}catch(e){}
  $('vault-apartment').innerHTML='<option value="">Nessuno</option>'+apartmentNames.map((n,i)=>`<option value="${i}">${esc(n)}</option>`).join('');
  $('vault-refresh').addEventListener('click',()=>reload().catch(e=>notice(e.message,true)));
  $('vault-search').addEventListener('input',e=>{search=e.target.value.trim().toLocaleLowerCase('it');draw();});
  document.querySelectorAll('.vault-dialog form').forEach(form=>form.addEventListener('submit',e=>{e.preventDefault();save(form.getAttribute('id').replace('vault-','').replace('-form',''));}));
  document.addEventListener('click',e=>{
    const tabButton=e.target.closest('[data-tab]');if(tabButton){tab=tabButton.dataset.tab;history.replaceState(null,'',tab==='overview'?'/vault':'/vault#'+tab);window.dispatchEvent(new Event('zeus-navigation-change'));draw();return;}
    const close=e.target.closest('[data-close]');if(close){$(close.dataset.close).close();return;}
    const action=e.target.closest('[data-action]');if(action){act(action).catch(err=>notice(err.message,true));}
  });
  window.addEventListener('hashchange',()=>{const next=location.hash.slice(1)||'overview';if(['overview','documents','folders','deadlines','trash'].includes(next)){tab=next;draw();}});
  await reload();
  if(['documents','folders','deadlines','trash'].includes(location.hash.slice(1))){tab=location.hash.slice(1);draw();}
 }
 init().catch(e=>notice('ZEUS Vault non disponibile: '+e.message,true));
})();
