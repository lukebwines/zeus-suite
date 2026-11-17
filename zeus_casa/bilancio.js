'use strict';
const $=id=>document.getElementById(id);
const money=v=>(Number(v||0)/100).toLocaleString('it-IT',{style:'currency',currency:'EUR'});
const safe=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const monthNames=['Gen','Feb','Mar','Apr','Mag','Giu','Lug','Ago','Set','Ott','Nov','Dic'];
const natureNames={economic:'Economica',patrimonial:'Patrimoniale',pass_through:'Partita di giro'};
const kindNames={income:'Entrata',expense:'Uscita',capital_in:'Apporto',capital_out:'Prelievo'};
let MODEL=null, VIEW='economic',TENANTS=[];
function info(msg,error=false){const e=$('zb-message');e.textContent=msg;e.className='zb-message'+(error?' bad':'');e.hidden=false;e.scrollIntoView({block:'nearest'});}
function table(head,rows){return `<table class="zb-table"><thead><tr>${head.map((h,i)=>`<th class="${i?'num':''}">${safe(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`}
const tr=(cells,klass='')=>`<tr class="${klass}">${cells.map((c,i)=>`<td class="${i?'num':''}">${c}</td>`).join('')}</tr>`;
function cards(items){return `<div class="zb-summary">${items.map(x=>`<div class="zb-card"><small>${safe(x[0])}</small><strong class="${x[2]||''}">${money(x[1])}</strong></div>`).join('')}</div>`}
function tableRowCategory(r,forecast=false){return tr([`${safe(r.group===r.category?r.category:r.group+' → '+r.category)}<div class="zb-muted">${natureNames[r.nature]||r.nature}</div>`,money(r.actual_cents),money(r.forecast_cents)],'');}
function showTab(tab){VIEW=tab;document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-selected',String(b.dataset.tab===tab))});for(const n of ['economic','cash','forecast','categories','deposits'])$('zb-'+n).hidden=n!==tab;
 const e=MODEL?.economic,c=MODEL?.cash;
 if(!e||!c)return;
 let values;
 if(tab==='economic')values=[['Ricavi incassati',e.income_cents,'zb-good'],['Costi pagati',e.costs_cents,'zb-bad'],['Interessi e oneri pagati',e.interest_cents+e.fees_cents,'zb-bad'],['Risultato gestionale',e.result_cents,e.result_cents<0?'zb-bad':'zb-good']];
 else if(tab==='cash')values=[['Liquidità effettiva',c.current_cents,'zb-good'],['Cauzioni da restituire',c.deposit_outstanding_cents,'zb-bad'],['Liquidità meno cauzioni',c.net_of_deposits_cents],['Saldo futuro stimato',c.end_forecast_cents]];
 else if(tab==='deposits')values=[['Depositi da restituire',c.deposit_outstanding_cents],['Depositi incassati nell’anno',c.deposit_received_cents],['Restituiti nell’anno',c.deposit_returned_cents],['Liquidità meno cauzioni',c.net_of_deposits_cents]];
 else if(tab==='forecast')values=[['Entrate registrate',e.income_cents],['Entrate non confermate',e.income_unconfirmed_cents],['Uscite registrate',e.costs_cents],['Uscite non confermate',e.costs_unconfirmed_cents]];
 else values=[['Categorie disponibili',MODEL.categories.filter(x=>x.active).length*100,''],['Categorie disattivate',MODEL.categories.filter(x=>!x.active).length*100,''],['Ricavi registrati',e.income_cents],['Costi registrati',e.costs_cents]];
 if(tab==='categories'){
   $('zb-summary').innerHTML=`<div class="zb-card"><small>Categorie attive</small><strong>${MODEL.categories.filter(x=>x.active).length}</strong></div><div class="zb-card"><small>Categorie disattivate</small><strong>${MODEL.categories.filter(x=>!x.active).length}</strong></div>`;
 }else $('zb-summary').innerHTML=cards(values);
}
function render(){if(!MODEL)return;const m=MODEL,e=m.economic,c=m.cash;
 $('zb-economic-summary').innerHTML=cards([['Ricavi effettivi',e.income_cents,'zb-good'],['Costi effettivi',e.costs_cents,'zb-bad'],['Risultato operativo',e.operating_cents],['Dopo interessi e oneri',e.result_cents,e.result_cents<0?'zb-bad':'zb-good']]);
 const rows=m.groups.filter(x=>x.nature==='economic'&&(x.actual_cents||x.forecast_cents)).map(r=>tableRowCategory(r));
 rows.push(tr(['<b>Risultato gestione operativa</b>',`<b>${money(e.operating_cents)}</b>`,money(e.operating_cents+e.income_unconfirmed_cents-e.costs_unconfirmed_cents)],'tot'));
 rows.push(tr(['Interessi sui prestiti pagati',money(e.interest_cents),money(e.interest_expected_cents)]));
 rows.push(tr(['Spese finanziarie pagate',money(e.fees_cents),'—']));
 rows.push(tr(['<b>Risultato dopo oneri finanziari</b>',`<b>${money(e.result_cents)}</b>`,'—'],'tot'));
 $('zb-economic-table').innerHTML=table(['Voce','Effettivo','Non confermato / previsto'],rows);
 $('zb-apartments').innerHTML=(m.unverified_common_entries?.length?'<p class="warning">Attenzione: '+m.unverified_common_entries.length+' movimenti comuni storici senza percentuale verificabile sono esclusi dalla ripartizione per appartamento. Verificare i documenti prima di attribuirli.</p>':'')+table(['Immobile','Risultato operativo effettivo'],['0','1'].map(a=>tr([safe(m.names[Number(a)]||'Appartamento '+a),money(m.apartments[a])])));
 $('zb-cash-cards').innerHTML=cards([['Saldo iniziale anno',c.opening_cents],['Disponibilità effettiva oggi',c.current_cents],['Cauzioni da restituire',c.deposit_outstanding_cents],['Saldo previsto a fine anno',c.end_forecast_cents]]);
 $('zb-cash-months').innerHTML=table(['Mese','Entrate incassate','Uscite pagate','Cauzioni ricevute','Cauzioni restituite','Rate pagate','Previsioni nette','Saldo previsto'],m.months.map((x,i)=>tr([monthNames[i],money(x.income_actual),money(x.expense_actual),money(x.deposit_received),money(x.deposit_returned),money(x.debt_paid_actual),money(x.forecast_net),`<b>${money(x.cash_projection)}</b>`])));
 const forecast=m.groups.map(r=>tr([`${safe(r.category)} <span class="zb-tag">${safe(natureNames[r.nature])}</span>`,money(r.actual_cents),money(r.forecast_cents),money(r.actual_cents+r.forecast_cents)]));
 $('zb-forecast-table').innerHTML=table(['Categoria','Accertato','Non confermato','Totale indicativo'],forecast);
 const catrows=m.categories.map(r=>tr([`${safe(r.name)}${r.parent_id?'<span class="zb-muted"> · sottocategoria</span>':''}`,safe(kindNames[r.kind]||r.kind),safe(natureNames[r.nature]),r.active?'Attiva':'Disattivata',`<button type="button" class="zb-smallbtn" data-edit-cat="${safe(r.id)}">Modifica</button>`]));
 $('zb-categories-table').innerHTML=table(['Categoria','Tipologia','Classificazione','Stato','Azioni'],catrows);
 $('zb-deposit-summary').innerHTML=cards([['Depositi incassati nell’anno',c.deposit_received_cents],['Restituzioni nell’anno',c.deposit_returned_cents],['Obblighi residui oggi',c.deposit_outstanding_cents],['Liquidità al netto',c.net_of_deposits_cents]]);
 const depRows=m.deposits.map(d=>tr([`${safe(d.tenant_label)}<small class="zb-muted">${safe(d.contract_reference||'Contratto di locazione')}</small>`,safe(m.names[Number(d.apartment)]||'Appartamento'),money(d.received_cents),money(d.returned_cents),`<b>${money(d.outstanding_cents)}</b>`,`<div class="zb-inline"><button class="zb-smallbtn" type="button" data-return="${safe(d.id)}" ${!d.outstanding_cents?'disabled':''}>Restituisci</button><button class="zb-smallbtn zb-danger" type="button" data-void-deposit="${safe(d.id)}">Annulla</button></div>`]));
 $('zb-deposits-table').innerHTML=table(['Inquilino/contratto','Immobile','Ricevuto','Restituito','Residuo','Azioni'],depRows.length?depRows:[tr(['Nessun deposito registrato','','','','',''])]);
 showTab(VIEW);
}
async function getData(){const year=$('zb-year').value;const [b,f]=await Promise.all([fetch('/api/finance/balance?year='+encodeURIComponent(year),{credentials:'same-origin'}),fetch('/api/finance?year='+encodeURIComponent(year),{credentials:'same-origin'})]);const d=await b.json(),aux=await f.json();if(!b.ok)throw Error(d.error||'Errore bilancio');if(!f.ok)throw Error(aux.error||'Errore anagrafiche');MODEL=d;TENANTS=(aux.tenants||[]);render();}
async function write(path,obj){const r=await fetch('/api/finance/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-Casa-Consumi':'1'},credentials:'same-origin',body:JSON.stringify(obj)});const d=await r.json();if(!r.ok)throw Error(d.error||'Salvataggio non riuscito');await getData();return d;}
function obj(form){const o=Object.fromEntries(new FormData(form).entries());if(form.id==='zb-category-form')o.active=Boolean(form.elements.active.checked);return o;}
function updateParent(){const f=$('zb-category-form'),cur=f.elements.parent_id.value;f.elements.parent_id.replaceChildren(new Option('Nessuna: categoria principale',''));for(const r of MODEL.categories.filter(x=>x.kind===f.elements.kind.value&&x.nature===f.elements.nature.value&&!x.parent_id&&x.active&&x.id!==f.elements.id.value))f.elements.parent_id.add(new Option(r.name,r.id));if([...f.elements.parent_id.options].some(x=>x.value===cur))f.elements.parent_id.value=cur;}
function openCategory(id){const f=$('zb-category-form');f.reset();const r=MODEL.categories.find(x=>x.id===id);f.elements.id.value=r?.id||'';f.elements.name.value=r?.name||'';f.elements.kind.value=r?.kind||'expense';f.elements.nature.value=r?.nature||'economic';f.elements.kind.disabled=!!r;f.elements.parent_id.disabled=!!r;f.elements.active.checked=r?Boolean(r.active):true;updateParent();f.elements.parent_id.value=r?.parent_id||'';$('zb-category-dialog').showModal();}
function openDeposit(){const f=$('zb-deposit-form');f.reset();f.elements.date.value=new Date().toLocaleDateString('en-CA');const select=$('zb-tenants');select.replaceChildren(new Option('Seleziona un contratto',''));for(const t of TENANTS.filter(t=>['0','1'].includes(t.apartment)))select.add(new Option((t.name||'Senza nome')+' · '+(MODEL.names[Number(t.apartment)]||'Appartamento'),t.id));$('zb-deposit-dialog').showModal();}
function openReturn(id){const d=MODEL.deposits.find(x=>x.id===id);if(!d)return;const f=$('zb-return-form');f.reset();f.elements.id.value=id;f.elements.date.value=new Date().toLocaleDateString('en-CA');f.elements.amount.value=(d.outstanding_cents/100).toFixed(2);f.elements.amount.max=(d.outstanding_cents/100).toFixed(2);$('zb-return-hint').textContent=`${d.tenant_label} · ancora da restituire ${money(d.outstanding_cents)}`;$('zb-return-dialog').showModal();}
function openVoidDeposit(id){
 const d=MODEL?.deposits.find(x=>x.id===id);if(!d)return;
 const f=$('zb-void-form');f.reset();f.elements.id.value=id;
 $('zb-void-hint').textContent=d.returned_cents?
  'Questo deposito presenta restituzioni: prima devi annullarle da Amministrazione → Manutenzione dati.':
  `${d.tenant_label} · ${money(d.received_cents)} · L’annullamento non cancella lo storico, ma rimuove l’importo dai saldi.`;
 $('zb-void-dialog').showModal();
}
function exportCsv(){if(!MODEL)return;const lines=[['Bilancio ZEUS '+MODEL.year],['Tipologia','Gruppo','Categoria','Natura','Effettivo EUR','Non confermato EUR']];for(const r of MODEL.groups)lines.push([r.kind,r.group,r.category,natureNames[r.nature],(r.actual_cents/100).toFixed(2),(r.forecast_cents/100).toFixed(2)]);lines.push([],['Depositi cauzionali'],['Inquilino','Ricevuto EUR','Restituito EUR','Residuo EUR']);for(const d of MODEL.deposits)lines.push([d.tenant_label,(d.received_cents/100).toFixed(2),(d.returned_cents/100).toFixed(2),(d.outstanding_cents/100).toFixed(2)]);const csv=lines.map(x=>x.map(y=>'"'+String(y??'').replace(/"/g,'""')+'"').join(';')).join('\r\n');const url=URL.createObjectURL(new Blob(['\ufeff',csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`zeus-bilancio-${MODEL.year}.csv`;a.click();URL.revokeObjectURL(url);}
(async()=>{
 const current=new Date().getFullYear();for(let y=current-4;y<=current+15;y++)$('zb-year').add(new Option(String(y),String(y)));$('zb-year').value=String(current);
 document.querySelectorAll('[data-tab]').forEach(x=>x.addEventListener('click',()=>showTab(x.dataset.tab)));
 const anchor=location.hash.slice(1);if(['economic','cash','forecast','categories','deposits'].includes(anchor))VIEW=anchor;
 $('zb-year').addEventListener('change',()=>getData().catch(e=>info(e.message,true)));$('zb-reload').addEventListener('click',()=>getData().catch(e=>info(e.message,true)));
 $('zb-print').addEventListener('click',()=>print());$('zb-export').addEventListener('click',exportCsv);
 $('zb-new-category').addEventListener('click',()=>openCategory());$('zb-new-deposit').addEventListener('click',openDeposit);
 $('zb-category-form').elements.kind.addEventListener('change',updateParent);$('zb-category-form').elements.nature.addEventListener('change',updateParent);
 document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>$(b.dataset.close).close()));
 document.addEventListener('click',e=>{const b=e.target.closest?.('[data-edit-cat],[data-return],[data-void-deposit]');if(!b)return;if(b.dataset.editCat)openCategory(b.dataset.editCat);else if(b.dataset.return)openReturn(b.dataset.return);else if(b.dataset.voidDeposit)openVoidDeposit(b.dataset.voidDeposit)});
 $('zb-category-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,v=obj(f);v.kind=f.elements.kind.value;try{await write(v.id?'category/update':'category/add',v);$('zb-category-dialog').close();info('Categoria salvata.')}catch(ex){info(ex.message,true)}});
 $('zb-deposit-form').addEventListener('submit',async e=>{e.preventDefault();try{await write('deposit/add',obj(e.currentTarget));$('zb-deposit-dialog').close();info('Deposito registrato come passività verso l’inquilino.')}catch(ex){info(ex.message,true)}});
 $('zb-void-form').addEventListener('submit',async e=>{e.preventDefault();if(!confirm('Confermi l’annullamento contabile di questo deposito? Verrà escluso dai saldi, ma conservato nello storico.'))return;try{await write('deposit/void',obj(e.currentTarget));$('zb-void-dialog').close();info('Deposito annullato; liquidità e saldi ricalcolati.')}catch(ex){info(ex.message,true)}});
 $('zb-return-form').addEventListener('submit',async e=>{e.preventDefault();try{await write('deposit/return',obj(e.currentTarget));$('zb-return-dialog').close();info('Restituzione registrata: ridotti liquidità e deposito da restituire.')}catch(ex){info(ex.message,true)}});
 try{await getData();}catch(e){info(e.message,true)}
})();
