'use strict';
const form=document.querySelector('#reading-form');
const photoInput=form.elements.namedItem('photo');
const errorBox=document.querySelector('#error');
function fail(text){errorBox.hidden=false;errorBox.textContent=text;}
function clearError(){errorBox.hidden=true;errorBox.textContent='';}
if(photoInput)photoInput.addEventListener('change',()=>{const file=photoInput.files?.[0];const area=document.querySelector('#preview');area.hidden=!file;if(file){const img=document.querySelector('#photo-preview');if(img.dataset.blob)URL.revokeObjectURL(img.dataset.blob);img.dataset.blob=URL.createObjectURL(file);img.src=img.dataset.blob;}});
async function jpegPhoto(file){
 const image=await createImageBitmap(file,{imageOrientation:'from-image'});
 const scale=Math.min(1,1800/Math.max(image.width,image.height));
 const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.width*scale));canvas.height=Math.max(1,Math.round(image.height*scale));
 canvas.getContext('2d',{alpha:false}).drawImage(image,0,0,canvas.width,canvas.height);image.close();
 const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.79));
 if(!blob||blob.size<1024||blob.size>3*1024*1024)throw Error('La fotografia non può essere elaborata (massimo 3 MB).');
 return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('Errore di lettura della foto'));reader.readAsDataURL(blob)});
}
form.addEventListener('submit',async event=>{
 event.preventDefault();clearError();const button=document.querySelector('#send');button.disabled=true;button.textContent='Invio in corso…';
 try{
  const pin=String(form.elements.namedItem('pin').value);if(!/^\d{4}$/.test(pin))throw Error('Inserisci il PIN numerico di quattro cifre.');
  const regGroup=form.querySelector('[data-register-group]');
  let payload;
  if(regGroup){
   const registers={};
   for(const f of ['f1','f2','f3']){
    const raw=String(form.elements.namedItem(f).value).trim();
    if(!/^\d+(?:\.\d{1,4})?$/.test(raw))throw Error('Inserisci un valore valido per '+f.toUpperCase()+'.');
    registers[f]=raw;
   }
   payload={pin,registers};
  }else{
   const value=String(form.elements.namedItem('value').value);
   if(!value||!Number.isFinite(Number(value))||Number(value)<0)throw Error('Inserisci una lettura valida.');
   payload={pin,value};
  }
  if(photoInput){
   const file=photoInput.files?.[0];if(!file)throw Error('La fotografia è obbligatoria.');
   payload.photo=await jpegPhoto(file);
  }
  const token=document.querySelector('#token').value;
  const response=await fetch('/submit/'+encodeURIComponent(token),{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const json=await response.json();if(!response.ok)throw Error(json.error||'Invio non riuscito.');
  const card=document.querySelector('.card');card.replaceChildren();
  const h=document.createElement('h1');h.textContent='Lettura inviata';card.appendChild(h);
  const p=document.createElement('p');p.className='intro';p.textContent='La lettura è stata ricevuta e rimane in attesa di approvazione. Riferimento: '+json.reference;card.appendChild(p);
 }catch(e){fail(e.message||'Impossibile inviare la lettura.');button.disabled=false;button.textContent='Invia lettura';}
});

// Convenience preview only: the server recomputes the authoritative total.
const activeRegisters=form.querySelector('[data-register-group]');
if(activeRegisters){
 const preview=()=>{
   const inputs=['f1','f2','f3'].map(k=>String(form.elements.namedItem(k).value).trim());
   const output=document.querySelector('#register-total');
   if(inputs.some(v=>!/^\d+(?:\.\d{1,4})?$/.test(v))){output.textContent='—';return;}
   const scaled=inputs.map(v=>{const [a,b='']=v.split('.');return Number(a)*10000+Number((b+'0000').slice(0,4));});
   const total=scaled.reduce((a,b)=>a+b,0);
   output.textContent=(total/10000).toLocaleString('it-IT',{maximumFractionDigits:4});
 };
 activeRegisters.addEventListener('input',preview);preview();
}
