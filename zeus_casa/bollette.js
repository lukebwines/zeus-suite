'use strict';
(()=>{
const $=s=>document.querySelector(s);
const safe=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names={electricity:'Elettricità',gas:'Gas',water:'Acqua'};
const money=c=>(Number(c)/100).toLocaleString('it-IT',{style:'currency',currency:'EUR'});
const moneyEur=c=>Number(c).toLocaleString('it-IT',{style:'currency',currency:'EUR'});
const showDate=s=>/^\d{4}-\d\d-\d\d$/.test(s||'')?s.slice(8,10)+'/'+s.slice(5,7)+'/'+s.slice(0,4):'—';
const focus=new URLSearchParams(location.search).get('focus')||'';
let busy=false,focused=false;
function alertMsg(text,error=false){const e=$('#message');e.hidden=false;e.textContent=text;e.className='message'+(error?' error':'');e.scrollIntoView({behavior:'smooth',block:'nearest'})}
async function req(path,body){const cfg={cache:'no-store'};if(body){cfg.method='POST';cfg.headers={'X-Casa-Consumi':'1','Content-Type':'application/json'};cfg.body=JSON.stringify(body)}const r=await fetch(path,cfg);if(r.status===401){location.replace('/login?next=/bollette');throw Error('Sessione scaduta')};const data=await r.json();if(!r.ok)throw Error(data.error||'Operazione non riuscita');return data}
function tag(text,warn=false){return `<span class="${warn?'warn':''}">${safe(text)}</span>`}
function pendingCard(row){const p=row.document,id=p.id;const url=encodeURIComponent(id);
 const readingsOK=row.snapshot&&row.snapshot.available;
 const issues=row.warnings||[];
 return `<article class="hub-item ${focus===id?'focus':''}" data-pending="${safe(id)}"><div class="subheading"><div><h3>${safe(names[p.utility]||p.utility)} · ${safe(p.invoice_number||'Senza numero')}</h3><div class="details">${showDate(p.period_start)} – ${showDate(p.period_end)} · ${money(p.total_cents)} · Emessa ${showDate(p.invoice_date)}</div></div><span class="pill ${row.can_post?'':'yellow'}">${row.can_post?'Verifica disponibile':'In attesa letture/verifiche'}</span></div>
 <div class="hub-tags">${tag(p.has_pdf?'PDF allegato':'PDF non allegato',!p.has_pdf)}${tag(readingsOK?'Letture presenti':'Letture da completare',!readingsOK)}${tag(row.provisional?'Possibile ripartizione provvisoria':'Da verificare')}${issues.length?tag(issues.length+' avvisi',true):''}</div>
 ${issues.length?`<details style="margin-top:10px"><summary class="hub-sub">Mostra gli avvisi (${issues.length})</summary><ul>${issues.map(x=>`<li>${safe(x)}</li>`).join('')}</ul></details>`:''}
 <div class="actions"><a class="btn" href="/percorso?step=pronte&document=${url}">Verifica e ripartisci</a><a class="btn light" href="/nuova-bolletta?edit=${url}">Visualizza / modifica</a>${p.has_pdf?`<a class="btn light" href="/api/readings/pending_pdf/${url}" target="_blank" rel="noopener">Apri PDF</a>`:''}<button type="button" class="hub-button" data-delete="${safe(id)}">Elimina</button></div>
 <div class="hub-upload"><label style="margin:0;white-space:nowrap">${p.has_pdf?'Sostituisci PDF:':'Allega PDF:'}</label><input aria-label="PDF della fattura ${safe(p.invoice_number||id)}" type="file" accept=".pdf,application/pdf" data-file="${safe(id)}"><button class="btn light" type="button" data-upload="${safe(id)}">Carica</button></div></article>`;
}
function postedCard(b,flags){const needsReview=flags.has(String(b.id));return `<article class="hub-item"><div class="subheading"><div><h3>${safe(names[b.utility]||b.utility)} · ${safe(b.invoiceNumber||'Senza numero')}</h3><div class="details">${showDate(b.start)} – ${showDate(b.end)} · ${moneyEur(b.total)} · Emessa ${showDate(b.invoiceDate)}</div></div><span class="pill ${needsReview?'yellow':''}">${needsReview?'Da verificare dopo rettifica':'Contabilizzata'}</span></div><div class="actions">${needsReview?'<a href="/analisi" class="btn">Verifica impatto rettifica</a>':''}<a href="/" class="btn light">Rendiconti</a><a href="/documenti?bill_id=${encodeURIComponent(b.id)}" class="btn light">Documenti</a><a href="/letture?tab=confronto" class="btn light">Confronta letture</a></div></article>`}
async function reload(){const [w,r]=await Promise.all([req('/api/workflow'),req('/api/readings')]);
 const pending=w.pending||[],posted=r.bills||[],reviewIds=new Set((r.reading_review_flags||[]).map(f=>String(f.bill_id)));
 $('#hub-stats').innerHTML=`<div class="card"><small>In attesa</small><strong>${pending.length}</strong></div><div class="card"><small>Da verificare o integrare</small><strong>${pending.filter(x=>!x.can_post).length}</strong></div><div class="card"><small>Con verifica disponibile</small><strong>${pending.filter(x=>x.can_post).length}</strong></div><div class="card"><small>Contabilizzate</small><strong>${posted.length}</strong></div><div class="card"><small>Da rivedere per rettifiche</small><strong>${reviewIds.size}</strong></div>`;
 $('#pending-list').innerHTML=pending.length?`<div class="hub-list">${pending.map(pendingCard).join('')}</div>`:'<div class="hub-empty">Nessuna fattura in attesa. Puoi registrarne una nuova.</div>';
 $('#posted-list').innerHTML=posted.length?`<div class="hub-list">${[...posted].sort((a,b)=>String(b.end).localeCompare(String(a.end))).slice(0,20).map(b=>postedCard(b,reviewIds)).join('')}</div>`:'<div class="hub-empty">Non risultano bollette contabilizzate.</div>';
 if(focus&&!focused){focused=true;const target=[...document.querySelectorAll('[data-pending]')].find(e=>e.dataset.pending===focus);if(target)requestAnimationFrame(()=>target.scrollIntoView({behavior:'smooth',block:'center'}))}
}
async function deletePending(id){if(!confirm('Eliminare questa fattura IN ATTESA e il PDF eventualmente allegato? L’operazione non potrà essere annullata.'))return;busy=true;try{await req('/api/readings/pending/delete',{id});await reload();alertMsg('Fattura in attesa eliminata. Nessun addebito era stato generato.')}catch(e){alertMsg(e.message,true)}finally{busy=false}}
async function uploadPending(id,button){const input=[...document.querySelectorAll('[data-file]')].find(e=>e.dataset.file===id),file=input?.files?.[0];if(!file){alertMsg('Seleziona un PDF da caricare.',true);return}if(file.size>15*1024*1024||file.size<8||!file.name.toLowerCase().endsWith('.pdf')){alertMsg('È richiesto un PDF valido, massimo 15 MB.',true);return}busy=true;button.disabled=true;try{const q=new URLSearchParams({id,filename:file.name});const resp=await fetch('/api/readings/pending_pdf?'+q,{method:'POST',headers:{'X-Casa-Consumi':'1','Content-Type':'application/pdf'},body:file});const answer=await resp.json();if(!resp.ok)throw Error(answer.error||'Caricamento non riuscito');await reload();alertMsg('Documento allegato alla fattura. Nessun addebito creato.')}catch(e){alertMsg(e.message,true)}finally{busy=false;button.disabled=false}}
$('#pending-list').addEventListener('click',e=>{if(busy)return;const del=e.target.closest('[data-delete]'),up=e.target.closest('[data-upload]');if(del)deletePending(del.dataset.delete);if(up)uploadPending(up.dataset.upload,up)});
$('#refresh').addEventListener('click',()=>reload().then(()=>alertMsg('Elenco aggiornato.')).catch(e=>alertMsg(e.message,true)));
reload().catch(e=>alertMsg('Impossibile caricare le fatture: '+e.message,true));
})();
