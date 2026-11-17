'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>Number(v).toLocaleString('it-IT',{style:'currency',currency:'EUR'});
const number=v=>Number(v).toLocaleString('it-IT',{maximumFractionDigits:3});
const units={electricity:'kWh',gas:'m³',water:'m³'};
let app=null,reading=null;
const valid=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
function metric(b,prefix){const a=b[prefix+'Start'],z=b[prefix+'End'];return valid(a)&&valid(z)&&Number(z)>=Number(a)?Number(z)-Number(a):null}
function dataline(b){const general=metric(b,'general'),sub=metric(b,'sub'),sub2=b.meterMode==='two_subs'?metric(b,'sub2'):null;return {general,sub,sub2,apt2:b.meterMode==='two_subs'?sub2:general!==null&&sub!==null?general-sub:null}}
function provider(b,link){const v=valid(b.providerBilledConsumption)?b.providerBilledConsumption:link&&valid(link.provider_consumption)&&link.provider_consumption_source!=='calcolato_dalle_letture'?link.provider_consumption:null;return valid(v)?Number(v):null}
function pending(u){return (reading?.pending||[]).filter(x=>x.utility===u&&!x.linked_bill_id)}
function activeBills(u){return (app?.archive?.bills||[]).filter(b=>b.utility===u).sort((a,b)=>b.end.localeCompare(a.end))}
function analyse(){const u=$('utility-filter').value,bs=activeBills(u),links=Object.fromEntries((reading?.links||[]).map(l=>[l.bill_id,l])),flags=(reading?.reading_review_flags||[]).filter(f=>bs.some(b=>String(b.id)===String(f.bill_id))),p=pending(u);let sum=bs.reduce((a,b)=>a+(Number(b.total)||0),0);
$('analysis-stats').innerHTML=`<div class="card"><small>Bollette contabilizzate</small><strong>${bs.length}</strong><span class="sub">Totale ${money(sum)}</span></div><div class="card"><small>Documenti in attesa</small><strong>${p.length}</strong><span class="sub"><a href="/bollette">Apri Gestione bollette</a></span></div><div class="card"><small>Bollette da verificare dopo rettifiche</small><strong>${new Set(flags.map(f=>f.bill_id)).size}</strong><span class="sub">Gli importi contabilizzati non vengono modificati automaticamente</span></div>`;
const months=new Map;for(const b of bs){const k=String(b.end||'').slice(0,7);if(!/^\d{4}-\d{2}$/.test(k))continue;const r=months.get(k)||{cost:0,one:0,two:0};r.cost+=Number(b.total)||0;const d=dataline(b);if(d.sub!==null)r.one+=d.sub;if(d.apt2!==null)r.two+=d.apt2;months.set(k,r)}
const entries=[...months].sort(([a],[b])=>b.localeCompare(a));const peak=Math.max(...entries.map(x=>x[1].cost),1);
$('monthly-chart').innerHTML=entries.length?entries.map(([k,v])=>`<div class="analysis-month"><div class="heading-row"><strong>${esc(k)}</strong><strong>${money(v.cost)}</strong></div><div class="analysis-bar" role="img" aria-label="Costo ${money(v.cost)}"><div style="width:${Math.max(0,100*v.cost/peak)}%"></div></div><div class="breakdown"><span>Quote da rendiconto: appartamento 1 ${number(v.one)} ${units[u]}</span><span>Appartamento 2 ${number(v.two)} ${units[u]}</span></div></div>`).join(''):'<div class="analysis-empty">Nessuna bolletta contabilizzata per questa utenza. Le fatture in attesa non entrano ancora in questo grafico. <a href="/bollette">Gestisci le bollette</a>.</div>';
$('comparison-list').innerHTML=bs.length?bs.map(b=>{const x=dataline(b),l=links[String(b.id)],p=provider(b,l),marked=flags.some(f=>String(f.bill_id)===String(b.id));const snap=l?.snapshot;
const arr=snap?.main&&snap?.sub&&(!snap?.sub2||b.meterMode==='two_subs'&&snap.sub2);
const datesAligned=!!(arr&&[snap.main,snap.sub,...(snap.sub2?[snap.sub2]:[])].every(m=>m?.start_date===b.start&&m?.end_date===b.end));
const unit=units[u];let message=marked?'Da verificare: lettura annullata':!l?'Confronto non registrato':!datesAligned?'Date da confrontare':'Periodi letture allineati';
const left=x.sub===null?'—':number(x.sub)+' '+unit,right=x.apt2===null?'—':number(x.apt2)+' '+unit;
const billed=p===null?'Non disponibile':number(p)+' '+(b.providerConsumptionUnit||l?.provider_unit||unit);
return `<div class="analysis-comparison"><div><strong>${esc(b.invoiceNumber||'Fattura')}</strong><small>${esc(b.start)} — ${esc(b.end)}</small><small>${money(b.total||0)}</small></div><div class="metrics"><div><small>Consumo fatturato</small><b>${esc(billed)}</b></div><div><small>Appartamento 1 (rendiconto)</small><b>${esc(left)}</b></div><div><small>Appartamento 2 (rendiconto)</small><b>${esc(right)}</b></div></div><span class="analysis-pill ${marked?'danger':!datesAligned?'warn':''}">${esc(message)}</span></div>`}).join(''):'<div class="analysis-empty">Nessun confronto: non sono state contabilizzate bollette per questa utenza.</div>';
const voids=(reading?.voided_readings||[]).filter(x=>x.utility===u);$('analysis-review').innerHTML=voids.length?voids.map(x=>`<div class="analysis-reviewitem"><b>Lettura annullata ${esc(x.reading_date)} · ${esc(x.meter)}</b> — valore ${number(x.value)} ${units[u]}<small>Motivo: ${esc(x.reason)} · Bollette potenzialmente interessate: ${x.affected_bills.length?x.affected_bills.map(esc).join(', '):'nessuna individuata'}</small></div>`).join(''):'<div class="analysis-empty">Nessuna lettura annullata per questa utenza.</div>';
}
async function load(){try{const [a,r]=await Promise.all([fetch('/api/state',{cache:'no-store'}),fetch('/api/readings',{cache:'no-store'})]);if(!a.ok||!r.ok)throw Error('Impossibile caricare i dati, verifica la sessione.');app=await a.json();reading=await r.json();analyse();$('message').hidden=true}catch(e){$('message').hidden=false;$('message').textContent=e.message}}
$('utility-filter').addEventListener('change',analyse);$('refresh').addEventListener('click',load);load();
