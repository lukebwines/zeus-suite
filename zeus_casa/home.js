'use strict';
/* ZEUS v1.7.3 — read-only dashboard, shared authenticated JSON APIs.
   All user content is inserted via textContent (never unsanitized HTML). */
(function(){
 const heroPhoto=document.querySelector('.zh-hero-photo');
 if(heroPhoto){
  const hideMissing=()=>{heroPhoto.hidden=true;};
  heroPhoto.addEventListener('error',hideMissing);
  if(heroPhoto.complete && heroPhoto.naturalWidth===0)hideMissing();
 }
 const $=id=>document.getElementById(id);
 const euro=c=>Number.isFinite(Number(c))?(Number(c)/100).toLocaleString('it-IT',{style:'currency',currency:'EUR'}):'—';
 const txt=(id,val)=>{const el=$(id);if(el)el.textContent=val};
 const today=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Rome',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const plusDays=(day,n)=>{const d=new Date(day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10)};
 const humanDate=iso=>{if(!/^\d{4}-\d\d-\d\d$/.test(iso||''))return iso||'';const [y,m,d]=iso.split('-');return `${d}/${m}/${y}`};
 function empty(id,msg){const block=$(id);block.replaceChildren();const p=document.createElement('p');p.className='zh-feed-empty';p.textContent=msg;block.append(p)}
 function feed(id,records,emptyMsg){const block=$(id);block.replaceChildren();if(!records.length){empty(id,emptyMsg);return}
  for(const r of records){const row=document.createElement('div');row.className='zh-feed-row';const tag=document.createElement('span');tag.className='zh-feed-tag '+(r.kind||'');tag.textContent=r.symbol||'◷';tag.setAttribute('aria-hidden','true');const wrapper=document.createElement('div');wrapper.className='zh-feed-text';const a=document.createElement('a');a.href=r.href;a.textContent=r.name;const detail=document.createElement('small');detail.textContent=r.detail;wrapper.append(a,detail);row.append(tag,wrapper);block.append(row)}
 }
 const req=async path=>{const r=await fetch(path,{credentials:'same-origin',cache:'no-store'});if(r.status===401){location.replace('/login?next=/');throw Error('Accesso richiesto')}if(!r.ok)throw Error(`HTTP ${r.status}`);return r.json()};
 async function load(){
 const all=await Promise.allSettled([req('/api/state'),req('/api/finance'),req('/api/debts'),req('/api/vault')]);
 const [casa,fin,loans,vault]=all.map(x=>x.status==='fulfilled'?x.value:null);
 const errors=[];['Casa','Finance','Lending','Vault'].forEach((name,i)=>{if(all[i].status==='rejected')errors.push(name)});
 if(casa){const bills=Array.isArray(casa.archive?.bills)?casa.archive.bills:[];txt('zh-bills',bills.length.toLocaleString('it-IT'));txt('zh-casa-caption',bills.length?`${bills.length} bollett${bills.length===1?'a':'e'} presenti nell'archivio. Consulta la panoramica per ripartizioni e importi.`:'Nessuna bolletta ancora registrata. Puoi iniziare da Gestione bollette.')}
 else {txt('zh-bills','—');txt('zh-casa-caption','Dati delle utenze temporaneamente non disponibili.')}
 if(fin){txt('zh-cash',euro(fin.current_cash));txt('zh-income',euro(fin.totals?.income_actual));txt('zh-expense',euro(fin.totals?.expense_actual));txt('zh-finance-caption',`Saldo previsto a fine ${fin.year}: ${euro(fin.totals?.end_cash_forecast)}. La previsione non equivale a liquidità già disponibile.`)}
 else {['zh-cash','zh-income','zh-expense'].forEach(id=>txt(id,'—'));txt('zh-finance-caption','Dati finanziari temporaneamente non disponibili.')}
 if(loans){const items=Array.isArray(loans.loans)?loans.loans:[];txt('zh-debt',euro(loans.total?.total_debt_cents));txt('zh-loans-caption',`${items.length} finanziament${items.length===1?'o':'i'} registrat${items.length===1?'o':'i'} · capitale, interessi e spese residui.`)}
 else {txt('zh-debt','—');txt('zh-loans-caption','Dati dei prestiti temporaneamente non disponibili.')}
 if(vault){txt('zh-docs',String(vault.counts?.documents??0));txt('zh-vault-due',String(vault.counts?.upcoming??0))}
 else {txt('zh-docs','—');txt('zh-vault-due','—')}
 const current=vault?.today||fin?.as_of||today(),limit=plusDays(current,30);
 let finNext=null;
 if(fin&&limit.slice(0,4)!==current.slice(0,4)){try{finNext=await req('/api/finance?year='+limit.slice(0,4))}catch(_e){errors.push('Finance (anno successivo)')}}
 const upcoming=[];
 if(vault){for(const item of vault.deadlines||[]){if(item.status!=='pending'||!item.due_date||item.due_date>limit)continue;upcoming.push({date:item.due_date,name:item.title,detail:`${humanDate(item.due_date)} · ${item.due_date<current?'Scaduta':'ZEUS Vault'}`,href:'/vault#deadlines',kind:'vault',symbol:'▤'})}}
 for(const financial of [fin,finNext]){if(!financial)continue;for(const it of financial.occurrences||[]){if(it.settled||!it.occurrence_date||it.occurrence_date<current||it.occurrence_date>limit)continue;upcoming.push({date:it.occurrence_date,name:it.label,detail:`${humanDate(it.occurrence_date)} · ${it.kind==='income'?'Entrata prevista':'Uscita prevista'} · ${euro(it.amount_cents)}`,href:'/finance#f-schedules',kind:it.kind==='income'?'income':'',symbol:it.kind==='income'?'＋':'−'})}
  for(const it of financial.debt_due_events||[]){const day=it.date||it.occurrence_date;if(!day||day<current||day>limit||it.settled)continue;upcoming.push({date:day,name:'Rata prestito prevista',detail:`${humanDate(day)} · ${euro(it.amount_cents)}`,href:'/finance#f-due',kind:'loan',symbol:'▤'})}}
 upcoming.sort((a,b)=>a.date.localeCompare(b.date));feed('zh-deadlines',upcoming.slice(0,7),errors.includes('Vault')||errors.includes('Finance')?'Alcune scadenze non sono disponibili.':'Nessuna scadenza registrata nei prossimi 30 giorni.');
 if(vault){const events=(vault.events||[]).slice(0,5).map(x=>({name:({folder:'Fascicolo',document:'Documento',deadline:'Scadenza',file:'Allegato'}[x.object_type]||'Elemento Vault')+' · '+(x.action||'aggiornato'),detail:(x.created_at||'').replace('T',' ').slice(0,16)+(x.details?' · '+String(x.details).slice(0,80):''),href:'/vault',kind:'vault',symbol:'▤'}));feed('zh-events',events,'Nessuna attività recente nell’archivio.')}
 else empty('zh-events','Attività recenti non disponibili.');
 txt('zh-status',errors.length?'Alcuni riepiloghi non sono disponibili: '+errors.join(', ')+'. Puoi aprire i moduli per verificare.':'Riepiloghi aggiornati dai dati effettivi dei moduli ZEUS.');
 }
 load().catch(()=>txt('zh-status','Impossibile aggiornare i riepiloghi. Ricarica la pagina o controlla la connessione.'));
})();
