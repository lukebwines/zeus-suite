'use strict';
const $=id=>document.getElementById(id);
const safe=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=x=>(Number(x||0)/100).toLocaleString('it-IT',{style:'currency',currency:'EUR'});
let data=null;
function notice(message,bad=false){let el=$('zm-info');el.textContent=message;el.className='zb-message'+(bad?' bad':'');el.hidden=false;}
async function reload(){const r=await fetch('/api/finance/maintenance',{credentials:'same-origin',cache:'no-store'}),obj=await r.json();if(!r.ok)throw Error(obj.error||'Lettura non disponibile');data=obj;draw();}
function draw(){const rows=[];
 for(const d of data.deposits){
  let activeReturns=(d.events||[]).filter(e=>e.kind==='returned'&&!e.voided_at);
  const status=d.voided_at?'Annullato':activeReturns.length?'Restituzioni collegate':'Attivo';
  let action=d.voided_at?'—':activeReturns.length?'Prima annulla le restituzioni':`<button type="button" class="zb-smallbtn zb-danger" data-void-deposit="${safe(d.id)}">Annulla deposito</button>`;
  rows.push(`<tr><td>${safe(d.tenant_label)}<div class="zb-muted">${safe(d.contract_reference)}</div></td><td>${safe(d.apartment==='0'?'Primo Piano':'Piano Terra')}</td><td class="num">${money(d.voided_at?d.original_received_cents:d.received_cents)}</td><td class="num">${money(d.voided_at?d.original_returned_cents:d.returned_cents)}</td><td>${safe(status)}</td><td>${action}</td></tr>`);
  for(const e of d.events||[]){if(e.kind!=='returned')continue;
   const label=e.voided_at?'Annullata':'Registrata';
   rows.push(`<tr><td class="zb-muted">↳ Restituzione ${safe(e.event_date)}</td><td>—</td><td class="num">—</td><td class="num">${money(e.amount_cents)}</td><td>${label}</td><td>${e.voided_at?'—':`<button class="zb-smallbtn zb-danger" type="button" data-void-return="${safe(d.id)}" data-event="${safe(e.id)}">Annulla restituzione</button>`}</td></tr>`);
  }
 }
 $('zm-deposits').innerHTML=`<table class="zb-table"><thead><tr><th>Contratto / movimento</th><th>Immobile</th><th class="num">Ricevuto</th><th class="num">Restituito</th><th>Stato</th><th>Correzione</th></tr></thead><tbody>${rows.join('')||'<tr><td colspan="6">Nessuna registrazione presente.</td></tr>'}</tbody></table>`;
 const corrections=data.corrections.map(x=>`<tr><td>${safe(x.created_at.slice(0,19).replace('T',' '))}</td><td>${safe(x.action==='annullamento_deposito'?'Deposito annullato':'Restituzione annullata')}</td><td>${safe(x.reason)}</td><td><code>${safe(x.deposit_id)}</code></td></tr>`);
 $('zm-log').innerHTML=`<table class="zb-table"><thead><tr><th>Data UTC</th><th>Operazione</th><th>Motivazione</th><th>Riferimento</th></tr></thead><tbody>${corrections.join('')||'<tr><td colspan="4">Nessuna correzione registrata.</td></tr>'}</tbody></table>`;
}
function ask(id,eventId){const form=$('zm-form');form.reset();form.elements.id.value=id;form.elements.event_id.value=eventId||'';form.elements.operation.value=eventId?'deposit/return/void':'deposit/void';
 const dep=data.deposits.find(x=>x.id===id);$('zm-hint').textContent=eventId?`Restituzione collegata a ${dep?.tenant_label||'deposito'}. La rettifica ripristina la liquidità e il debito cauzionale.`:`Deposito di ${dep?.tenant_label||'inquilino'}: l'importo verrà escluso dalla liquidità e dal debito da restituire.`;$('zm-dialog').showModal();}
document.addEventListener('click',e=>{const x=e.target.closest?.('[data-void-deposit],[data-void-return]');if(!x)return;ask(x.dataset.voidDeposit||x.dataset.voidReturn,x.dataset.event||'');});
$('zm-cancel').addEventListener('click',()=>$('zm-dialog').close());
$('zm-refresh').addEventListener('click',()=>reload().catch(e=>notice(e.message,true)));
$('zm-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,body=Object.fromEntries(new FormData(f).entries()),operation=body.operation;delete body.operation;if(!confirm('Confermi l’annullamento per errore materiale? Il movimento resterà nello storico, ma non sarà più conteggiato.'))return;
 try{const r=await fetch('/api/finance/'+operation,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Casa-Consumi':'1'},body:JSON.stringify(body)}),out=await r.json();if(!r.ok)throw Error(out.error||'Operazione non riuscita');$('zm-dialog').close();await reload();notice('Annullamento registrato e saldi aggiornati.')}catch(err){notice(err.message,true)}});
reload().catch(e=>notice(e.message,true));
