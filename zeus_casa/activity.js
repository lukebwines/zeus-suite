'use strict';
(()=>{
 const $=id=>document.getElementById(id);
 const fmt=v=>{try{return new Date(v).toLocaleString('it-IT',{dateStyle:'short',timeStyle:'medium'});}catch{return v||'—'}};
 const clean=v=>String(v??'');
 function filters(){const q=new URLSearchParams();if($('activity-module').value)q.set('module',$('activity-module').value);if($('activity-since').value)q.set('since',$('activity-since').value);return q.toString()}
 async function reload(){
  $('activity-status').textContent='Caricamento…';
  try{
   const response=await fetch('/api/activity?'+filters(),{credentials:'same-origin'});
   if(response.status===401){location.assign('/login?next=%2Factivity');return;}
   const data=await response.json();if(!response.ok)throw new Error(data.error||'Lettura non riuscita');
   const table=$('activity-rows');table.replaceChildren();
   for(const ev of data.events){
    const tr=document.createElement('tr');
    for(const value of [fmt(ev.created_at),ev.actor,ev.module,ev.action,ev.object_type+(ev.object_id?' · '+ev.object_id:''),ev.detail]){
     const td=document.createElement('td');td.textContent=clean(value);tr.appendChild(td);
    }table.appendChild(tr);
   }
   if(!data.events.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=6;td.textContent='Nessuna operazione registrata per i filtri selezionati.';tr.appendChild(td);table.appendChild(tr);}
   $('activity-status').textContent='Operazioni visualizzate: '+data.events.length+' (massimo 300 per pagina).';
  }catch(err){$('activity-status').textContent=err.message;$('activity-status').classList.add('error');}
 }
 $('activity-filter').addEventListener('click',reload);
 $('activity-export').addEventListener('click',()=>{location.href='/api/activity/csv?'+filters()});
 reload();
})();
