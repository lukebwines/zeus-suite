'use strict';
(()=>{
const form=document.getElementById('bill-form'), meterArea=document.getElementById('meters');
const $=key=>form.elements.namedItem(key);
const fmt=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const unit={electricity:'kWh',gas:'Smc',water:'m³'};
let records=[],setupDate='',saving=false,savedId='',editId=new URLSearchParams(location.search).get('edit')||'';
const overridden={provider_start_date:false,provider_end_date:false};
function syncDate(source,target){if(!overridden[target])$(target).value=$(source).value;}
for(const [src,dst] of [['period_start','provider_start_date'],['period_end','provider_end_date']]){
  $(src).addEventListener('change',()=>syncDate(src,dst));
  $(dst).addEventListener('input',()=>{overridden[dst]=true});
}

const msg=(text,error=false)=>{const el=document.getElementById('message');el.textContent=text;el.hidden=false;el.className='message'+(error?' error':'');window.scrollTo({top:0,behavior:'smooth'})};
const num=name=>{const v=$(name).value;return v!==''?Number(v):null};
function supplierPreview(){
 const a=num('provider_start_value'), b=num('provider_end_value'), c=num('provider_consumption');
 const el=document.getElementById('supplier-check'),u=$('utility').value;
 document.getElementById('zeus-gas-conversion').hidden=(u!=='gas');
 const f=n=>n.toLocaleString('it-IT',{maximumFractionDigits:4});
 const hasPair=a!==null&&b!==null;
 el.classList.remove('supplier-problem','supplier-ok');
 if ((a===null)!==(b===null)){
   el.textContent='Completa entrambe le letture del fornitore oppure cancellale. In alternativa inserisci il consumo fatturato.';
   el.classList.add('supplier-problem');return;
 }
 if(hasPair&&b<a){el.textContent='La lettura finale è inferiore a quella iniziale: verifica sostituzioni o azzeramenti del contatore.';el.classList.add('supplier-problem');return;}
 if(!hasPair&&c===null){el.textContent='Per registrare la fattura inserisci il consumo fatturato oppure entrambe le letture rilevate dal fornitore.';el.classList.add('supplier-problem');return;}
 if(hasPair&&c===null){
   const delta=b-a;
   el.textContent=u==='gas' ? `Differenza calcolata: ${f(delta)} m³. ZEUS salverà il valore come m³ ricavati dalle letture, NON come Smc fatturati. Se presenti, inserisci gli Smc riportati in bolletta.` :
        `Consumo calcolato automaticamente dalle letture: ${f(delta)} ${unit[u]}. ZEUS lo registrerà come valore calcolato, senza inventare il consumo dichiarato in fattura.`;
   el.classList.add('supplier-ok');return;
 }
 if(!hasPair){el.textContent=`Consumo fatturato: ${f(c)} ${unit[u]}. Le letture del fornitore rimangono sconosciute e non vengono ricostruite artificialmente.`;el.classList.add('supplier-ok');return;}
 const delta=b-a;
 if(u==='gas'){
   el.textContent=`Consumo fatturato: ${f(c)} Smc; differenza tra letture: ${f(delta)} m³. Sono unità diverse: nessuna equivalenza automatica.`;
 } else if(Math.abs(delta-c)<=0.00005){
   el.textContent=`Verifica superata: consumo fatturato e differenza delle letture coincidono (${f(c)} ${unit[u]}).`;
   el.classList.add('supplier-ok');
 } else {
   el.textContent=`Da verificare: letture = ${f(delta)} ${unit[u]}; consumo fatturato = ${f(c)} ${unit[u]}. Entrambi i dati verranno conservati, senza correzioni automatiche.`;
   el.classList.add('supplier-problem');
 }
}

function pairs(arr){return arr.filter(r=>r.utility===$('utility').value && r.meter==='main')}
function renderMeters(){
 const u=$('utility').value,end=$('period_end').value,start=$('period_start').value;
 const required=[['main','Contatore generale (registro separato)'],['sub','Sottocontatore appartamento 1']];
 if(u==='electricity'&&setupDate&&end>=setupDate)required.push(['sub2','Sottocontatore appartamento 2']);
 let warnings=0;
 meterArea.innerHTML=required.map(([m,title])=>{
   const match=records.filter(r=>r.utility===u&&r.meter===m&&(!end||r.date<=end)).sort((a,b)=>b.date.localeCompare(a.date));
   const [last,previous]=match;if(!last||!previous){warnings++;return `<div class="meter-record invalid"><strong>${fmt(title)}</strong><small>Servono almeno due letture per calcolare il consumo. Puoi registrarle in un secondo momento.</small></div>`}
   const delta=Number(last.value)-Number(previous.value),align=Boolean(start&&end&&previous.date===start&&last.date===end);
   if(!align||delta<0)warnings++;
   return `<div class="meter-record ${align&&delta>=0?'good':'invalid'}"><strong>${fmt(title)}</strong><small>${fmt(previous.date)}: ${fmt(previous.value)} → ${fmt(last.date)}: ${fmt(last.value)} ${u==='electricity'?'kWh':u==='gas'?'m³':'m³'}</small><small>Consumo rilevato: ${fmt(delta)} · ${align?'Date coincidenti con il periodo indicato':'DATE DA VERIFICARE: non coincidono necessariamente con la fattura'}</small></div>`;
 }).join('');
 const state=document.getElementById('state');
 if(u==='electricity'&&setupDate&&start&&end&&start<setupDate&&end>=setupDate){state.textContent='La fattura attraversa l’attivazione del secondo sottocontatore: ripartizione automatica bloccata. È necessaria una verifica manuale.';return}
 state.textContent=warnings ? `Sono presenti ${warnings} situazioni da verificare nel registro. Puoi salvare la fattura, ma nessun addebito verrà creato.` : 'Sono disponibili coppie di letture con date coincidenti. La ripartizione sarà comunque soggetta a conferma nel Percorso Bollette.';
}
async function load(){
 try{const res=await fetch('/api/readings',{cache:'no-store'});if(res.status===401){location.assign('/login');return}const data=await res.json();if(!res.ok)throw Error(data.error||'Letture non disponibili');records=data.readings||[];setupDate=data.electricity_second_sub_from||'';renderMeters()}
 catch(err){meterArea.textContent='Impossibile leggere il registro: '+err.message;document.getElementById('state').textContent='Il salvataggio della fattura resta possibile senza letture, ma non è possibile calcolare la ripartizione.'}
}
for(const field of ['provider_start_value','provider_end_value','provider_consumption'])$(field).addEventListener('input',supplierPreview);
for(const name of ['utility','period_start','period_end'])$(name).addEventListener('change',()=>{document.getElementById('bill-unit').textContent=unit[$('utility').value];supplierPreview();renderMeters()});
$('pdf').addEventListener('change',()=>{const file=$('pdf').files[0];const warning=document.getElementById('pdf-warning');warning.classList.toggle('hidden',Boolean(file));if(file&&(file.size>15*1024*1024||!file.name.toLowerCase().endsWith('.pdf')))msg('È ammesso soltanto un PDF di massimo 15 MB.',true)});
const initial=new URLSearchParams(location.search).get('utility');if(Object.keys(unit).includes(initial))$('utility').value=initial;
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Casa-Consumi':'1'},body:JSON.stringify(body)});const o=await r.json();if(!r.ok)throw Error(o.error||'Errore di salvataggio');return o}
async function upload(id,file){const q=new URLSearchParams({id,filename:file.name});const r=await fetch('/api/readings/pending_pdf?'+q,{method:'POST',headers:{'X-Casa-Consumi':'1','Content-Type':'application/pdf'},body:file});const o=await r.json();if(!r.ok)throw Error(o.error||'Caricamento del PDF non riuscito');return o}
form.addEventListener('submit',async e=>{
 e.preventDefault();if(saving||savedId)return;
 if(!form.reportValidity())return;
 if($('period_start').value>=$('period_end').value){msg('La data finale del periodo deve essere successiva a quella iniziale.',true);return}
 const a=num('provider_start_value'),b=num('provider_end_value'),c=num('provider_consumption');
 if((a===null)!==(b===null)){msg('Inserisci entrambe le letture rilevate dal fornitore oppure lasciale vuote.',true);return}
 if(c===null&&a===null){msg('È obbligatorio inserire il consumo fatturato oppure entrambe le letture del fornitore.',true);return}
 if(a!==null&&b<a){msg('Lettura finale fatturata inferiore alla precedente: verifica il documento.',true);return}
 const tot=num('total'),fixed=num('fixed');if(tot===null||fixed===null||fixed>tot){msg('La quota fissa non può superare il totale della fattura.',true);return}
 const file=$('pdf').files[0];if(file&&(file.size>15*1024*1024||!file.name.toLowerCase().endsWith('.pdf'))){msg('PDF non valido o superiore a 15 MB.',true);return}
 const body={};for(const name of ['utility','invoice_type','invoice_number','invoice_date','period_start','period_end','provider_start_value','provider_end_value','provider_start_date','provider_end_date','provider_consumption','conversion_c','total','fixed','note'])body[name]=$(name).value;
 saving=true;document.getElementById('save').disabled=true;
 try{
   if(editId)body.id=editId;
   const result=await api(editId?'/api/readings/pending/update':'/api/readings/pending/add',body);savedId=result.id;
   let uploaded=false,uploadError='';if(file){try{await upload(savedId,file);uploaded=true}catch(err){uploadError=err.message}}
   form.querySelectorAll('input,select,textarea').forEach(el=>{el.disabled=true});
   document.getElementById('save').hidden=true;
   const actions=form.querySelector('.actions');const link=document.createElement('a');link.href='/bollette?focus='+encodeURIComponent(savedId);link.className='btn';link.textContent='Gestisci fattura e ripartizione';actions.appendChild(link);
   const text=(editId?'Fattura aggiornata IN ATTESA.':'Fattura salvata IN ATTESA.')+' Nessun addebito creato.'+
      (uploaded?' PDF giustificativo archiviato.':file?' ATTENZIONE: PDF non caricato ('+uploadError+'). Puoi riprovare in Percorso Bollette.':' Bolletta non allegata: il documento giustificativo non sarà disponibile finché non lo aggiungerai.');
   msg(text,Boolean(uploadError));
 }catch(err){msg(err.message,true);saving=false;document.getElementById('save').disabled=false}
});
async function initializeEditing(){
 if(!editId)return;
 try{const r=await fetch('/api/readings',{cache:'no-store'});if(!r.ok)throw Error('Impossibile caricare la fattura');
  const data=await r.json(),b=(data.pending||[]).find(p=>p.id===editId&&!p.linked_bill_id);
  if(!b)throw Error('Fattura non trovata o già contabilizzata');
  for(const k of ['utility','invoice_type','invoice_number','invoice_date','period_start','period_end','provider_start_value','provider_end_value','provider_start_date','provider_end_date','provider_consumption','conversion_c','note']){if($(k))$(k).value=k==='provider_consumption'&&b.provider_consumption_source==='calcolato_dalle_letture'?'':(b[k]||'')}
  $('total').value=(b.total_cents/100).toFixed(2);$('fixed').value=(b.fixed_cents/100).toFixed(2);
  for(const [src,dst] of [['period_start','provider_start_date'],['period_end','provider_end_date']]){overridden[dst]=Boolean(b[dst]&&b[dst]!==b[src]);if(!b[dst])syncDate(src,dst)}
  document.querySelector('.intro h1').textContent='Modifica fattura in attesa';
  document.getElementById('save').textContent='Salva modifiche';
  supplierPreview();renderMeters();
 }catch(e){msg(e.message,true);document.getElementById('save').disabled=true}
}
supplierPreview();load();initializeEditing();
})();
