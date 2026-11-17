"use strict";
(function(){
  // The existing React bill form predates PDF storage. Offer a direct archive link
  // without touching its internal data/submit handler.
  let seen=new WeakSet();
  function update(){
    for(const form of document.querySelectorAll('form')){
      if(seen.has(form)||!form.textContent.includes('Salva bolletta'))continue;
      seen.add(form);
      const note=document.createElement('aside');note.className='zeus-bill-docnote';
      note.style.cssText='border:1px solid #dec9a4;background:#fff8e9;color:#755d31;padding:11px 14px;border-radius:6px;margin:12px 0;font-size:12px;line-height:1.6';
      note.innerHTML='Documento PDF facoltativo. Se non alleghi la bolletta, non sarà disponibile il documento giustificativo dei consumi e degli importi. <b>Salva la bolletta</b>, poi usa <a href="/documenti" style="text-decoration:underline;color:#28594a" target="_blank" rel="noopener">Allega PDF</a> (anche in seguito). Per l’elettricità con due sottocontatori usa <a href="/percorso" style="text-decoration:underline;color:#28594a">Percorso bollette</a>.';
      form.insertBefore(note,form.querySelector('.modal-footer')||form.lastElementChild);
    }
  }
  const observer=new MutationObserver(update);observer.observe(document.getElementById('root'),{childList:true,subtree:true});
  update();
})();
