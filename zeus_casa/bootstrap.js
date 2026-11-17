"use strict";
(async () => {
  const root = document.getElementById('root');
  try {
    const response = await fetch('/api/state', {cache:'no-store'});
    if (response.status===401){location.replace('/login');return;}if (!response.ok) throw new Error('Risposta del server: ' + response.status);
    const initial = await response.json();
    if (!Number.isInteger(initial.revision) || initial.revision < 0 || !initial.archive) throw new Error('Archivio del server non valido');
    // The tenant report's due date is a read-only effective date for arrears.
    // Keep it separate from archive.bills[*].due1/due2 to avoid changing bills.
    const reportsResponse = await fetch('/api/reports', {cache:'no-store'});
    if (!reportsResponse.ok) throw new Error('Scadenze rendiconti non disponibili (HTTP ' + reportsResponse.status + ')');
    const reportsData = await reportsResponse.json();
    if (!Array.isArray(reportsData.issued_bills)) throw new Error('Archivio scadenze rendiconti non valido');
    const reportDue = Object.create(null);
    for (const item of reportsData.issued_bills) {
      if (typeof item.bill_id !== 'string' || ![0,1].includes(item.apartment) ||
          !/^\d{4}-\d{2}-\d{2}$/.test(item.due_on || '')) {
        throw new Error('Scadenza non valida per una quota rendicontata');
      }
      const key = item.bill_id + ':' + item.apartment;
      if (reportDue[key]) throw new Error('Più rendiconti per la stessa quota: verificare archivio');
      reportDue[key] = Object.freeze({due_on:item.due_on, label:item.report_label || ''});
    }
    window.__zeusIssuedReportDue = Object.freeze(reportDue);
    const key = 'casa-consumi-local-v1';
    const memory = new Map();
    if(initial.revision >= 1) memory.set(key, JSON.stringify(initial));
    window.__casaSharedStorage = Object.freeze({
      getItem: k => memory.has(k) ? memory.get(k) : null,
      setItem: (k,v) => { memory.set(String(k),String(v)); },
      removeItem: k => { memory.delete(k); },
    });
    const script = document.createElement('script');
    script.src = '/app.js?v=1.7.17-rc5';
    script.onerror = () => {root.textContent = 'Impossibile caricare l’applicazione. Aggiorna la pagina.'};
    document.body.appendChild(script);
  } catch (err) {
    root.innerHTML = '';
    const box = document.createElement('div'); box.style.cssText='max-width:480px;margin:12vh auto;padding:32px;background:white;border-radius:10px;font-family:Arial,sans-serif';
    const title = document.createElement('h2');title.textContent='Archivio non raggiungibile';
    const text = document.createElement('p');text.textContent=err.message+'. Verifica che l’app ZEUS Casa sia avviata e aggiorna la pagina.';
    text.style.cssText='margin:20px 0;line-height:1.8';
    const btn = document.createElement('button');btn.textContent='Ricarica';btn.onclick=()=>location.reload();
    btn.style.cssText='padding:10px 18px;cursor:pointer';
    box.append(title,text,btn);root.append(box);
  }
})();
