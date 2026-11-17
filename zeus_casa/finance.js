"use strict";
/* ZEUS Finance v1.6.7 — property-only ledger; all money in cents on server. */
const $ = id => document.getElementById(id);
let MODEL=null;
const euro=c=>(Number(c||0)/100).toLocaleString('it-IT',{style:'currency',currency:'EUR'});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const kinds={income:'Entrata',expense:'Uscita',capital_in:'Dotazione / apporto',capital_out:'Prelievo'};
const freqs={once:'Una tantum',monthly:'Mensile',quarterly:'Trimestrale',semiannual:'Semestrale',annual:'Annuale'};
let categories={income:['Canone di locazione','Affitto','Altra entrata immobiliare'],expense:['Manutenzione ordinaria','Manutenzione straordinaria','IMU','Cedolare secca','IRPEF','Assicurazione immobile','Spese condominiali proprietà','Consulenza','Altre spese immobiliari'],capital_in:['Dotazione iniziale','Apporto di liquidità'],capital_out:['Prelievo del proprietario']};
const monthNames=['Gen','Feb','Mar','Apr','Mag','Giu','Lug','Ago','Set','Ott','Nov','Dic'];
const own=key=>Object.prototype.hasOwnProperty.call(MODEL||{},key);
function show(msg,error=false){const n=$('finance-message');n.textContent=msg;n.className='f-message'+(error?' bad':'');n.hidden=false;n.scrollIntoView({block:'nearest',behavior:'smooth'});}
async function getModel(){const year=$('f-year').value;const resp=await fetch('/api/finance?year='+encodeURIComponent(year),{credentials:'same-origin'});const data=await resp.json();if(!resp.ok)throw Error(data.error||'Impossibile aprire ZEUS Finance');MODEL=data;for(const kind of Object.keys(categories))categories[kind]=(data.categories||[]).filter(c=>c.kind===kind&&c.active).map(c=>c.name);render();}
async function send(route,payload){const r=await fetch('/api/finance/'+route,{method:'POST',headers:{'Content-Type':'application/json','X-Casa-Consumi':'1'},body:JSON.stringify(payload),credentials:'same-origin'});const data=await r.json();if(!r.ok)throw Error(data.error||'Salvataggio non riuscito');await getModel();return data;}
function optCategory(select,kind){const selected=select.value;select.replaceChildren();for(const c of categories[kind]||categories.income){const o=document.createElement('option');o.value=c;o.textContent=c;select.appendChild(o);}if([...select.options].some(x=>x.value===selected))select.value=selected;}
function apartment(r){return r.apartment==='common'?'Comune':(MODEL.names?.[Number(r.apartment)]||'Appartamento '+(Number(r.apartment)+1));}
function scheduleStatus(o,asOf){
 const expired=o.occurrence_date<asOf;
 if(o.settled)return {label:'REGISTRATO',klass:'f-status-registered'};
 return expired?{label:o.kind==='income'?'DA INCASSARE · SCADUTO':'DA PAGARE · SCADUTO',klass:'f-status-overdue'}:{label:'PREVISTO',klass:'f-status-planned'};
}
function scheduleBadge(o,asOf){const status=scheduleStatus(o,asOf);return '<span class="f-status-badge '+status.klass+'">'+status.label+'</span>';}
function render(){const m=MODEL,t=m.totals;
 const warning=$('f-horizon-note');
 const ended=(m.schedules||[]).filter(s=>s.active&&s.end_date&&s.end_date<`${m.year}-01-01`);
 if(m.year>new Date().getFullYear()&&t.income_expected===0&&t.expense_expected===0&&t.debt_expected===0){
   warning.textContent=`Nel ${m.year} non risultano nuove entrate, spese o rate programmate. ${ended.length?'Alcune programmazioni terminano negli anni precedenti: controlla le loro date di fine.':'Inserisci i programmi ricorrenti per estendere la previsione.'} Il saldo riportato deriva dalle previsioni pregresse e dai movimenti registrati.`;
   warning.hidden=false;
 }else warning.hidden=true;
 $('f-kpis').innerHTML=[
  ['Disponibilità effettiva oggi',m.current_cash,'Solo movimenti registrati'],
  ['Risultato operativo effettivo',t.operating_actual,'Canoni incassati − spese pagate'],
  ['Risultato operativo previsto',t.operating_forecast,'Escluso rimborso capitale e interessi'],
  ['Saldo previsto fine anno',t.end_cash_forecast,'Incluse rate di debito collegate']
 ].map(([title,value,desc],i)=>`<div class="tile${i===3?' accent':''}"><small>${esc(title)}</small><strong>${euro(value)}</strong><p>${esc(desc)}</p></div>`).join('');
 // Four physically separate cash-flow series. Apporti iniziali and
 // proprietario prelievi stay in opening/cumulative cash, not the flow chart. Debt repayments and disbursements
 // are included once; forecast debt instalments count as expected OUT, never as paid.
 const series=m.months.map(x=>({
  inActual:x.income_actual+x.debt_funding_actual,
  depositIn:x.deposit_received,
  inForecast:x.income_expected,
  outActual:x.expense_actual+x.debt_paid_actual,
  depositOut:x.deposit_returned,
  outForecast:x.expense_expected+x.debt_expected
 }));
 const maxFlow=Math.max(1,...series.flatMap(v=>Object.values(v)));
 const niceStep=v=>{const pow=10**Math.floor(Math.log10(v));return [1,2,5,10].map(k=>k*pow).find(k=>k>=v)||10*pow;};
 const axis=niceStep(maxFlow);const axisTxt=euro(axis);
 const graph=$('f-chart');
 graph.innerHTML=`<div class="f-chart-area" role="img" aria-label="Flussi mensili: verde scuro entrate effettive, verde chiaro entrate previste, rosso scuro uscite pagate, rosso chiaro uscite previste; blu depositi cauzionali ricevuti, viola cauzioni restituite.">
 <div class="f-flow-axis" aria-hidden="true"><span>${axisTxt}</span><span>${euro(0)}</span><span>−${axisTxt}</span></div>
 <div class="f-flow-months">${series.map((v,i)=>`<div class="f-flow-month" aria-label="${monthNames[i]}: incassi effettivi ${euro(v.inActual)}, entrate attese ${euro(v.inForecast)}, uscite effettive ${euro(v.outActual)}, uscite previste ${euro(v.outForecast)}, cauzioni ricevute ${euro(v.depositIn)}, cauzioni restituite ${euro(v.depositOut)}">
 <div class="f-flow-half f-flow-positive"><div class="f-flow-bar f-flow-in-actual" data-value="${v.inActual}" title="${monthNames[i]} · Entrate accertate: ${euro(v.inActual)}"></div><div class="f-flow-bar f-flow-in-forecast" data-value="${v.inForecast}" title="${monthNames[i]} · Entrate previste: ${euro(v.inForecast)}"></div><div class="f-flow-bar f-flow-deposit-in" data-value="${v.depositIn}" title="${monthNames[i]} · Depositi ricevuti: ${euro(v.depositIn)}"></div></div>
 <div class="f-flow-half f-flow-negative"><div class="f-flow-bar f-flow-out-actual" data-value="${v.outActual}" title="${monthNames[i]} · Uscite accertate: ${euro(v.outActual)}"></div><div class="f-flow-bar f-flow-out-forecast" data-value="${v.outForecast}" title="${monthNames[i]} · Uscite previste: ${euro(v.outForecast)}"></div><div class="f-flow-bar f-flow-deposit-out" data-value="${v.depositOut}" title="${monthNames[i]} · Depositi restituiti: ${euro(v.depositOut)}"></div></div>
 <b class="f-flow-month-label">${monthNames[i]}</b></div>`).join('')}</div></div>`;
 // Use CSSOM instead of inline style attributes: CSP disallows style-src inline.
 graph.querySelectorAll('.f-flow-bar').forEach(b=>{const amount=Number(b.dataset.value)||0;b.style.height=`${amount?Math.max(.8,100*amount/axis):0}%`;});
 $('f-month-table').querySelector('tbody').innerHTML=m.months.map((x,i)=>`<tr><td>${monthNames[i]}</td><td class="f-amount">${euro(x.income_actual)}</td><td class="f-amount">${euro(x.income_expected)}</td><td class="f-amount">${euro(x.expense_actual)}</td><td class="f-amount">${euro(x.deposit_received)}</td><td class="f-amount">${euro(x.deposit_returned)}</td><td class="f-amount">${euro(x.expense_expected)}</td><td class="f-amount">${euro(x.debt_paid_actual)}</td><td class="f-amount">${euro(x.debt_expected)}</td><td class="f-amount"><strong>${euro(x.cash_projection)}</strong></td></tr>`).join('');
 $('f-places').innerHTML=['0','1'].map(a=>`<div class="f-line"><span>${esc(m.names?.[Number(a)]||('Appartamento '+a))}</span><strong class="${m.apartment_results[a]>=0?'f-inflow':'f-outflow'}">${euro(m.apartment_results[a])}</strong></div>`).join('')+'<p class="f-note">Risultato operativo previsto, non saldo bancario né utile fiscale.</p>';
 const pending=m.occurrences.filter(o=>!o.settled).map(o=>({...o,type:'schedule'}));
 const installments=(m.debt_due_events||[]).map(o=>({...o,type:'debt'}));
 const due=[...pending,...installments].sort((a,b)=>a.occurrence_date.localeCompare(b.occurrence_date));
 $('f-due').innerHTML=due.length?due.map(o=>o.type==='debt'?
  `<div class="f-due-row"><div><b>Rata · ${esc(o.loan_label)}${o.status==='overdue'?' · SCADUTA / DA VERIFICARE':''}</b><small>${esc(o.occurrence_date)} · ${esc(apartment(o))} · ${o.status==='overdue'?'Quote da ricalcolare alla data del pagamento':`Capitale ${euro(o.principal_cents)} + interessi ${euro(o.interest_cents)}${o.fee_cents?' + spese '+euro(o.fee_cents):''} = ${euro(o.amount_cents)} (stimati)`}</small></div><div><button class="f-sml-btn" data-settle-debt="${esc(o.loan_id)}" data-due="${esc(o.occurrence_date)}">Registra rata</button>${o.status==='overdue'?` <button class="f-sml-btn" data-skip-debt="${esc(o.loan_id)}" data-due="${esc(o.occurrence_date)}">Salta rata</button>`:''}</div></div>` :
  `<div class="f-due-row"><div><b>${esc(o.label)}</b> ${scheduleBadge(o,m.as_of)}<small>${esc(o.occurrence_date)} · ${esc(apartment(o))} · ${euro(o.amount_cents)} (${o.kind==='income'?'entrata':'uscita'})</small></div><button class="f-sml-btn" data-settle="${esc(o.schedule_id)}" data-due="${esc(o.occurrence_date)}">Registra</button></div>`).join(''):'<div class="f-empty">Nessuna scadenza da registrare nell’anno selezionato.</div>';
 const skipped=(m.debt_skips||[]).filter(r=>r.occurrence_date.startsWith(String(m.year)));
 $('f-debt-skips').innerHTML=skipped.length?skipped.map(r=>`<div class="f-due-row"><div><b>Rata saltata · ${esc((m.available_debt_loans||[]).find(l=>l.id===r.loan_id)?.label||'Finanziamento')}</b><small>${esc(r.occurrence_date)} · ${esc(r.reason)} · Capitale non rimborsato, interessi ancora dovuti</small></div><button class="f-sml-btn" data-unskip-debt="${esc(r.loan_id)}" data-due="${esc(r.occurrence_date)}">Riattiva</button></div>`).join(''):'<div class="f-empty">Nessuna rata saltata nell’anno selezionato.</div>';
 const sch=m.schedules;
 $('f-schedules').innerHTML=sch.length?`<table><thead><tr><th>Programmazione</th><th>Appartamento</th><th>Periodicità</th><th>Importo</th><th>Stato</th><th>Azioni</th></tr></thead><tbody>${sch.map(s=>`<tr><td><b>${esc(s.label)}</b><small>${esc(s.category)}${s.tenant_label?' · '+esc(s.tenant_label):''}</small></td><td>${esc(apartment(s))}</td><td>${esc(freqs[s.frequency])}<small>Dal ${esc(s.start_date)}${s.end_date?' al '+esc(s.end_date):''}</small></td><td>${euro(s.amount_cents)}</td><td>${s.superseded_by?'Storica':s.active?'Attiva':'Sospesa'}${(()=>{const occ=m.occurrences.filter(o=>o.schedule_id===s.id);const overdue=occ.filter(o=>!o.settled&&o.occurrence_date<m.as_of);const future=occ.filter(o=>!o.settled&&o.occurrence_date>=m.as_of);const done=occ.filter(o=>o.settled);return '<div class="f-occurrence-status">'+(overdue.length?'<span class="f-status-badge f-status-overdue">'+(s.kind==='income'?'DA INCASSARE · SCADUTO':'DA PAGARE · SCADUTO')+' ('+overdue.length+')</span>':'')+(future.length?'<span class="f-status-badge f-status-planned">PREVISTO ('+future.length+')</span>':'')+(done.length?'<span class="f-status-badge f-status-registered">REGISTRATO ('+done.length+')</span>':'')+'</div>';})()} </td><td><div class="f-schedule-actions">${s.superseded_by?'':`<button class="f-sml-btn" data-edit-schedule="${esc(s.id)}">Modifica</button> <button class="f-sml-btn" data-close-schedule="${esc(s.id)}">Fine</button> <button class="f-sml-btn" data-toggle="${esc(s.id)}">${s.active?'Sospendi':'Riattiva'}</button> <button class="f-sml-btn danger" data-remove-schedule="${esc(s.id)}">Elimina</button>`}${(m.schedule_history||[]).some(h=>h.schedule_id===s.id||h.successor_id===s.id)?` <button class="f-sml-btn" data-history-schedule="${esc(s.id)}">Storico</button>`:''}</div></td></tr>`).join('')}</tbody></table>`:'<div class="f-empty">Nessuna entrata o uscita programmata. Inserisci il canone mensile di ciascun contratto e le prossime scadenze fiscali.</div>';
 const entries=m.entries.filter(x=>x.entry_date.startsWith(String(m.year)));
 $('f-ledger').innerHTML=entries.length?`<table><thead><tr><th>Data</th><th>Movimento</th><th>Appartamento</th><th>Categoria</th><th>Importo</th><th></th></tr></thead><tbody>${entries.map(r=>`<tr><td>${esc(r.entry_date)}</td><td>${esc(r.label)}${r.schedule_id?'<small>Da programmazione</small>':''}</td><td>${esc(apartment(r))}</td><td>${esc(r.category)}</td><td class="f-amount ${['expense','capital_out'].includes(r.kind)?'f-outflow':'f-inflow'}">${['expense','capital_out'].includes(r.kind)?'−':'+'}${euro(r.amount_cents)}</td><td><button class="f-sml-btn danger" data-delete-entry="${esc(r.id)}">Annulla</button></td></tr>`).join('')}</tbody></table>`:'<div class="f-empty">Nessun movimento effettivo nell’anno selezionato.</div>';
 const links=m.debt_links||[];
 $('f-debt-links').innerHTML=links.length?`<div class="f-table-wrap"><table><thead><tr><th>Finanziamento</th><th>Appartamento</th><th>Importo mensile</th><th>Collegato dal</th><th></th></tr></thead><tbody>${links.map(l=>`<tr><td><b>${esc(l.loan_label)}</b><small>Tasso ${esc(l.loan_rate)}% ACT/360 · ${l.loan_plan==='free'?'Rimborsi liberi':l.loan_installment_kind==='fixed_total'?'Rata totale fissa (capitale + interessi)':'Quota capitale mensile + interessi'}</small></td><td>${esc(apartment(l))}</td><td>${l.loan_installment_kind==='fixed_total'?euro(l.loan_installment_cents):l.principal_installment_cents?euro(l.principal_installment_cents):l.loan_installment_cents?euro(l.loan_installment_cents):'Nessuna rata programmata'}</td><td>${esc(l.start_date)}${l.first_due?'<small>Prima rata: '+esc(l.first_due)+'</small>':''}</td><td><button class="f-sml-btn" data-update-debt="${esc(l.loan_id)}">Modifica</button> <button class="f-sml-btn danger" data-unlink-debt="${esc(l.loan_id)}">Scollega</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="f-empty">Nessun finanziamento collegato. La ZEUS Lending rimane indipendente finché non autorizzi il collegamento.</div>';
 const totalPrincipal=m.totals.debt_principal_actual+m.totals.debt_principal_expected;
 $('f-debt-events').textContent=`Capitale rimborsato nell’anno: ${euro(totalPrincipal)} · Interessi effettivi: ${euro(m.totals.interest_actual)} · Interessi previsti: ${euro(m.totals.interest_expected)} · Risultato dopo interessi: ${euro(m.totals.after_interest_forecast)}. Questi importi non modificano la redditività operativa.`;
 const history=(m.debt_settlements||[]).filter(x=>x.occurrence_date.startsWith(String(m.year)));
 $('f-debt-history').innerHTML=history.length?history.map(x=>`<div class="f-due-row"><div><b>${esc((m.available_debt_loans||[]).find(l=>l.id===x.loan_id)?.label||'Prestito')}</b><small>Rata ${esc(x.occurrence_date)} · Pagata ${esc(x.payment_date)} · Capitale ${euro(x.principal_cents)} · Interessi ${euro(x.interest_cents)} · Spese ${euro(x.fee_cents)}</small></div><button class="f-sml-btn danger" data-void-debt="${esc(x.loan_id)}" data-due="${esc(x.occurrence_date)}">Annulla</button></div>`).join(''):'<div class="f-empty">Nessuna rata confermata da ZEUS Finance nell’anno selezionato.</div>';
 document.querySelectorAll('[data-settle-debt]').forEach(b=>b.addEventListener('click',()=>openDebtSettle(b.dataset.settleDebt,b.dataset.due)));
 document.querySelectorAll('[data-skip-debt]').forEach(b=>b.addEventListener('click',async()=>{
  const reason=prompt('Motivo del mancato pagamento della rata (non è una cancellazione del debito):');
  if(reason===null)return;
  try{await send('debt/skip',{loan_id:b.dataset.skipDebt,occurrence_date:b.dataset.due,reason});show('Rata segnata come saltata: nessun pagamento o rimborso inventato. Piano futuro ricalcolato.')}catch(e){show(e.message,true)}
 }));
 document.querySelectorAll('[data-unskip-debt]').forEach(b=>b.addEventListener('click',async()=>{
  const reason=prompt('Motivo della riattivazione della rata:');if(reason===null)return;
  try{await send('debt/unskip',{loan_id:b.dataset.unskipDebt,occurrence_date:b.dataset.due,reason});show('Rata riattivata. Rimane da pagare fino a conferma.')}catch(e){show(e.message,true)}
 }));
 document.querySelectorAll('[data-void-debt]').forEach(b=>b.addEventListener('click',async()=>{
  const reason=prompt('Motivo annullamento della rata (saranno annullati anche i pagamenti collegati in ZEUS Lending):');
  if(reason===null)return;
  try{await send('debt/void',{loan_id:b.dataset.voidDebt,occurrence_date:b.dataset.due,reason});show('Rata annullata in entrambi i moduli; lo storico è conservato.')}catch(e){show(e.message,true)}
 }));
 document.querySelectorAll('[data-unlink-debt]').forEach(btn=>btn.addEventListener('click',async()=>{if(!confirm('Scollegare questo finanziamento da ZEUS Finance? I dati in ZEUS Lending non saranno alterati.'))return;try{await send('debt/unlink',{loan_id:btn.dataset.unlinkDebt})}catch(e){show(e.message,true)}}));
 document.querySelectorAll('[data-update-debt]').forEach(btn=>btn.addEventListener('click',()=>openDebt(btn.dataset.updateDebt)));
 document.querySelectorAll('[data-settle]').forEach(btn=>btn.addEventListener('click',()=>openSettle(btn.dataset.settle,btn.dataset.due)));
 document.querySelectorAll('[data-edit-schedule]').forEach(btn=>btn.addEventListener('click',()=>openSchedule(btn.dataset.editSchedule)));
 document.querySelectorAll('[data-history-schedule]').forEach(btn=>btn.addEventListener('click',()=>openScheduleHistory(btn.dataset.historySchedule)));
 document.querySelectorAll('[data-close-schedule]').forEach(btn=>btn.addEventListener('click',async()=>{const d=prompt('Data di fine programmazione (AAAA-MM-GG):',new Date().toLocaleDateString('en-CA'));if(d===null)return;try{await send('schedule/close',{id:btn.dataset.closeSchedule,end_date:d})}catch(e){show(e.message,true)}}));
 document.querySelectorAll('[data-toggle]').forEach(btn=>btn.addEventListener('click',async()=>{try{await send('schedule/toggle',{id:btn.dataset.toggle})}catch(e){show(e.message,true)}}));
 document.querySelectorAll('[data-remove-schedule]').forEach(btn=>btn.addEventListener('click',async()=>{if(!confirm('Eliminare questa programmazione? Le scadenze già registrate restano nello storico.'))return;try{await send('schedule/delete',{id:btn.dataset.removeSchedule})}catch(e){show(e.message,true)}}));
 document.querySelectorAll('[data-delete-entry]').forEach(btn=>btn.addEventListener('click',async()=>{const reason=prompt('Motivo dell’annullamento (verrà conservato nello storico):');if(reason===null)return;try{await send('entry/delete',{id:btn.dataset.deleteEntry,reason})}catch(e){show(e.message,true)}}));
}
function openDebt(id=''){
 const f=$('f-debt-form'),sel=$('f-debt-loan');f.reset();sel.replaceChildren();
 for(const l of MODEL.available_debt_loans||[])sel.add(new Option(l.label+' · '+l.property_name,l.id));
 const link=(MODEL.debt_links||[]).find(x=>x.loan_id===id);
 if(link){sel.value=id;f.elements.apartment.value=link.apartment;f.elements.start_date.value=link.start_date;f.elements.first_due.value=link.first_due||'';f.elements.monthly_principal.value=link.principal_installment_cents?(link.principal_installment_cents/100).toFixed(2):'';}
 else {f.elements.start_date.value=new Date().toLocaleDateString('en-CA');}
 if(!sel.value){show('Non ci sono finanziamenti disponibili in ZEUS Lending.',true);return;}
 syncDebtInstallment();$('f-debt-dialog').showModal();
}
function syncDebtInstallment(){
 const f=$('f-debt-form'), l=(MODEL.available_debt_loans||[]).find(x=>x.id===f.elements.loan_id.value);
 const fixed=l&&l.installment_kind==='fixed_total';
 $('f-custom-principal').hidden=!!fixed;
 f.elements.monthly_principal.disabled=!!fixed;
 if(fixed)f.elements.monthly_principal.value='';
 $('f-debt-installment-note').textContent=fixed?'Il prestito usa una rata totale costante (comprende capitale e interessi). La quota capitale non va configurata qui.':'Lascia vuoto per importare la quota capitale dal piano del prestito.';
}
function openEntry(){const d=$('f-entry-dialog'),f=$('f-entry-form');f.reset();f.elements.date.value=new Date().toLocaleDateString('en-CA');optCategory($('f-entry-category'),'income');d.showModal();}
function openInitial(){openEntry();const f=$('f-entry-form');f.elements.kind.value='capital_in';updateCapital();f.elements.label.value='Dotazione iniziale';}
function openSchedule(id=null){
 const d=$('f-schedule-dialog'),f=$('f-schedule-form');f.reset();
 const today=new Date().toLocaleDateString('en-CA'),item=id?(MODEL.schedules||[]).find(s=>s.id===id):null;
 f.elements.id.value=item?.id||'';
 $('f-schedule-title').textContent=item?'Modifica programmazione':'Programma entrata o uscita';
 $('f-schedule-submit').textContent=item?'Salva modifiche':'Salva previsione';
 const versioned=Boolean(item&&item.frequency!=='once'&&(item.start_date<today||(MODEL.entries||[]).some(e=>e.schedule_id===item.id)));
 $('f-schedule-edit-note').hidden=!item;
 $('f-schedule-edit-note').textContent=versioned?'Ricorrenza già iniziata: imposta la decorrenza delle NUOVE condizioni (da oggi in poi). ZEUS conserverà la versione precedente e i movimenti effettuati.':'Puoi modificare i dati previsti. I movimenti effettivi già registrati non saranno alterati.';
 f.elements.start_date.value=versioned?today:item?.start_date||today;
 f.elements.end_date.value=item?.end_date||'';
 f.elements.day.value=item?.day||Number(today.slice(8,10));
 f.elements.kind.value=item?.kind||'income';
 f.elements.apartment.value=item?.apartment||'0';
 f.elements.frequency.value=item?.frequency||'monthly';
 optCategory($('f-schedule-category'),f.elements.kind.value);
 if(item&&![...f.elements.category.options].some(o=>o.value===item.category))f.elements.category.add(new Option(item.category,item.category));
 f.elements.category.value=item?.category||f.elements.category.value;
 syncTenants();
 if(item?.tenant_id){if(![...f.elements.tenant_id.options].some(x=>x.value===item.tenant_id))f.elements.tenant_id.add(new Option(item.tenant_label||'Contratto archiviato',item.tenant_id));f.elements.tenant_id.value=item.tenant_id;}
 f.elements.label.value=item?.label||'';
 f.elements.amount.value=item?eurInput(item.amount_cents):'';
 f.elements.common_share_ap1.value=item?.common_share_ap1??50;
 f.elements.notes.value=item?.notes||'';
 d.showModal();
}
function openScheduleHistory(id){
 const s=(MODEL.schedules||[]).find(x=>x.id===id),history=(MODEL.schedule_history||[]).filter(x=>x.schedule_id===id||x.successor_id===id);
 if(!s||!history.length)return;
 const d=$('f-schedule-history-dialog'),rows=$('f-schedule-history-content');
 const fmt=v=>{try{const x=JSON.parse(v);return `${euro(x.amount_cents)} · ${esc(x.start_date)}${x.end_date?' – '+esc(x.end_date):''} · ${esc(x.frequency)} · ${esc(x.label)}`;}catch{return 'Dati non disponibili';}};
 rows.innerHTML=history.map(h=>`<div class="f-history-row"><b>${esc(h.action==='nuova_versione'?'Nuova versione':'Modifica')} · ${esc(h.recorded_at.slice(0,16).replace('T',' '))} UTC</b><small>Prima: ${fmt(h.before_json)}</small><small>Dopo: ${fmt(h.after_json)}</small></div>`).join('');d.showModal();
}
function syncTenants(){const sel=$('f-schedule-tenant');const place=$('f-schedule-apartment').value;const kind=$('f-schedule-kind').value;sel.replaceChildren(new Option('Nessun collegamento',''));
 for(const t of MODEL?.tenants||[]){if(t.apartment===place&&kind==='income'){sel.add(new Option(t.name+(t.active?'':' · archiviato'),t.id));}}
 sel.disabled=kind!=='income'||place==='common';}
function updateCapital(){const f=$('f-entry-form'),type=f.elements.kind.value;optCategory($('f-entry-category'),type);if(type==='capital_in'||type==='capital_out'){f.elements.apartment.value='common';f.elements.apartment.disabled=true}else f.elements.apartment.disabled=false;}
function updateScheduleKind(){const f=$('f-schedule-form'),type=f.elements.kind.value;optCategory($('f-schedule-category'),type);syncTenants();}
function chooseTenant(){const f=$('f-schedule-form'),id=f.elements.tenant_id.value;if(!id)return;const t=(MODEL.tenants||[]).find(t=>t.id===id);if(!t)return;f.elements.apartment.value=t.apartment;f.elements.category.value='Canone di locazione';f.elements.label.value='Canone '+t.name;f.elements.frequency.value='monthly';if(t.start)f.elements.start_date.value=t.start;if(t.end)f.elements.end_date.value=t.end;const day=f.elements.start_date.value.slice(-2);if(day)f.elements.day.value=Number(day);}
function updateFrequency(){const f=$('f-schedule-form');if(f.elements.frequency.value==='once'){const d=f.elements.start_date.value;if(d){f.elements.end_date.value=d;f.elements.day.value=Number(d.slice(-2));}}}
let debtQuoteRequest=0;
const eurInput=c=>(Number(c||0)/100).toFixed(2);
async function refreshDebtQuote(){
 const f=$('f-debt-settle-form'),info=$('f-debt-quote-info');
 const request=++debtQuoteRequest;
 info.textContent='Ricalcolo degli interessi alla data effettiva…';
 try{
  const url='/api/finance/debt/quote?loan_id='+encodeURIComponent(f.elements.loan_id.value)+'&date='+encodeURIComponent(f.elements.date.value);
  const r=await fetch(url,{credentials:'same-origin'}),q=await r.json();
  if(!r.ok)throw Error(q.error||'Ricalcolo non riuscito');
  if(request!==debtQuoteRequest)return;
  f.elements.principal.value=eurInput(q.principal_suggested_cents);
  f.elements.interest.value=eurInput(q.interest_suggested_cents);
  f.elements.fee.value=eurInput(q.fee_suggested_cents);
  info.textContent=`Alla data ${q.date}: capitale residuo ${euro(q.principal_available_cents)}, interessi maturati ${euro(q.interest_available_cents)}, spese dovute ${euro(q.fee_available_cents)}. Le quote sono modificabili. Un pagamento parziale lascia il residuo nel debito.`;
 }catch(e){if(request===debtQuoteRequest){info.textContent='Verifica non riuscita: '+e.message;f.elements.principal.value='';f.elements.interest.value='';f.elements.fee.value='';}}
}
function openDebtSettle(loanId,due){
 const f=$('f-debt-settle-form'),d=$('f-debt-settle-dialog');
 f.reset();f.elements.loan_id.value=loanId;f.elements.occurrence_date.value=due;
 const today=new Date().toLocaleDateString('en-CA');
 f.elements.date.value=today;
 const event=(MODEL.debt_due_events||[]).find(x=>x.loan_id===loanId&&x.date===due);
 $('f-debt-settle-info').textContent=`${event?.loan_label||'Finanziamento'} · scadenza ${due}. La conferma registrerà un unico pagamento ripartito fra capitale, interessi e spese in ZEUS Lending e aggiornerà Finance.`;
 d.showModal();refreshDebtQuote();
}
function openSettle(id,due){const s=MODEL.schedules.find(s=>s.id===id);if(!s)return;const d=$('f-settle-dialog'),f=$('f-settle-form');f.reset();f.elements.id.value=id;f.elements.occurrence_date.value=due;f.elements.date.value=new Date().toLocaleDateString('en-CA');f.elements.amount.value=(s.amount_cents/100).toFixed(2);$('f-settle-info').textContent=`${s.kind==='income'?'Incasso':'Pagamento'}: ${s.label} · scadenza ${due}. Importo e data sono modificabili.`;d.showModal();}
function objForm(form){const obj=Object.fromEntries(new FormData(form).entries());for(const key of ['amount'])if(obj[key])obj[key]=obj[key].trim();return obj;}
function csv(){if(!MODEL)return;const num=c=>(Number(c||0)/100).toFixed(2).replace('.',',');let rows=[['ZEUS Finance '+MODEL.year],['Mese','Incassi effettivi','Entrate attese','Spese effettive','Depositi ricevuti','Depositi restituiti','Spese attese','Rate debiti pagate','Rate debiti previste','Saldo previsto']];for(const m of MODEL.months)rows.push([m.month,num(m.income_actual),num(m.income_expected),num(m.expense_actual),num(m.deposit_received),num(m.deposit_returned),num(m.expense_expected),num(m.debt_paid_actual),num(m.debt_expected),num(m.cash_projection)]);rows.push([],['Movimenti effettivi'],['Data','Tipologia','Appartamento','Categoria','Descrizione','Importo euro']);for(const r of MODEL.entries)if(r.entry_date.startsWith(String(MODEL.year)))rows.push([r.entry_date,kinds[r.kind],apartment(r),r.category,r.label,num(r.amount_cents*(r.kind==='expense'||r.kind==='capital_out'?-1:1))]);const str=rows.map(row=>row.map(v=>'"'+String(v??'').replace(/"/g,'""')+'"').join(';')).join('\r\n');const blob=new Blob(['\ufeff',str],{type:'text/csv;charset=utf-8;'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`zeus-finance-${MODEL.year}.csv`;a.click();URL.revokeObjectURL(url);}
(async function init(){const year=$('f-year');const current=new Date().getFullYear();for(let y=current-4;y<=current+15;y++){year.add(new Option(String(y),String(y)));}year.value=String(current);
 year.addEventListener('change',()=>getModel().catch(e=>show(e.message,true)));$('refresh').addEventListener('click',()=>getModel().catch(e=>show(e.message,true)));
 $('f-link-debt').addEventListener('click',()=>openDebt());
 $('f-debt-loan').addEventListener('change',syncDebtInstallment);
 $('f-debt-settle-form').elements.date.addEventListener('change',refreshDebtQuote);
 $('f-debt-settle-form').addEventListener('submit',async e=>{
  e.preventDefault();const v=objForm(e.currentTarget);
  if(!confirm('Confermi il pagamento di questa rata? Saranno registrati anche capitale e interessi in ZEUS Lending.'))return;
  try{await send('debt/settle',v);$('f-debt-settle-dialog').close();show('Rata registrata in ZEUS Finance e ZEUS Lending.')}catch(ex){show(ex.message,true)}
 });
 $('f-debt-form').addEventListener('submit',async e=>{e.preventDefault();try{await send('debt/link',objForm(e.currentTarget));$('f-debt-dialog').close();show('Finanziamento collegato: previsioni aggiornate.')}catch(ex){show(ex.message,true)}});
 $('f-new-entry').addEventListener('click',openEntry);$('f-initial').addEventListener('click',openInitial);$('f-new-schedule').addEventListener('click',openSchedule);$('f-open-schedule').addEventListener('click',openSchedule);
 $('f-export').addEventListener('click',csv);$('f-print').addEventListener('click',()=>window.print());
 document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>$(b.dataset.close).close()));
 $('f-entry-kind').addEventListener('change',updateCapital);$('f-schedule-kind').addEventListener('change',updateScheduleKind);
 $('f-schedule-apartment').addEventListener('change',syncTenants);$('f-schedule-tenant').addEventListener('change',chooseTenant);$('f-schedule-frequency').addEventListener('change',updateFrequency);
 $('f-entry-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget;const v=objForm(f);if(f.elements.apartment.disabled)v.apartment='common';try{await send('entry/add',v);$('f-entry-dialog').close();show('Movimento registrato correttamente.')}catch(ex){show(ex.message,true)}});
 $('f-schedule-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget;const v=objForm(f);if(v.frequency==='once'){v.end_date=v.start_date;v.day=Number(v.start_date.slice(-2));}try{const editing=Boolean(v.id),res=await send(editing?'schedule/update':'schedule/add',v);$('f-schedule-dialog').close();show(editing?(res.versioned?'Nuova versione creata dalla decorrenza indicata. Storico e movimenti registrati conservati.':'Programmazione aggiornata; previsioni ricalcolate.'):'Programmazione inserita: nessun movimento effettivo creato.')}catch(ex){show(ex.message,true)}});
 $('f-settle-form').addEventListener('submit',async e=>{e.preventDefault();const v=objForm(e.currentTarget);try{await send('schedule/settle',v);$('f-settle-dialog').close();show('Pagamento / incasso registrato, previsione sostituita dal dato effettivo.')}catch(ex){show(ex.message,true)}});
 try{await getModel();}catch(e){show(e.message,true)}
})();
