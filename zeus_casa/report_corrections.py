"""Audited source identification and immutable corrections to issued reports.

Reclassification never creates a receipt. Corrections void a bill allocation for
current balances but keep its original row and the issued PDF snapshot intact.
"""
from __future__ import annotations
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import json
import re
import uuid


def initialize(db):
    columns = {r[1] for r in db.execute('PRAGMA table_info(receipt_allocations)')}
    if 'voided_at' not in columns:
        db.execute('ALTER TABLE receipt_allocations ADD COLUMN voided_at TEXT')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS generic_source_assignments (
        id TEXT PRIMARY KEY, source_kind TEXT NOT NULL CHECK(source_kind IN ('receipt','credit')),
        source_id TEXT NOT NULL, apartment INTEGER NOT NULL, tenant_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(amount_cents>0), reason TEXT NOT NULL,
        assigned_at TEXT NOT NULL, actor TEXT NOT NULL,
        UNIQUE(source_kind,source_id)
      );
      CREATE TABLE IF NOT EXISTS issued_report_corrections (
        id TEXT PRIMARY KEY, report_id TEXT NOT NULL,
        serial INTEGER NOT NULL CHECK(serial>0), allocation_id TEXT NOT NULL UNIQUE,
        source_kind TEXT NOT NULL, source_id TEXT NOT NULL,
        bill_id TEXT NOT NULL, apartment INTEGER NOT NULL, tenant_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
        reason TEXT NOT NULL, created_at TEXT NOT NULL, actor TEXT NOT NULL,
        remaining_before_cents INTEGER NOT NULL, remaining_after_cents INTEGER NOT NULL,
        UNIQUE(report_id,serial)
      );
      CREATE INDEX IF NOT EXISTS idx_report_corrections_report ON issued_report_corrections(report_id,serial);
    ''')


def _reason(body):
    if not isinstance(body,dict):raise ValueError('Richiesta non valida')
    reason=body.get('reason')
    if not isinstance(reason,str) or not 12<=len(reason.strip())<=500:
        raise ValueError('Indicare una motivazione documentata (12-500 caratteri)')
    if any(ord(c)<32 for c in reason):raise ValueError('Motivazione non valida')
    return reason.strip()


def _kind(body):
    kind=body.get('source_kind') if isinstance(body,dict) else None
    source_id=body.get('source_id') if isinstance(body,dict) else None
    if kind not in ('receipt','credit') or not isinstance(source_id,str) or not 1<=len(source_id)<=120:
        raise ValueError('Movimento non valido')
    return kind,source_id


def generic_sources(db):
    """Show only receipts and negative credits with no tenant identity."""
    rows=[]
    for uid,apt,amount,when in db.execute("SELECT id,apartment,received_cents,date FROM receipts WHERE tenant_id='' AND received_cents>0"):
        rows.append(dict(source_kind='receipt',source_id=uid,apartment=apt,amount_cents=amount,date=when))
    for uid,apt,amount,when in db.execute("SELECT id,apartment,-signed_cents,date FROM adjustments WHERE tenant_id='' AND signed_cents<0"):
        rows.append(dict(source_kind='credit',source_id=uid,apartment=apt,amount_cents=amount,date=when))
    return sorted(rows,key=lambda x:(x['date'],x['source_kind'],x['source_id']))


def assign_generic(db,body,actor):
    """Move one entire unidentified movement to one explicitly selected tenant.

    A source covering multiple tenants is deliberately rejected as a use case:
    it requires a future audited split, never an estimated attribution.
    Caller must own a BEGIN IMMEDIATE transaction.
    """
    kind,source_id=_kind(body);reason=_reason(body)
    tid=body.get('tenant_id')
    if not isinstance(tid,str) or not tid or len(tid)>120:
        raise ValueError('Selezionare il conduttore destinatario')
    table,field=('receipts','received_cents') if kind=='receipt' else ('adjustments','signed_cents')
    source=db.execute(f'SELECT apartment,tenant_id,{field} FROM {table} WHERE id=?',(source_id,)).fetchone()
    if source is None:raise ValueError('Movimento inesistente')
    apt,prior,amount=source
    if prior!='':raise ValueError('Movimento già associato a un conduttore')
    if kind=='receipt' and amount<=0 or kind=='credit' and amount>=0:
        raise ValueError('Il movimento non è un incasso o un credito utilizzabile')
    archive=json.loads(db.execute('SELECT archive FROM state WHERE id=1').fetchone()[0])
    if not any(t.get('id')==tid and t.get('apartment')==apt for t in archive.get('tenants',[])):
        raise ValueError('Conduttore non presente nell’appartamento del movimento')
    if db.execute('SELECT 1 FROM receipt_allocations WHERE source_kind=? AND source_id=?',(kind,source_id)).fetchone():
        raise ValueError('Movimento già collegato a quote: identificazione non modificabile')
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    uid=uuid.uuid4().hex
    db.execute('INSERT INTO generic_source_assignments VALUES(?,?,?,?,?,?,?,?,?)',
              (uid,kind,source_id,apt,tid,abs(amount),reason,now,str(actor)[:100]))
    db.execute(f'UPDATE {table} SET tenant_id=? WHERE id=? AND tenant_id=?',(tid,source_id,''))
    return dict(ok=True,id=uid,source_id=source_id,source_kind=kind,tenant_id=tid,amount_cents=abs(amount))


def assignments(db):
    cols=('id','source_kind','source_id','apartment','tenant_id','amount_cents','reason','assigned_at','actor')
    return [dict(zip(cols,r)) for r in db.execute('SELECT '+','.join(cols)+' FROM generic_source_assignments ORDER BY assigned_at DESC,id')]


def correct_issued(db,body,actor):
    """Append a dated correction; preserve original allocation and report PDF."""
    import tenant_reports
    reason=_reason(body)
    uid=body.get('allocation_id')
    if not isinstance(uid,str) or not re.fullmatch('[0-9a-f]{32}',uid):
        raise ValueError('Attribuzione non valida')
    row=db.execute('SELECT id,source_kind,source_id,bill_id,apartment,tenant_id,amount_cents,voided_at '
                   'FROM receipt_allocations WHERE id=?',(uid,)).fetchone()
    if row is None:raise ValueError('Attribuzione inesistente')
    _,kind,source_id,bid,apt,tid,amount,voided=row
    if voided:raise ValueError('Attribuzione già rettificata')
    match=db.execute('SELECT r.id FROM tenant_reports r JOIN tenant_report_bills rb ON rb.report_id=r.id '
                     'WHERE rb.bill_id=? AND rb.apartment=? AND r.tenant_id=?',(bid,apt,tid)).fetchone()
    if not match:raise ValueError('Attribuzione non collegata a un rendiconto emesso')
    rid=match[0]
    reports={x['id']:x for x in tenant_reports.list_reports(db)}
    before=reports[rid]['remaining_cents']
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    update=db.execute('UPDATE receipt_allocations SET voided_at=? WHERE id=? AND voided_at IS NULL',(now,uid))
    if update.rowcount!=1:raise ValueError('Attribuzione già rettificata')
    after={x['id']:x for x in tenant_reports.list_reports(db)}[rid]['remaining_cents']
    serial=db.execute('SELECT COALESCE(MAX(serial),0)+1 FROM issued_report_corrections WHERE report_id=?',(rid,)).fetchone()[0]
    correction_id=uuid.uuid4().hex
    db.execute('INSERT INTO issued_report_corrections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (correction_id,rid,serial,uid,kind,source_id,bid,apt,tid,amount,reason,now,str(actor)[:100],before,after))
    return dict(ok=True,id=correction_id,report_id=rid,serial=serial,allocation_id=uid,
                amount_cents=amount,remaining_before_cents=before,remaining_after_cents=after)


def corrections(db):
    fields=('id','report_id','serial','allocation_id','source_kind','source_id','bill_id','apartment',
            'tenant_id','amount_cents','reason','created_at','actor','remaining_before_cents','remaining_after_cents')
    return [dict(zip(fields,r)) for r in db.execute('SELECT '+','.join(fields)+' FROM issued_report_corrections ORDER BY created_at DESC,id')]


def correction_pdf(db,ident):
    """One-page correction note, a *new* document, never a rewritten statement."""
    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):
        raise ValueError('Rettifica non valida')
    entry=next((v for v in corrections(db) if v['id']==ident),None)
    if entry is None:raise ValueError('Rettifica inesistente')
    row=db.execute('SELECT year,serial,issued_on,apartment,snapshot FROM tenant_reports WHERE id=?',(entry['report_id'],)).fetchone()
    if row is None:raise ValueError('Rendiconto originale inesistente')
    from PIL import Image,ImageDraw,ImageFont
    fontdirs=[Path('/usr/share/fonts/dejavu'),Path('/usr/share/fonts/ttf/dejavu'),
              Path('/usr/share/fonts/truetype/dejavu'),Path('/usr/share/fonts/truetype/dejavu')]
    fontdirs += [Path('/usr/share/fonts/truetype/dejavu')]
    def font(size,bold=False):
        name='DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
        for d in fontdirs:
            if (d/name).is_file():return ImageFont.truetype(str(d/name),size)
        return ImageFont.load_default(size=size)
    def euros(v):return f'{v/100:,.2f}'.replace(',','X').replace('.',',').replace('X','.')+' EUR'
    w,h=1240,1754;im=Image.new('RGB',(w,h),'white');d=ImageDraw.Draw(im)
    def write(txt,x,y,sz=23,bold=False):d.text((x,y),str(txt),font=font(sz,bold),fill=(28,52,43))
    write('ZEUS CASA',80,84,50,True)
    write('NOTA DI RETTIFICA DEL RENDICONTO',80,195,33,True)
    write('Nota n. '+str(entry['serial'])+' — Riferimento: '+str(row[1])+'/'+str(row[0]),80,261,25)
    write('Registrata il: '+entry['created_at'].replace('T',' ')[:19]+' UTC',80,310,20)
    write('Rendiconto originale emesso il: '+row[2],80,355,20)
    write('Appartamento: '+str(entry['apartment']+1)+'  Conduttore ID: '+entry['tenant_id'][:80],80,418,20)
    write('Attribuzione rettificata (identificativo): '+entry['allocation_id'],80,472,19)
    write('Bolletta ID: '+entry['bill_id'][:95],80,515,19)
    write('Importo attribuzione annullata: '+euros(entry['amount_cents']),80,590,26,True)
    write('Residuo visualizzato prima: '+euros(entry['remaining_before_cents']),80,648,22)
    write('Residuo visualizzato dopo: '+euros(entry['remaining_after_cents']),80,696,22)
    write('Motivazione della rettifica:',80,780,24,True)
    words=entry['reason'].split();line='';y=824
    for word in words:
        candidate=(line+' '+word).strip()
        if line and d.textbbox((0,0),candidate,font=font(20))[2]>1060:
            write(line,80,y,20);y+=36;line=word
        else:line=candidate
    if line:write(line,80,y,20);y+=36
    write('La presente nota NON sostituisce il rendiconto originale.',80,max(y+70,1110),20,True)
    write('Non registra nuovi incassi né emette una richiesta automatica di pagamento.',80,max(y+115,1157),19)
    output=BytesIO();im.save(output,format='PDF',resolution=150);return output.getvalue()
