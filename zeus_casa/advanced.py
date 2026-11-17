"""Casa Consumi: advanced accounting features and HA notifications. Stdlib only."""
from __future__ import annotations
import json, sqlite3, datetime as dt, decimal, re, uuid, os, hashlib, urllib.request, urllib.error, threading, logging, time
from zoneinfo import ZoneInfo
from pathlib import Path

Money=decimal.Decimal
ALLOWED_UTILITIES={'electricity','gas','water'}
def cents(v):
    try:
        d=Money(str(v).replace(',','.'))
        if not d.is_finite() or d.as_tuple().exponent < -2 or abs(d)>Money('99999999'): raise ValueError()
        return int(d*100)
    except (ValueError, decimal.InvalidOperation, TypeError): raise ValueError('Importo monetario non valido (massimo due decimali)')
def euro(n): return round(n/100,2)
def datecheck(v, optional=True):
    if v is None and optional:return ''
    if v=='' and optional:return ''
    if not isinstance(v,str): raise ValueError('Data non valida')
    try:
        if dt.date.fromisoformat(v).isoformat()!=v: raise ValueError()
    except ValueError: raise ValueError('Data non valida (AAAA-MM-GG)')
    return v
def txt(v,maxlen=200,required=False):
    if not isinstance(v,str) or len(v)>maxlen or (required and not v.strip()): raise ValueError('Testo mancante o troppo lungo')
    return v.strip()
def apt(v):
    if type(v)!=int or v not in (0,1): raise ValueError('Appartamento non valido')
    return v
def receipt_id():return uuid.uuid4().hex

def calc(b):
    """Consistent cent-rounded bill calculation, submeter=flat 1."""
    total=cents(b['total']); fixed=cents(b['fixed'])
    if total<0 or fixed<0 or fixed>total: raise ValueError('Importi bolletta non validi')
    consumed=float(b['generalEnd'])-float(b['generalStart'])
    sub=float(b['subEnd'])-float(b['subStart'])
    two=b.get('meterMode')=='two_subs'
    sub2=float(b['sub2End'])-float(b['sub2Start']) if two else max(0,consumed-sub)
    if consumed < -1e-6 or sub < -1e-6 or sub2 < -1e-6 or not two and sub>consumed+1e-6:
        raise ValueError('Letture contatori non coerenti')
    if abs(consumed)<1e-8 and total>fixed: raise ValueError('Senza consumo, la quota variabile deve essere zero')
    fshare=b.get('fixedShare1',50)
    vshare=b.get('variableShare1',None)
    if type(fshare) not in (float,int) or fshare<0 or fshare>100: raise ValueError('Quota fissa personalizzata non valida')
    if vshare is not None and (type(vshare) not in (float,int) or vshare<0 or vshare>100): raise ValueError('Quota variabile personalizzata non valida')
    fc=int((Money(fixed)*Money(str(fshare))/100).quantize(Money('1'),rounding=decimal.ROUND_HALF_UP))
    fraction=(Money(str(vshare))/100 if vshare is not None else (Money(str(sub))/Money(str(sub+sub2)) if two and sub+sub2>0 else Money(str(sub))/Money(str(consumed)) if consumed>0 else Money(0)))
    vc=int((Money(total-fixed)*fraction).quantize(Money('1'),rounding=decimal.ROUND_HALF_UP))
    return {'q1':fc+vc,'q2':total-fc-vc,'f1':fc,'f2':fixed-fc,'v1':vc,'v2':total-fixed-vc,'c1':sub,'c2':sub2,'total':total}

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS invoices (
      id TEXT PRIMARY KEY, bill_id TEXT NOT NULL, filename TEXT NOT NULL,
      size INTEGER NOT NULL, sha256 TEXT NOT NULL, data BLOB NOT NULL,
      added_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS adjustments (
      id TEXT PRIMARY KEY, apartment INTEGER NOT NULL CHECK(apartment in (0,1)),
      tenant_id TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL,
      signed_cents INTEGER NOT NULL, date TEXT NOT NULL, due TEXT NOT NULL DEFAULT '',
      bill_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL, created_at TEXT NOT NULL,
      original_cents INTEGER, final_cents INTEGER);
    CREATE TABLE IF NOT EXISTS receipts (
      id TEXT PRIMARY KEY, apartment INTEGER NOT NULL CHECK(apartment in (0,1)),
      tenant_id TEXT NOT NULL DEFAULT '', received_cents INTEGER NOT NULL,
      date TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS closures (
      bill_id TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL, tenant_snapshot_json TEXT NOT NULL,
      closed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL, context TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS notifications_sent (
      date TEXT NOT NULL, apartment INTEGER NOT NULL, PRIMARY KEY(date,apartment));
    ''')
    cols={r[1] for r in db.execute('PRAGMA table_info(adjustments)')}
    if 'original_cents' not in cols: db.execute('ALTER TABLE adjustments ADD COLUMN original_cents INTEGER')
    if 'final_cents' not in cols: db.execute('ALTER TABLE adjustments ADD COLUMN final_cents INTEGER')
    # Snapshot names on existing items (idempotent) so changing tenant profile will not rename archived reports.
    row=db.execute('SELECT revision,archive FROM state WHERE id=1').fetchone()
    if row:
        obj=json.loads(row[1]); tenants={t['id']:t for t in obj.get('tenants',[])}
        changed=False
        for b in obj.get('bills',[]):
            for i in (1,2):
                snap='tenant'+str(i)+'Snapshot'
                current=tenants.get(b.get('tenant'+str(i)+'Id',''),{})
                if snap not in b and current:
                    b[snap]={k:current.get(k,'') for k in ('name','address','taxCode')}
                    changed=True
        if changed:
            db.execute('UPDATE state SET revision=?,archive=? WHERE id=1',(row[0]+1,json.dumps(obj,ensure_ascii=False)))

def enrich(old, new):
    oldmap={b['id']:b for b in old.get('bills',[])}
    tenants={t['id']:t for t in new.get('tenants',[])}
    for b in new.get('bills',[]):
        past=oldmap.get(b['id'],{})
        for custom in ('fixedShare1','variableShare1'):
            if custom not in b and custom in past:b[custom]=past[custom]
        for i in (1,2):
            f='tenant'+str(i)+'Id'; sn='tenant'+str(i)+'Snapshot'
            if past.get(f,'')==b.get(f,'') and past.get(sn):
                b[sn]=past[sn]
            else:
                t=tenants.get(b.get(f,''))
                if t: b[sn]={k:t.get(k,'') for k in ('name','address','taxCode')}
                else: b.pop(sn,None)
    return new

def guard_closed(db, old, new):
    oldby={b['id']:b for b in old.get('bills',[])}
    newby={b['id']:b for b in new.get('bills',[])}
    deleted=set(oldby)-set(newby)
    carry_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='consumption_carry'").fetchone() is not None
    reports_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tenant_report_bills'").fetchone() is not None
    for bid in deleted:
        if reports_exists and db.execute('SELECT 1 FROM tenant_report_bills WHERE bill_id=?',(bid,)).fetchone():
            raise PermissionError('Bolletta inclusa in un rendiconto emesso: non può essere eliminata.')
        if carry_exists and db.execute('SELECT 1 FROM consumption_carry WHERE bill_id=?',(bid,)).fetchone():
            raise PermissionError('Bolletta collegata al riporto progressivo dei consumi: non eliminarla senza una riconciliazione controllata delle bollette successive.')
        if db.execute('SELECT 1 FROM invoices WHERE bill_id=?',(bid,)).fetchone() or db.execute('SELECT 1 FROM adjustments WHERE bill_id=?',(bid,)).fetchone() or db.execute('SELECT 1 FROM meter_bill_links WHERE bill_id=?',(bid,)).fetchone() or db.execute('SELECT 1 FROM reading_pending WHERE linked_bill_id=?',(bid,)).fetchone():
            raise PermissionError('Bolletta con fatture o conguagli collegati: elimina i collegamenti prima di cancellarla.')
    for bid in (set(oldby) & set(newby)):
        if reports_exists and db.execute('SELECT 1 FROM tenant_report_bills WHERE bill_id=? LIMIT 1',(bid,)).fetchone():
            oldbill=oldby[bid];newbill=newby[bid]
            allowed_changes={'paid1','paid2','received1','received2','due1','due2','paymentDate1','paymentDate2'}
            for field in ('paymentDate1','paymentDate2'):
                if newbill.get(field)!=oldbill.get(field):
                    datecheck(newbill.get(field), optional=True)
            oldfixed={k:v for k,v in oldbill.items() if k not in allowed_changes}
            newfixed={k:v for k,v in newbill.items() if k not in allowed_changes}
            if oldfixed!=newfixed:
                raise PermissionError('Bolletta compresa in un rendiconto emesso: solo pagamenti e scadenze originarie sono modificabili.')
    newtenants={t['id']:t for t in new.get('tenants',[])}
    for table in ('adjustments','receipts'):
        for tid,a in db.execute(f'SELECT DISTINCT tenant_id,apartment FROM {table} WHERE tenant_id != ?', ('',)):
            if tid not in newtenants:
                # Historic financial records keep the tenant identifier and any
                # original snapshots; the separate audit entry authorizes removal
                # from the working address book only.
                old_tenant=next((t for t in old.get('tenants',[]) if t.get('id')==tid),None)
                deleted=db.execute('SELECT apartment FROM tenant_archive_deletions WHERE tenant_id=?',(tid,)).fetchone()
                if deleted and deleted[0]==a and (old_tenant is None or old_tenant.get('active') is False):
                    continue
                raise PermissionError('Conduttore con movimenti contabili: archivialo prima di rimuovere la scheda.')
            if newtenants[tid]['apartment']!=a:
                raise PermissionError('Non spostare un conduttore con movimenti contabili.')
    # Once a payment is linked to a bill, protect its amount and ownership.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_allocations'").fetchone():
        for bid,apt,allocated in db.execute(
                'SELECT bill_id,apartment,SUM(amount_cents) FROM receipt_allocations WHERE voided_at IS NULL GROUP BY bill_id,apartment'):
            oldbill=oldby.get(bid);newbill=newby.get(bid)
            if not oldbill or not newbill:
                raise PermissionError('Bolletta con incassi riconciliati: non può essere eliminata.')
            tid='tenant'+str(apt+1)+'Id'
            if oldbill.get(tid)!=newbill.get(tid):
                raise PermissionError('Non cambiare il conduttore di una bolletta con incassi attribuiti.')
            cap=calc(newbill)['q1' if apt==0 else 'q2']
            got=newbill.get('received'+str(apt+1))
            paid=cents(got) if got is not None else (cap if newbill.get('paid'+str(apt+1)) else 0)
            if paid+allocated>cap:
                raise PermissionError('Pagamenti e attribuzioni eccedono la quota della bolletta.')
    for (bill_id,snapshot) in db.execute('SELECT bill_id,snapshot_json FROM closures'):
        if bill_id not in newby or newby[bill_id]!=json.loads(snapshot):
            raise PermissionError('Rendiconto chiuso: sbloccalo dal Gestionale prima di modificarlo o eliminarlo.')

def log(db,action,ctx):db.execute('INSERT INTO events(at,action,context) VALUES(?,?,?)',(dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),action,ctx[:600]))
def get_archive(db):return json.loads(db.execute('SELECT archive FROM state WHERE id=1').fetchone()[0])
def get_bill(db,bid):
    return next((b for b in get_archive(db)['bills'] if b['id']==bid),None)

def get_all(db):
    archive=get_archive(db)
    inv=[dict(zip(('id','bill_id','filename','size','sha256','added_at'),r)) for r in db.execute('SELECT id,bill_id,filename,size,sha256,added_at FROM invoices ORDER BY added_at DESC')]
    adj=[dict(zip(('id','apartment','tenant_id','kind','signed_cents','date','due','bill_id','note','created_at','original_cents','final_cents'),r)) for r in db.execute('SELECT id,apartment,tenant_id,kind,signed_cents,date,due,bill_id,note,created_at,original_cents,final_cents FROM adjustments ORDER BY date DESC,created_at DESC')]
    rec=[dict(zip(('id','apartment','tenant_id','received_cents','date','note','created_at'),r)) for r in db.execute('SELECT id,apartment,tenant_id,received_cents,date,note,created_at FROM receipts ORDER BY date DESC,created_at DESC')]
    closed=[dict(zip(('bill_id','snapshot_json','tenant_snapshot_json','closed_at'),r)) for r in db.execute('SELECT bill_id,snapshot_json,tenant_snapshot_json,closed_at FROM closures')]
    events=[dict(zip(('at','action','context'),r)) for r in db.execute('SELECT at,action,context FROM events ORDER BY id DESC LIMIT 100')]
    return {'archive':archive,'invoices':inv,'adjustments':adj,'receipts':rec,'closures':closed,'events':events}

def mutate(db, path, body):
    # Must be called inside BEGIN IMMEDIATE transaction.
    archive=get_archive(db); tenants={t['id']:t for t in archive.get('tenants',[])}
    bills={b['id']:b for b in archive.get('bills',[])}
    if path=='/api/advanced/assign-bill-tenant':
        # Only assign an unowned, unpaid bill share to a real tenant.
        # Do not alter readings, splits, sums, dates, or payment records.
        a=apt(body.get('apartment'))
        billid=txt(body.get('bill_id'),100,True)
        tid=txt(body.get('tenant_id'),100,True)
        bill=bills.get(billid)
        tenant=tenants.get(tid)
        if not bill:raise ValueError('Bolletta non trovata')
        if not tenant or tenant.get('apartment')!=a or not tenant.get('active'):
            raise ValueError('Scegli un conduttore attivo dell’appartamento corretto')
        slot=a+1
        key='tenant'+str(slot)+'Id'
        if bill.get(key):
            raise PermissionError('La quota è già associata a un conduttore: non sovrascrivere lo storico')
        if tenant.get('start','') and tenant['start']>bill.get('start',''):
            raise PermissionError('La locazione inizia dopo l’inizio del periodo fatturato: verificare manualmente il cambio conduttore')
        if tenant.get('end','') and tenant['end']<bill.get('end',''):
            raise PermissionError('La locazione termina prima della fine del periodo fatturato: serve una ripartizione manuale')
        if db.execute('SELECT 1 FROM closures WHERE bill_id=?',(billid,)).fetchone():
            raise PermissionError('Bolletta chiusa: occorre una riapertura tracciata')
        if db.execute('SELECT 1 FROM tenant_report_bills WHERE bill_id=?',(billid,)).fetchone():
            raise PermissionError('Bolletta già inclusa in un rendiconto emesso')
        share=calc(bill)['q'+str(slot)]
        received=bill.get('received'+str(slot))
        if bill.get('paid'+str(slot)) or (received is not None and cents(received)!=0) or bill.get('paymentDate'+str(slot)):
            raise PermissionError('Sono presenti pagamenti sulla quota: associazione automatica non consentita')
        if db.execute('SELECT 1 FROM receipt_allocations WHERE bill_id=? AND apartment=? AND voided_at IS NULL',(billid,a)).fetchone():
            raise PermissionError('Quota con pagamenti riconciliati: modifica non consentita')
        if db.execute('SELECT 1 FROM adjustments WHERE bill_id=? AND apartment=?',(billid,a)).fetchone():
            raise PermissionError('Quota con conguagli collegati: verifica contabile manuale')
        bill[key]=tid
        bill['tenant'+str(slot)+'Snapshot']={k:tenant.get(k,'') for k in ('name','address','taxCode')}
        rev=db.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]
        db.execute('UPDATE state SET revision=?, archive=? WHERE id=1',
                   (rev+1,json.dumps(archive,ensure_ascii=False,separators=(',',':'))))
        log(db,'conduttore_associato_bolletta',f'{billid} appartamento {slot} conduttore {tid}')
        return {'ok':True,'revision':rev+1,'share_cents':share}
    if path=='/api/advanced/adjustment':
        a=apt(body.get('apartment')); tid=txt(body.get('tenant_id',''),100)
        if tid and (tid not in tenants or tenants[tid]['apartment']!=a): raise ValueError('Conduttore non valido per appartamento')
        kind=body.get('kind')
        if kind not in ('conguaglio','rettifica','credito'):raise ValueError('Tipologia non valida')
        amount=cents(body.get('amount'))
        if amount==0:raise ValueError('Importo non può essere zero')
        if kind=='credito' and amount>=0:raise ValueError('Un credito deve essere negativo')
        previous=final=None
        if kind=='conguaglio':
            previous=cents(body.get('previous'))
            final=cents(body.get('definitive'))
            if previous<0 or final<0:raise ValueError('Importi di partenza del conguaglio non validi')
            if final-previous!=amount:raise ValueError('Conguaglio diverso dalla differenza tra importo definitivo e precedente')
        billid=txt(body.get('bill_id',''),100)
        if billid and billid not in bills:raise ValueError('Bolletta collegata inesistente')
        when=datecheck(body.get('date'),False); due=datecheck(body.get('due',''))
        note=txt(body.get('note',''),1000,True)
        uid=receipt_id(); now=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
        db.execute('INSERT INTO adjustments(id,apartment,tenant_id,kind,signed_cents,date,due,bill_id,note,created_at,original_cents,final_cents) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(uid,a,tid,kind,amount,when,due,billid,note,now,previous,final))
        log(db,'conguaglio_creato',f'{uid} appartamento={a} importo={amount}')
        return {'id':uid}
    if path=='/api/advanced/receipt':
        a=apt(body.get('apartment')); tid=txt(body.get('tenant_id',''),100)
        if tid and (tid not in tenants or tenants[tid]['apartment']!=a):raise ValueError('Conduttore non valido')
        amount=cents(body.get('amount'))
        if amount<=0:raise ValueError('Importo deve essere positivo')
        kind=body.get('kind','incasso')
        if kind not in ('incasso','rimborso'):raise ValueError('Tipo operazione non valido')
        if kind=='rimborso':
            # Refunds can only use the available credit of this exact tenant account.
            credit_balance=0
            for bill in archive.get('bills',[]):
                if bill.get('tenant'+str(a+1)+'Id','')!=tid: continue
                q=calc(bill)['q1' if a==0 else 'q2']
                got=bill.get('received'+str(a+1))
                if got is None:got=q/100 if bill.get('paid'+str(a+1)) else 0
                credit_balance+=q-cents(got)
            credit_balance+=sum(v for (v,) in db.execute('SELECT signed_cents FROM adjustments WHERE apartment=? AND tenant_id=?',(a,tid)))
            credit_balance-=sum(v for (v,) in db.execute('SELECT received_cents FROM receipts WHERE apartment=? AND tenant_id=?',(a,tid)))
            if credit_balance>=0 or amount> -credit_balance:
                raise ValueError('Rimborso superiore al credito disponibile del conduttore selezionato.')
            amount=-amount
        # A refund must not invalidate already allocated receipts.
        if kind=='rimborso' and db.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_allocations'").fetchone():
            allotted=db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM receipt_allocations WHERE source_kind='receipt' AND apartment=? AND tenant_id=?",(a,tid)).fetchone()[0]
            net=db.execute('SELECT COALESCE(SUM(received_cents),0) FROM receipts WHERE apartment=? AND tenant_id=?',(a,tid)).fetchone()[0]
            if net+amount<allotted:
                raise ValueError('Rimborso incompatibile con incassi già attribuiti alle bollette.')
        when=datecheck(body.get('date'),False); note=txt(body.get('note',''),1000,True)
        note=('Rimborso: ' if kind=='rimborso' else '')+note
        uid=receipt_id(); now=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
        db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,?)',(uid,a,tid,amount,when,note,now))
        log(db,'incasso_creato',f'{uid} appartamento={a} importo={amount}')
        return {'id':uid}
    if path=='/api/advanced/delete':
        typ=body.get('type'); uid=txt(body.get('id'),64,True)
        if typ not in ('adjustments','receipts'):raise ValueError('Tipo movimento non valido')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_allocations'").fetchone():
            kind='receipt' if typ=='receipts' else 'credit'
            if db.execute('SELECT 1 FROM receipt_allocations WHERE source_kind=? AND source_id=?',(kind,uid)).fetchone():
                raise ValueError('Movimento attribuito a bollette: revocare prima le attribuzioni ammesse.')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='generic_source_assignments'").fetchone():
            kind='receipt' if typ=='receipts' else 'credit'
            if db.execute('SELECT 1 FROM generic_source_assignments WHERE source_kind=? AND source_id=?',(kind,uid)).fetchone():
                raise ValueError('Movimento identificato con registrazione storica: non eliminare senza procedura di storno documentata.')
        if db.execute(f'DELETE FROM {typ} WHERE id=?',(uid,)).rowcount!=1:raise ValueError('Movimento inesistente')
        log(db,'movimento_eliminato',f'{typ}:{uid}')
        return {'ok':True}
    if path=='/api/advanced/split':
        billid=txt(body.get('bill_id'),100,True)
        if billid not in bills:raise ValueError('Bolletta non trovata')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tenant_report_bills'").fetchone() and db.execute('SELECT 1 FROM tenant_report_bills WHERE bill_id=?',(billid,)).fetchone():
            raise PermissionError('Bolletta già inclusa in un rendiconto numerato: la ripartizione non è più modificabile.')
        if db.execute('SELECT 1 FROM closures WHERE bill_id=?',(billid,)).fetchone():raise PermissionError('Rendiconto chiuso: riaprilo prima.')
        fixed=body.get('fixedShare1'); variable=body.get('variableShare1')
        def percent(v):
            if type(v) not in (int,float) or not (0<=v<=100) or round(v,2)!=v:raise ValueError('Percentuale non valida (0–100)')
            return v
        if variable is not None:variable=percent(variable)
        fixed=percent(fixed)
        obj=bills[billid]
        former=calc(obj)
        # Convert a legacy 'paid' flag to an explicit amount before changing its quota.
        for a in (0,1):
            k='received'+str(a+1)
            if obj.get(k) is None and obj.get('paid'+str(a+1)):
                obj[k]=former['q1' if a==0 else 'q2']/100
        obj['fixedShare1']=fixed
        if variable is None:obj.pop('variableShare1',None)
        else:obj['variableShare1']=variable
        after=calc(obj)
        for a in (0,1):
            received=obj.get('received'+str(a+1),0) or 0
            if cents(received)>after['q1' if a==0 else 'q2']:
                raise ValueError('La nuova quota sarebbe inferiore agli importi già incassati. Registra prima la rettifica contabile necessaria.')
        rev=db.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]
        db.execute('UPDATE state SET revision=?,archive=? WHERE id=1',(rev+1,json.dumps(archive,ensure_ascii=False)))
        log(db,'ripartizione_modificata',f'{billid} fissa={fixed} variabile={variable}')
        return {'revision':rev+1}
    if path=='/api/advanced/closure':
        billid=txt(body.get('bill_id'),100,True)
        if billid not in bills:raise ValueError('Bolletta non trovata')
        action=body.get('action')
        if action=='close':
            if db.execute('SELECT 1 FROM closures WHERE bill_id=?',(billid,)).fetchone():raise ValueError('Già chiuso')
            b=bills[billid]
            snapshots=[b.get('tenant1Snapshot',{}),b.get('tenant2Snapshot',{})]
            now=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
            db.execute('INSERT INTO closures VALUES(?,?,?,?)',(billid,json.dumps(b,ensure_ascii=False,sort_keys=True),json.dumps(snapshots,ensure_ascii=False),now))
            log(db,'rendiconto_chiuso',billid)
            return {'ok':True}
        if action=='reopen':
            reason=txt(body.get('reason',''),400,True)
            if db.execute('DELETE FROM closures WHERE bill_id=?',(billid,)).rowcount!=1:raise ValueError('Rendiconto non chiuso')
            log(db,'rendiconto_riaperto',f'{billid}: {reason}')
            return {'ok':True}
        raise ValueError('Azione non valida')
    if path=='/api/advanced/tenant_end':
        tid=txt(body.get('tenant_id',''),100,True)
        when=datecheck(body.get('end'),False)
        t=tenants.get(tid)
        if not t:raise ValueError('Inquilino non trovato')
        if t.get('start') and when<t['start']:raise ValueError('Data antecedente all’inizio rapporto')
        t['end']=when;t['active']=False
        defs=archive.get('defaultTenants',['',''])
        archive['defaultTenants']=[('' if x==tid else x) for x in defs]
        rev=db.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]
        db.execute('UPDATE state SET revision=?,archive=? WHERE id=1',(rev+1,json.dumps(archive,ensure_ascii=False)))
        log(db,'rapporto_terminato',f'{tid}: {when}')
        return {'revision':rev+1}
    raise ValueError('Operazione non supportata')

def add_invoice(db,bill_id,filename,data):
    if not get_bill(db,bill_id):raise ValueError('Bolletta collegata inesistente')
    if len(data)>15*1024*1024 or len(data)<8 or not data.startswith(b'%PDF-'):raise ValueError('Caricare un PDF valido massimo 15 MB')
    filename=txt(filename,160,True).replace('\\','_').replace('/','_')
    if not filename.lower().endswith('.pdf'):raise ValueError('Nome documento non valido')
    uid=receipt_id()
    now=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    db.execute('INSERT INTO invoices VALUES(?,?,?,?,?,?,?)',(uid,bill_id,filename,len(data),hashlib.sha256(data).hexdigest(),data,now))
    log(db,'fattura_aggiunta',f'{uid} bolletta={bill_id} nome={filename}')
    return {'id':uid}

def balance(archive, adjustments, receipts):
    """Credits and debits are never automatically offset between different tenants."""
    today=dt.datetime.now(ZoneInfo('Europe/Rome')).date().isoformat()
    groups={}
    def account(a,tid):
        key=(a,tid or '')
        if key not in groups:groups[key]={'due':0,'paid':0,'adjustments':0,'other_paid':0,'overdue':0}
        return groups[key]
    for b in archive.get('bills',[]):
        try:q=calc(b)
        except (ValueError,KeyError,TypeError):continue
        for a in (0,1):
            group=account(a,b.get('tenant'+str(a+1)+'Id',''))
            due=q['q1' if a==0 else 'q2'];group['due']+=due
            paid=b.get('received'+str(a+1))
            if paid is None:paid=(due/100 if b.get('paid'+str(a+1)) else 0)
            paid=cents(paid);group['paid']+=paid
            if b.get('due'+str(a+1),'') and b['due'+str(a+1)]<today and due>paid:group['overdue']+=due-paid
    for adj in adjustments:
        g=account(adj['apartment'],adj['tenant_id']);g['adjustments']+=adj['signed_cents']
        if adj['due'] and adj['due']<today and adj['signed_cents']>0:g['overdue']+=adj['signed_cents']
    for rec in receipts:account(rec['apartment'],rec['tenant_id'])['other_paid']+=rec['received_cents']
    totals=[{'due':0,'paid':0,'adjustments':0,'other_paid':0,'balance':0,'overdue':0,'credit':0,'debit':0} for _ in (0,1)]
    for (a,_),g in groups.items():
        result=totals[a]
        for field in ('due','paid','adjustments','other_paid'):result[field]+=g[field]
        bal=g['due']+g['adjustments']-g['paid']-g['other_paid']
        result['debit']+=max(0,bal)
        result['credit']+=max(0,-bal)
        result['overdue']+=max(0,min(g['overdue'],bal))
    for t in totals:t['balance']=t['debit']-t['credit']
    return totals

def send_notice(service,message):
    if not re.fullmatch(r'notify\.[a-z0-9_]+',service):raise ValueError('Servizio notify non valido: es. notify.mobile_app_telefono')
    token=os.environ.get('SUPERVISOR_TOKEN','')
    if not token: raise RuntimeError('SUPERVISOR_TOKEN non disponibile: notifiche solo su Home Assistant OS')
    body=json.dumps({'title':'Casa Consumi · Scadenze','message':message}).encode('utf-8')
    target=service.split('.',1)[1]
    req=urllib.request.Request(f'http://supervisor/core/api/services/notify/{target}',data=body,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=12) as response:
        if response.status not in (200,201): raise RuntimeError('Home Assistant restituisce '+str(response.status))
    return True

def reminder_loop(db_factory, options_file):
    while True:
        try:
            settings=json.loads(Path(options_file).read_text()) if Path(options_file).exists() else {}
            if settings.get('notifications_enabled') and settings.get('notify_service'):
                hh=settings.get('notify_hour','09:00')
                now=dt.datetime.now(ZoneInfo('Europe/Rome'))
                if now.strftime('%H:%M') >= hh:
                    with db_factory() as db:
                        result=get_all(db)
                        sums=balance(result['archive'],result['adjustments'],result['receipts'])
                        for a,item in enumerate(sums):
                            if item['overdue']<=0:continue
                            day=now.date().isoformat()
                            if db.execute('SELECT 1 FROM notifications_sent WHERE date=? AND apartment=?',(day,a)).fetchone():continue
                            name=result['archive']['names'][a]
                            send_notice(settings['notify_service'],f'{name}: risultano importi scaduti per € {euro(item["overdue"]):.2f}. Verifica il gestionale Casa Consumi.')
                            db.execute('INSERT INTO notifications_sent(date,apartment) VALUES(?,?)',(day,a))
        except Exception:
            logging.exception('Controllo notifiche Casa Consumi non riuscito')
        time.sleep(300)

def start_reminders(db_factory,options_file):
    threading.Thread(target=reminder_loop,args=(db_factory,options_file),daemon=True,name='casa-reminders').start()
