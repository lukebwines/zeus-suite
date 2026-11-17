"""ZEUS Casa: immutable per-apartment tenant report issues (not invoices or new receivables).

Issuing a report creates a numbered document referencing *existing* bill credits.
No finance ledger movements are created. Preview never modifies SQLite.
"""
from __future__ import annotations
import calendar
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from io import BytesIO
import json
import sqlite3
import re
import uuid

import advanced
import report_corrections

KINDS = {'electricity': 'Energia elettrica', 'gas': 'Gas', 'water': 'Acqua'}


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS tenant_reports (
      id TEXT PRIMARY KEY,
      year INTEGER NOT NULL,
      serial INTEGER NOT NULL,
      apartment INTEGER NOT NULL CHECK (apartment IN (0,1)),
      tenant_id TEXT NOT NULL,
      issued_on TEXT NOT NULL,
      due_on TEXT NOT NULL,
      amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
      snapshot TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(year,serial)
    );
    CREATE TABLE IF NOT EXISTS tenant_report_bills (
      report_id TEXT NOT NULL REFERENCES tenant_reports(id),
      bill_id TEXT NOT NULL,
      apartment INTEGER NOT NULL CHECK (apartment IN (0,1)),
      PRIMARY KEY (bill_id,apartment)
    );
    CREATE INDEX IF NOT EXISTS idx_tenant_reports_apt_due ON tenant_reports(apartment,due_on);
    CREATE TABLE IF NOT EXISTS receipt_allocations (
      id TEXT PRIMARY KEY,
      source_kind TEXT NOT NULL CHECK(source_kind IN ('receipt','credit')),
      source_id TEXT NOT NULL,
      bill_id TEXT NOT NULL,
      apartment INTEGER NOT NULL CHECK(apartment IN (0,1)),
      tenant_id TEXT NOT NULL,
      amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
      created_at TEXT NOT NULL,
      CHECK(length(source_id)>0 AND length(bill_id)>0)
    );
    CREATE INDEX IF NOT EXISTS idx_receipt_allocations_source ON receipt_allocations(source_kind,source_id);
    CREATE INDEX IF NOT EXISTS idx_receipt_allocations_bill ON receipt_allocations(bill_id,apartment);
    CREATE TABLE IF NOT EXISTS tenant_report_payment_preferences (
      apartment INTEGER PRIMARY KEY CHECK(apartment IN (0,1)),
      settings_json TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    ''')
    report_corrections.initialize(db)


PAYMENT_FIELDS = ('enabled','beneficiary','iban','reference','instructions')


def _clean_payment_string(v, maximum, label):
    if not isinstance(v, str) or len(v) > maximum:
        raise ValueError(f'{label}: testo non valido o troppo lungo')
    # No control characters/newlines inside print-ready payment details.
    if any(ord(c)<32 or ord(c)==127 for c in v):
        raise ValueError(f'{label}: sono ammessi solo caratteri stampabili')
    return ' '.join(v.split())


def validate_payment(obj):
    if obj is None:
        return None
    if not isinstance(obj,dict) or any(k not in PAYMENT_FIELDS for k in obj):
        raise ValueError('Dati per il pagamento non validi')
    if type(obj.get('enabled')) is not bool:
        raise ValueError('Specificare se includere i dati per il pagamento')
    beneficiary=_clean_payment_string(obj.get('beneficiary',''),150,'Intestatario')
    iban=_clean_payment_string(obj.get('iban',''),50,'IBAN').replace(' ','').upper()
    reference=_clean_payment_string(obj.get('reference',''),160,'Causale')
    instructions=_clean_payment_string(obj.get('instructions',''),250,'Istruzioni aggiuntive')
    if obj['enabled'] and iban and (not re.fullmatch(r'[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}',iban) or not 15 <= len(iban) <= 34 or not _valid_iban(iban)):
        raise ValueError('IBAN non valido: controlla le cifre e il codice di controllo')
    if obj['enabled'] and not beneficiary:
        raise ValueError('Intestatario del conto obbligatorio per il pagamento')
    if obj['enabled'] and not iban:
        raise ValueError('IBAN obbligatorio per il pagamento')
    return {'enabled':obj['enabled'],'beneficiary':beneficiary,'iban':iban,
            'reference':reference,'instructions':instructions}


def _valid_iban(iban):
    rem=0
    for c in iban[4:]+iban[:4]:
        digits=c if c.isdigit() else str(ord(c)-ord('A')+10)
        for d in digits:
            rem=(rem*10+int(d))%97
    return rem==1


def payment_preferences(db):
    profiles=[{'enabled':False,'beneficiary':'','iban':'','reference':'','instructions':''} for _ in range(2)]
    for apt,raw in db.execute('SELECT apartment,settings_json FROM tenant_report_payment_preferences'):
        if apt in (0,1):
            try:
                parsed=validate_payment(json.loads(raw))
                if parsed is not None: profiles[apt]=parsed
            except (ValueError,TypeError,json.JSONDecodeError):
                pass
    return profiles


def save_payment_preferences(db, body):
    if not isinstance(body,dict) or type(body.get('apartment')) is not int or body['apartment'] not in (0,1):
        raise ValueError('Appartamento non valido')
    details=validate_payment(body.get('payment'))
    if details is None:
        raise ValueError('Indicare i dati per il pagamento')
    db.execute('INSERT INTO tenant_report_payment_preferences(apartment,settings_json,updated_at) VALUES(?,?,?) '
      'ON CONFLICT(apartment) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at',
      (body['apartment'],json.dumps(details,ensure_ascii=False),datetime.now(timezone.utc).isoformat(timespec='seconds')))
    # Never store bank details in the generic activity log.
    advanced.log(db,'coordinate_pagamento_aggiornate','Appartamento '+str(body['apartment']+1))
    return {'ok':True,'apartment':body['apartment']}

def iso_date(raw):
    if not isinstance(raw, str):
        raise ValueError('Data non valida')
    try:
        parsed = date.fromisoformat(raw)
        if parsed.isoformat() != raw:
            raise ValueError()
        return parsed
    except ValueError:
        raise ValueError('Data non valida: usare AAAA-MM-GG') from None


def plus_month(d):
    year = d.year + int(d.month == 12)
    month = d.month % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))



def bill_allocated(db, bill_id, apartment):
    return db.execute(
        'SELECT COALESCE(SUM(amount_cents),0) FROM receipt_allocations WHERE bill_id=? AND apartment=? AND voided_at IS NULL',
        (bill_id, apartment)).fetchone()[0]


def pending_reconciliation(db, apartment, tenant_id):
    """Unallocated ledger movements for this tenant plus account-level unassigned entries.

    The original receipts and corrections continue affecting the account balance
    exactly once; linking a payment to a bill is not another ledger posting.
    """
    receipts = db.execute(
        "SELECT COALESCE(SUM(received_cents),0) FROM receipts "
        "WHERE apartment=? AND tenant_id IN (?, '')", (apartment, tenant_id)).fetchone()[0]
    credits = db.execute(
        "SELECT COALESCE(SUM(-signed_cents),0) FROM adjustments "
        "WHERE apartment=? AND tenant_id IN (?, '') AND signed_cents<0", (apartment, tenant_id)).fetchone()[0]
    allocated_receipts, allocated_credits = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN source_kind='receipt' THEN amount_cents ELSE 0 END),0), "
        "COALESCE(SUM(CASE WHEN source_kind='credit' THEN amount_cents ELSE 0 END),0) "
        'FROM receipt_allocations WHERE apartment=? AND tenant_id=? AND voided_at IS NULL',
        (apartment, tenant_id)).fetchone()
    return {'receipts_cents': max(0, receipts-allocated_receipts),
            'credits_cents': max(0, credits-allocated_credits)}


def allocation_sources(db):
    """Available movements exposed only to authenticated administrators."""
    result=[]
    rows=db.execute('SELECT id,apartment,tenant_id,received_cents,date FROM receipts '
                    'WHERE received_cents>0 ORDER BY date,id').fetchall()
    rows += [('credit:'+ident,apt,tid,-amount,when) for ident,apt,tid,amount,when in
             db.execute('SELECT id,apartment,tenant_id,signed_cents,date FROM adjustments '
                        'WHERE signed_cents<0 ORDER BY date,id')]
    for ident,apt,tid,amount,when in rows:
        kind='credit' if ident.startswith('credit:') else 'receipt'
        source_id=ident[7:] if kind=='credit' else ident
        allocated=db.execute('SELECT COALESCE(SUM(amount_cents),0) FROM receipt_allocations '
                             'WHERE source_kind=? AND source_id=? AND voided_at IS NULL',(kind,source_id)).fetchone()[0]
        available=max(0,amount-allocated)
        if kind=='receipt' and tid:
            # A later refund cannot release already allocated cash to be reused.
            net=db.execute('SELECT COALESCE(SUM(received_cents),0) FROM receipts '
                           'WHERE apartment=? AND tenant_id=?',(apt,tid)).fetchone()[0]
            acct_allotted=db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM receipt_allocations "
                                    "WHERE source_kind='receipt' AND apartment=? AND tenant_id=? AND voided_at IS NULL",
                                    (apt,tid)).fetchone()[0]
            available=min(available,max(0,net-acct_allotted))
        if available>0:
            result.append({'kind':kind,'source_id':source_id,'apartment':apt,
                           'tenant_id':tid,'date':when,'available_cents':available})
    return result


def list_allocations(db):
    return [dict(zip(('id','source_kind','source_id','bill_id','apartment','tenant_id',
                      'amount_cents','created_at','voided_at'),r)) for r in db.execute(
        'SELECT id,source_kind,source_id,bill_id,apartment,tenant_id,amount_cents,created_at,voided_at '
        'FROM receipt_allocations ORDER BY created_at DESC,id')]


def allocate(db, body):
    """Explicitly associate a real receipt or credit with one bill share.

    Caller must hold BEGIN IMMEDIATE to serialize the availability checks.
    No receipt, credit, or amount in the source archive is altered.
    """
    if not isinstance(body,dict):raise ValueError('Richiesta non valida')
    kind=body.get('source_kind'); source_id=body.get('source_id');bill_id=body.get('bill_id')
    if kind not in ('receipt','credit') or not isinstance(source_id,str) or not 1<=len(source_id)<=120:
        raise ValueError('Movimento da riconciliare non valido')
    if not isinstance(bill_id,str) or not 1<=len(bill_id)<=120:
        raise ValueError('Bolletta non valida')
    amount=advanced.cents(body.get('amount'))
    if amount<=0:raise ValueError('Importo da attribuire deve essere positivo')
    table,amount_col=('receipts','received_cents') if kind=='receipt' else ('adjustments','signed_cents')
    row=db.execute(f'SELECT apartment,tenant_id,{amount_col} FROM {table} WHERE id=?',(source_id,)).fetchone()
    if row is None:raise ValueError('Movimento inesistente')
    apt,tid,source_amount=row
    if not tid:
        raise ValueError('Movimento riferito al solo appartamento: associare prima un conduttore con una procedura esplicita')
    if (kind=='receipt' and source_amount<=0) or (kind=='credit' and source_amount>=0):
        raise ValueError('Solo incassi positivi o crediti negativi sono attribuibili')
    source_limit=source_amount if kind=='receipt' else -source_amount
    used=db.execute('SELECT COALESCE(SUM(amount_cents),0) FROM receipt_allocations '
                    'WHERE source_kind=? AND source_id=? AND voided_at IS NULL',(kind,source_id)).fetchone()[0]
    if used+amount>source_limit:raise ValueError('Importo superiore al residuo del movimento')
    if kind=='receipt':
        net=db.execute('SELECT COALESCE(SUM(received_cents),0) FROM receipts '
                       'WHERE apartment=? AND tenant_id=?',(apt,tid)).fetchone()[0]
        used_account=db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM receipt_allocations "
                                "WHERE source_kind='receipt' AND apartment=? AND tenant_id=? AND voided_at IS NULL",
                                (apt,tid)).fetchone()[0]
        if amount+used_account>net:
            raise ValueError('Incasso già rimborsato o indisponibile: importo netto insufficiente')
    archive=advanced.get_archive(db)
    bill=next((b for b in archive.get('bills',[]) if b.get('id')==bill_id),None)
    if bill is None:raise ValueError('Bolletta inesistente')
    if bill.get('tenant'+str(apt+1)+'Id','')!=tid:
        raise ValueError('Il movimento e la quota appartengono a conduttori o appartamenti diversi')
    due=advanced.calc(bill)['q1' if apt==0 else 'q2']
    residual=due-_paid_cents(bill,apt,due)-bill_allocated(db,bill_id,apt)
    if amount>residual:raise ValueError('Attribuzione superiore al residuo della quota')
    ident=uuid.uuid4().hex
    db.execute('INSERT INTO receipt_allocations(id,source_kind,source_id,bill_id,apartment,tenant_id,amount_cents,created_at) VALUES(?,?,?,?,?,?,?,?)',(
        ident,kind,source_id,bill_id,apt,tid,amount,
        datetime.now(timezone.utc).isoformat(timespec='seconds')))
    advanced.log(db,'movimento_riconciliato',f'{ident} {kind}:{source_id} bolletta={bill_id} quota={amount}')
    return {'ok':True,'id':ident,'amount_cents':amount,'bill_id':bill_id}


def revoke_allocation(db,body):
    """Undo a mistaken unissued attribution with an audit trail.

    Previously issued report snapshots are immutable and must not be silently
    contradicted: allocations on issued shares require a separate correction.
    """
    ident=body.get('id') if isinstance(body,dict) else None
    if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident):
        raise ValueError('Identificativo attribuzione non valido')
    row=db.execute('SELECT bill_id,apartment FROM receipt_allocations WHERE id=?',(ident,)).fetchone()
    if row is None:raise ValueError('Attribuzione inesistente')
    if db.execute('SELECT 1 FROM tenant_report_bills WHERE bill_id=? AND apartment=?',row).fetchone():
        raise ValueError('Attribuzione collegata a un rendiconto emesso: richiesta rettifica documentale')
    reason=body.get('reason','').strip() if isinstance(body.get('reason'),str) else ''
    if len(reason)<5 or len(reason)>300:raise ValueError('Indicare una motivazione (5-300 caratteri)')
    db.execute('DELETE FROM receipt_allocations WHERE id=?',(ident,))
    advanced.log(db,'attribuzione_annullata',f'{ident} causa={reason}')
    return {'ok':True}


def guard_reconciliation(db, apartment, tenant_id):
    """Fail closed before producing a document that asks for payment twice."""
    pending = pending_reconciliation(db, apartment, tenant_id)
    if pending['receipts_cents'] or pending['credits_cents']:
        raise ValueError(
            'Rendiconto non emettibile: sono presenti incassi o crediti '
            'non imputati alle singole bollette del conduttore/appartamento. '
            'Verificare e riconciliare i movimenti nella situazione contabile '
            'prima di richiedere il pagamento. Nessun importo viene attribuito automaticamente.'
        )

def _prepare(db, body):
    if not isinstance(body, dict):
        raise ValueError('Richiesta non valida')
    apartment = body.get('apartment')
    if type(apartment) is not int or apartment not in (0,1):
        raise ValueError('Appartamento non valido')
    issued = iso_date(body.get('issued_on', ''))
    due_raw = body.get('due_on')
    due = iso_date(due_raw) if due_raw else plus_month(issued)
    if due < issued:
        raise ValueError('La scadenza non può precedere l’emissione')
    bill_ids = body.get('bill_ids')
    if not isinstance(bill_ids, list) or not (1 <= len(bill_ids) <= 50):
        raise ValueError('Selezionare da 1 a 50 bollette')
    if not all(isinstance(x,str) and 0 < len(x) <= 120 for x in bill_ids) or len(set(bill_ids)) != len(bill_ids):
        raise ValueError('Elenco bollette non valido o duplicato')
    row = db.execute('SELECT archive FROM state WHERE id=1').fetchone()
    if row is None:
        raise ValueError('Archivio ZEUS non trovato')
    archive = json.loads(row[0])
    bills = {b['id']: b for b in archive.get('bills',[])}
    tenant_id = body.get('tenant_id')
    if not isinstance(tenant_id,str) or not tenant_id or len(tenant_id)>120:
        raise ValueError('Selezionare un conduttore')
    tenants = {t['id']:t for t in archive.get('tenants',[])}
    tenant = tenants.get(tenant_id)
    # Historical tenants remain eligible when present in the address book.
    if tenant is None or tenant.get('apartment') != apartment:
        raise ValueError('Conduttore non associato all’appartamento')
    entries=[]; total=0; gross=0; already_received=0
    for bid in bill_ids:
        bill=bills.get(bid)
        if not bill:
            raise ValueError('Bolletta inesistente: '+bid[:30])
        if bill.get('tenant'+str(apartment+1)+'Id','')!=tenant_id:
            raise ValueError('Non è possibile unire bollette di conduttori diversi')
        existing=db.execute('SELECT report_id FROM tenant_report_bills WHERE bill_id=? AND apartment=?',(bid,apartment)).fetchone()
        if existing:
            raise ValueError('Bolletta già inserita in un rendiconto emesso: '+bid[:30])
        calc=advanced.calc(bill)
        amount=calc['q1' if apartment==0 else 'q2']
        if amount<=0:
            raise ValueError('La bolletta selezionata non contiene quote da addebitare a questo appartamento')
        received=_paid_cents(bill,apartment,amount)+bill_allocated(db,bid,apartment)
        if received>=amount:
            raise ValueError('Bolletta già interamente pagata: non vi sono importi da versare')
        fixed=calc['f1' if apartment==0 else 'f2']
        variable=calc['v1' if apartment==0 else 'v2']
        if fixed+variable != amount:
            raise ValueError('Quote della bolletta non riconciliate')
        detail=_consumption_detail(db,bill,apartment)
        item={
          'consumption_detail':detail,
          'bill_id':bid, 'utility':bill.get('utility',''),
          'invoice_number':str(bill.get('invoiceNumber') or '')[:80],
          'invoice_date':bill.get('invoiceDate') or '',
          'period_start':bill.get('start') or '', 'period_end':bill.get('end') or '',
          'fixed_cents':fixed, 'variable_cents':variable, 'amount_cents':amount,
          'already_received_cents':received,'outstanding_cents':amount-received,
          'tenant_id':tenant_id,
        }
        entries.append(item);total+=amount-received;gross+=amount;already_received+=received
    # Per-bill received amounts are not the same as generic ledger receipts:
    # stop a new report instead of silently asking for already received money.
    guard_reconciliation(db, apartment, tenant_id)
    entries.sort(key=lambda x:(x.get('invoice_date') or '',x.get('period_end') or '',x['utility'],x['bill_id']))
    payment=validate_payment(body.get('payment'))
    snapshot={
      'render_model':'A1',
      'payment':payment if payment and payment['enabled'] else None,
      'apartment':apartment,
      'apartment_name':str(archive.get('names',['Appartamento 1','Appartamento 2'])[apartment])[:60],
      'tenant_id':tenant_id,
      'tenant_name':str(tenant.get('name') or '')[:180],
      'tenant_address':str(tenant.get('address') or '')[:250],
      'contract_start':str(tenant.get('start') or ''),
      'contract_end':str(tenant.get('end') or ''),
      'issued_on':issued.isoformat(), 'due_on':due.isoformat(),
      'entries':entries,'amount_cents':total,
      'gross_cents':gross,'already_received_cents':already_received,
    }
    return snapshot


def _consumption_detail(db, bill, apartment):
    """Snapshot evidence only. No fabricated readings, allocations or gas conversions.

    The physical reading and the allocation are different concepts. The first is
    a real progressive meter reading; the latter may be a provisional accounting
    allocation from a consumption carry, never described as a physical reading.
    """
    kind=bill.get('utility')
    unit='kWh' if kind=='electricity' else 'm³'
    out={'unit':unit,'start':None,'end':None,'measured':None,
         'allocated':None,'carry':None,'meter_label':'', 'note':''}
    snap=None
    try:
        row=db.execute('SELECT snap_json FROM meter_bill_links WHERE bill_id=?',(bill['id'],)).fetchone()
        if row: snap=json.loads(row[0])
    except (TypeError,ValueError,json.JSONDecodeError,sqlite3.OperationalError):
        snap=None
    mode=(snap or {}).get('meter_mode') or bill.get('meterMode') or 'single_sub'
    meter='sub' if apartment==0 else ('sub2' if mode=='two_subs' else None)
    out['meter_label']=('Sottocontatore' if meter else 'Consumo per differenza')
    source=(snap or {}).get(meter) if meter else None
    if isinstance(source,dict):
        for key,dest in [('start','start'),('end','end'),('consumption','measured')]:
            if source.get(key) not in (None,''):
                out[dest]=_quantity(source[key])
        if source.get('start_date') and source.get('end_date'):
            out['reading_dates']=(source['start_date'],source['end_date'])
    elif apartment==0 and meter=='sub':
        # Older manually entered invoices retain actual submeter initial/final
        # but do not necessarily have dated provider-aligned readings.
        try:
            st,ed=Decimal(str(bill['subStart'])),Decimal(str(bill['subEnd']))
            if st>=0 and ed>=st:
                out.update(start=_quantity(st),end=_quantity(ed),measured=_quantity(ed-st))
        except (KeyError,ValueError,TypeError,ArithmeticError):pass
    elif apartment==1 and meter=='sub2':
        try:
            st,ed=Decimal(str(bill['sub2Start'])),Decimal(str(bill['sub2End']))
            if st>=0 and ed>=st:
                out.update(start=_quantity(st),end=_quantity(ed),measured=_quantity(ed-st))
        except (KeyError,ValueError,TypeError,ArithmeticError):pass
    elif apartment==1 and isinstance(snap,dict):
        main,sub=snap.get('main'),snap.get('sub')
        # Only calculate by difference if the physical measurement periods align.
        if (isinstance(main,dict) and isinstance(sub,dict) and
            main.get('start_date')==sub.get('start_date') and
            main.get('end_date')==sub.get('end_date') and
            main.get('start_date') and main.get('end_date')):
            try:
                st=Decimal(str(main['start']))-Decimal(str(sub['start']))
                ed=Decimal(str(main['end']))-Decimal(str(sub['end']))
                if st>=0 and ed>=st:
                    out.update(start=_quantity(st),end=_quantity(ed),measured=_quantity(ed-st))
                    out['reading_dates']=(main['start_date'],main['end_date'])
            except (KeyError,ValueError,TypeError,ArithmeticError):pass
    ledger=bill.get('consumptionReconciliation')
    if not isinstance(ledger,dict) or not ledger.get('available'):
        try:
            row=db.execute('SELECT snapshot_json FROM consumption_carry WHERE bill_id=?',(bill['id'],)).fetchone()
            if row: ledger=json.loads(row[0])
        except (ValueError,TypeError,json.JSONDecodeError,sqlite3.OperationalError):pass
    if isinstance(ledger,dict) and ledger.get('available'):
        suffix=str(apartment+1)
        for k,dest in [('measured_','measured'),('allocated_','allocated'),('carry_','carry')]:
            if ledger.get(k+suffix) not in (None,''):
                out[dest]=_quantity(ledger[k+suffix])
        # For progressive carry, the allocated measurement period may start at
        # the previous carry checkpoint, not at the last two meter readings.
        # Use the checkpoint values instead of mismatching reading endpoints.
        try:
            prior=db.execute('SELECT snapshot_json FROM consumption_carry WHERE utility=? AND period_end<? ORDER BY period_end DESC LIMIT 1',
                             (kind,bill.get('end',''))).fetchone()
        except sqlite3.OperationalError:
            prior=None
        if prior:
            try:
                old=json.loads(prior[0]);lastvals=old.get('end_values') or {}
                nowvals=ledger.get('end_values') or {}
                if meter and meter in lastvals and meter in nowvals:
                    out['start']=_quantity(lastvals[meter]);out['end']=_quantity(nowvals[meter])
                    out['reading_dates']=(old.get('end_dates',{}).get(meter),ledger.get('end_dates',{}).get(meter))
                elif (not meter and all(k in lastvals and k in nowvals for k in ('main','sub'))):
                    out['start']=_quantity(Decimal(str(lastvals['main']))-Decimal(str(lastvals['sub'])))
                    out['end']=_quantity(Decimal(str(nowvals['main']))-Decimal(str(nowvals['sub'])))
                    out['reading_dates']=(old.get('end_dates',{}).get('main'),ledger.get('end_dates',{}).get('main'))
                else:
                    # Do not combine older cumulative consumption with unrelated endpoints.
                    out['start']=out['end']=None
            except (ValueError,TypeError,ArithmeticError,json.JSONDecodeError):
                out['start']=out['end']=None
        if ledger.get('estimated'):
            out['note']='Ripartizione provvisoria; periodo della lettura diverso dal periodo fatturato.'
    return out


def _quantity(v):
    try:
        n=Decimal(str(v))
        if not n.is_finite() or abs(n)>Decimal('9999999999'):return None
        return format(n.quantize(Decimal('0.0001')).normalize(),'f')
    except (TypeError,ValueError,ArithmeticError):return None


def preview(db, body):
    data=_prepare(db,body)
    data['label']='ANTEPRIMA – NON EMESSO'
    return data


def issue(db, body):
    # The HTTP handler uses BEGIN IMMEDIATE: next serial + reservation are atomic.
    data=_prepare(db,body)
    issued=iso_date(data['issued_on'])
    year=issued.year
    serial=db.execute('SELECT COALESCE(MAX(serial),0)+1 FROM tenant_reports WHERE year=?',(year,)).fetchone()[0]
    ident=uuid.uuid4().hex
    data['label']=f'{serial:03d}/{year}'
    db.execute('INSERT INTO tenant_reports(id,year,serial,apartment,tenant_id,issued_on,due_on,amount_cents,snapshot,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(
      ident,year,serial,data['apartment'],data['tenant_id'],data['issued_on'],data['due_on'],data['amount_cents'],
      json.dumps(data,ensure_ascii=False),datetime.now(timezone.utc).isoformat(timespec='seconds')))
    for entry in data['entries']:
        db.execute('INSERT INTO tenant_report_bills(report_id,bill_id,apartment) VALUES(?,?,?)',(
          ident,entry['bill_id'],data['apartment']))
    advanced.log(db,'rendiconto_emesso',data['label']+' / '+str(data['apartment']))
    return {'ok':True,'id':ident,'label':data['label'],'amount_cents':data['amount_cents'], 'due_on':data['due_on']}


def _paid_cents(bill,apartment,amount):
    i=apartment+1
    raw=bill.get('received'+str(i))
    if raw is None:
        return amount if bill.get('paid'+str(i)) else 0
    try:
        return max(0,min(amount,advanced.cents(raw)))
    except (ValueError,TypeError):
        return 0


def list_issued_bills(db):
    """Expose report due dates per bill/apartment for read-only arrears views.

    Source of truth remains tenant_reports.due_on. The original bill due fields
    are never rewritten; one apartment's report cannot affect the other.
    """
    rows=db.execute(
        "SELECT b.bill_id,b.apartment,r.due_on,r.year,r.serial "
        "FROM tenant_report_bills AS b JOIN tenant_reports AS r ON r.id=b.report_id"
    ).fetchall()
    return [{"bill_id":bid,"apartment":apt,"due_on":due,
             "report_label":f"{serial:03d}/{year}"} for bid,apt,due,year,serial in rows]

def list_reports(db):
    row=db.execute('SELECT archive FROM state WHERE id=1').fetchone()
    bills={b['id']:b for b in json.loads(row[0]).get('bills',[])} if row else {}
    rows=db.execute('SELECT id,year,serial,apartment,tenant_id,issued_on,due_on,amount_cents,snapshot,created_at FROM tenant_reports ORDER BY issued_on DESC,serial DESC').fetchall()
    result=[]
    for r in rows:
        ident,year,serial,apartment,tenant_id,issued,due,amount,snap,created=r
        data=json.loads(snap)
        received=sum(_paid_cents(bills[e['bill_id']],apartment,e['amount_cents']) + bill_allocated(db,e['bill_id'],apartment) for e in data['entries'] if e['bill_id'] in bills)
        already_received=data.get('already_received_cents',0)
        remaining=max(0,sum(max(0,e['amount_cents']-_paid_cents(bills[e['bill_id']],apartment,e['amount_cents'])-bill_allocated(db,e['bill_id'],apartment)) for e in data['entries'] if e['bill_id'] in bills))
        paid_since_issue=max(0,received-already_received)
        pending=pending_reconciliation(db,apartment,tenant_id) if remaining>0 else {'receipts_cents':0,'credits_cents':0}
        corrections_count=db.execute('SELECT COUNT(*) FROM issued_report_corrections WHERE report_id=?',(ident,)).fetchone()[0]
        needs_reconciliation=bool(pending['receipts_cents'] or pending['credits_cents'])
        today=datetime.now(ZoneInfo('Europe/Rome')).date().isoformat()
        status=('rettificato' if corrections_count else 'da_riconciliare' if needs_reconciliation else
                'pagato' if remaining==0 else 'scaduto' if due<today else
                'parziale' if paid_since_issue>0 or already_received>0 else 'da_ricevere')
        result.append({'id':ident,'label':f'{serial:03d}/{year}', 'apartment':apartment,
          'tenant_name':data['tenant_name'], 'issued_on':issued,'due_on':due,'amount_cents':amount,
          'received_cents':received,'remaining_cents':remaining,'status':status,
          'reconciliation_required':needs_reconciliation,
          'corrections_count':corrections_count,
          'unallocated_receipts_cents':pending['receipts_cents'],
          'unallocated_credits_cents':pending['credits_cents'],
          'bill_count':len(data['entries']),'created_at':created})
    return result


def get(db, ident):
    if not re.fullmatch(r'[a-f0-9]{32}',ident or ''):
        raise ValueError('Identificativo rendiconto non valido')
    row=db.execute('SELECT snapshot FROM tenant_reports WHERE id=?',(ident,)).fetchone()
    if row is None:
        raise ValueError('Rendiconto inesistente')
    return json.loads(row[0])


def make_pdf(data):
    """Tenant-safe ZEUS model A: full physical/allocated quantities and opt-in payment."""
    if data.get('render_model')!='A1':
        # Older v1.7.14 reports must print exactly in their original design.
        return make_pdf_1714(data)
    from PIL import Image, ImageDraw, ImageFont
    from pathlib import Path
    fontdir=next((p for p in (Path('/usr/share/fonts/dejavu'), Path('/usr/share/fonts/ttf/dejavu'), Path('/usr/share/fonts/truetype/dejavu'))
                  if (p/'DejaVuSans.ttf').is_file() and (p/'DejaVuSans-Bold.ttf').is_file()), None)
    if fontdir is None:
        raise RuntimeError('Font DejaVu non disponibili nel container')
    def font(sz,bold=False):
        return ImageFont.truetype(str(fontdir/('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')),sz)
    dark=(39,62,55);green=(40,89,74);muted=(114,127,117);line=(219,227,219)
    pale=(236,243,239);page_w,page_h=1240,1754
    pages=[]
    def newpage():
        im=Image.new('RGB',(page_w,page_h),'white'); d=ImageDraw.Draw(im)
        d.rounded_rectangle((65,65,158,158),radius=22,fill=green)
        d.line([(88,115),(112,90),(136,115)],fill='white',width=6,joint='curve')
        d.line([(94,111),(94,138),(132,138),(132,111)],fill='white',width=5)
        d.text((182,65),'ZEUS Casa',font=font(54,True),fill=dark)
        d.text((184,129),'GESTIONE IMMOBILI E UTENZE',font=font(18,True),fill=muted)
        d.text((1175,73),'RENDICONTO CONSUMI',font=font(20,True),fill=dark,anchor='ra')
        d.text((1175,112),'N. '+data['label'],font=font(20),fill=green,anchor='ra')
        d.text((1175,142),'Emesso il '+fmtdate(data['issued_on']),font=font(18),fill=muted,anchor='ra')
        d.line([(65,187),(1175,187)],fill=green,width=4)
        pages.append(im)
        return im,d,219
    def txt(d,text,x,y,sz=20,bold=False,color=dark,anchor=None):
        d.text((x,y),str(text),font=font(sz,bold),fill=color,anchor=anchor)
    def fmtdate(s):
        try:return date.fromisoformat(s).strftime('%d/%m/%Y')
        except (TypeError,ValueError):return '—'
    def eur(cents):
        s=f'{Decimal(cents)/100:,.2f}'
        return s.replace(',','T').replace('.',',').replace('T','.')+' €'
    def fit(d,text,x,y,limit,sz=20,bold=False,color=dark):
        text=str(text);ft=font(sz,bold)
        while len(text)>1 and d.textbbox((0,0),text,font=ft)[2]>limit:
            text=text[:-2].rstrip()+'…'
        txt(d,text,x,y,sz,bold,color)
    def wrap(d,text,x,y,limit=1030,sz=19,step=28):
        words=str(text).split();row=''
        for word in words:
            cand=(row+' '+word).strip()
            if row and d.textbbox((0,0),cand,font=font(sz))[2]>limit:
                txt(d,row,x,y,sz); y+=step;row=word
            else:row=cand
        if row:txt(d,row,x,y,sz);y+=step
        return y
    def block_title(d,title,y):
        d.rounded_rectangle((75,y,1165,y+45),radius=9,fill=pale)
        txt(d,title,94,y+11,21,True,green)
        return y+57
    def qty(detail,key):
        v=detail.get(key)
        if v is None or v=='':return 'n.d.'
        return f'{v} {detail.get("unit", "")}'
    im,d,y=newpage()
    # Model A: identity and payment date in the same band.
    d.rounded_rectangle((65,y,1175,y+103),radius=14,fill=(246,248,246))
    for x,label in [(82,'APPARTAMENTO'),(480,'DESTINATARIO'),(885,'SCADENZA PAGAMENTO')]:
        txt(d,label,x,y+16,15,True,green)
    fit(d,data['apartment_name'],82,y+50,355,22,True)
    fit(d,data['tenant_name'],480,y+50,362,22,True)
    txt(d,fmtdate(data['due_on']),887,y+50,23,True)
    y+=119
    d.rounded_rectangle((65,y,1175,y+119),radius=14,outline=line,width=2)
    for x,label in [(87,'TOTALE DI COMPETENZA'),(462,'GIÀ CORRISPOSTO'),(855,'DA VERSARE')]:
        txt(d,label,x,y+18,16,True,muted)
    txt(d,eur(data.get('gross_cents',data['amount_cents'])),87,y+56,35,True)
    txt(d,eur(data.get('already_received_cents',0)),462,y+56,35,True)
    txt(d,eur(data['amount_cents']),855,y+56,37,True,green)
    y+=138
    notes=[]
    for index,e in enumerate(data['entries'],1):
        detail=e.get('consumption_detail') or {}
        has_extra=bool(detail.get('note'))
        card_h=265 if not has_extra else 294
        if y+card_h>1625:
            im,d,y=newpage()
        d.rounded_rectangle((65,y,1175,y+card_h-12),radius=16,outline=line,width=2)
        d.rounded_rectangle((78,y+12,1162,y+59),radius=11,fill=pale)
        name=KINDS.get(e['utility'],e['utility']).upper()
        txt(d,name,98,y+22,23,True,green)
        txt(d,eur(e['amount_cents']),1138,y+22,24,True,green,'ra')
        invoice=e.get('invoice_number') or 'non indicato'
        line_invoice='Fattura n. '+invoice+' del '+fmtdate(e.get('invoice_date'))
        if detail.get('meter_label'):
            line_invoice+='  |  '+detail['meter_label']
        fit(d,line_invoice,92,y+73,1060,17,color=muted)
        if e.get('period_start') or e.get('period_end'):
            txt(d,'Periodo fatturato: '+fmtdate(e.get('period_start'))+' – '+fmtdate(e.get('period_end')),92,y+99,16,color=muted)
        labels=['LETTURA INIZIALE','LETTURA FINALE','CONSUMO RILEVATO','IMPUTATO','DA RIPORTARE']
        xs=[93,310,535,777,972]
        for x,label in zip(xs,labels):txt(d,label,x,y+129,14,True,muted)
        for x,key in zip(xs,('start','end','measured','allocated','carry')):
            fit(d,qty(detail,key),x,y+154,205,21,True)
        d.line([(93,y+191),(1138,y+191)],fill=line,width=2)
        txt(d,'Quota fissa: '+eur(e['fixed_cents']),94,y+209,18)
        txt(d,'Spesa consumi: '+eur(e['variable_cents']),420,y+209,18)
        txt(d,'Totale quota: '+eur(e['amount_cents']),825,y+209,18,True,green)
        if e.get('already_received_cents',0):
            notes.append(f'{name}: già corrisposto {eur(e["already_received_cents"])}; residuo {eur(e["outstanding_cents"])}.')
        if has_extra:
            fit(d,detail['note'],94,y+244,1030,15,color=(145,95,47))
        if (detail.get('measured') is None):
            notes.append(f'{name}: letture del sottocontatore non disponibili o non verificabili per questo documento.')
        if (detail.get('allocated') is None):
            notes.append(f'{name}: consumo imputato e riporto non disponibili senza riconciliazione registrata.')
        y+=card_h+3
    # Notes do not make assumptions about unavailable values.
    if notes:
        note_lines=[]
        for n in notes:
            note_lines.extend(_wrap_char(n,116))
        needed=25+len(note_lines)*24
        if y+needed>1595:im,d,y=newpage()
        for l in note_lines:
            txt(d,l,80,y,15,color=muted);y+=24
        y+=8
    payment=data.get('payment')
    if isinstance(payment,dict) and payment.get('enabled'):
        reason=payment.get('reference') or 'Rendiconto '+data['label']+' - '+data['apartment_name']
        fields=[('Intestatario',payment.get('beneficiary','')),('IBAN',payment.get('iban','')),
                ('Causale',reason)]
        if payment.get('instructions'):fields.append(('Indicazioni',payment['instructions']))
        # Reserve sufficient space; use an additional page rather than squeezing reading columns.
        approx=64+len(fields)*34+sum(max(0,len(v)//95)*28 for _,v in fields)
        if y+approx>1595:im,d,y=newpage()
        d.rounded_rectangle((65,y,1175,y+min(approx+18,420)),radius=14,fill=pale)
        txt(d,'DATI PER IL PAGAMENTO',86,y+12,20,True,green)
        y+=51
        for label,value in fields:
            txt(d,label+':',87,y,17,True)
            y=wrap(d,value,256,y,855,18,27)
            y+=6
    for i,p in enumerate(pages,1):
        d=ImageDraw.Draw(p)
        d.line([(65,1670),(1175,1670)],fill=line,width=2)
        txt(d,'ZEUS Casa · Rendiconto individuale del conduttore',70,1687,16,color=muted)
        txt(d,f'Pagina {i} di {len(pages)}',1175,1687,16,color=muted,anchor='ra')
    output=BytesIO()
    pages[0].save(output,'PDF',save_all=True,append_images=pages[1:],resolution=150)
    return output.getvalue()


def _wrap_char(text,limit):
    """Break only display notes (not accounting values)."""
    words=text.split();lines=[];cur=''
    for word in words:
        cand=(cur+' '+word).strip()
        if cur and len(cand)>limit:
            lines.append(cur);cur=word
        else:cur=cand
    if cur:lines.append(cur)
    return lines


def make_pdf_1714(data):
    """Create a tenant-safe PDF using Pillow (already in the add-on Dockerfile)."""
    from PIL import Image, ImageDraw, ImageFont
    from pathlib import Path
    fontdir=next((p for p in (Path('/usr/share/fonts/dejavu'), Path('/usr/share/fonts/ttf/dejavu'), Path('/usr/share/fonts/truetype/dejavu'))
                  if (p/'DejaVuSans.ttf').is_file() and (p/'DejaVuSans-Bold.ttf').is_file()), None)
    if fontdir is None:
        raise RuntimeError('Font DejaVu non disponibili nel container')
    def font(sz,bold=False):
        f=fontdir/('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')
        return ImageFont.truetype(str(f),sz)
    dark=(39,62,55);green=(40,89,74);muted=(114,127,117);line=(219,227,219)
    W,H=1240,1754
    pages=[]
    def fresh():
        canvas=Image.new('RGB',(W,H),'white');draw=ImageDraw.Draw(canvas)
        draw.rounded_rectangle((65,65,158,158),radius=22,fill=green)
        draw.line([(88,115),(112,90),(136,115)],fill='white',width=6,joint='curve')
        draw.line([(94,111),(94,138),(132,138),(132,111)],fill='white',width=5)
        draw.text((182,65),'ZEUS Casa',font=font(54,True),fill=dark)
        draw.text((184,129),'GESTIONE IMMOBILI E UTENZE',font=font(18,True),fill=muted)
        draw.text((1175,73),'RENDICONTO CONSUMI',font=font(20,True),fill=dark,anchor='ra')
        draw.text((1175,112),'N. '+data['label'],font=font(20),fill=dark,anchor='ra')
        draw.text((1175,142),'Emesso il '+fd(data['issued_on']),font=font(18),fill=muted,anchor='ra')
        draw.line([(65,187),(1175,187)],fill=green,width=5)
        return canvas,draw,220
    def txt(d,text,x,y,sz=23,bold=False,color=dark):d.text((x,y),str(text),fill=color,font=font(sz,bold))
    def section(d,name,y):
        d.rounded_rectangle((65,y,1175,y+52),radius=10,fill=(237,243,237))
        txt(d,name,84,y+11,24,True)
        return y+71
    def fd(s):
        try:return date.fromisoformat(s).strftime('%d/%m/%Y')
        except (TypeError,ValueError):return '—'
    def eur(n):
        n=Decimal(n)/100
        return f'{n:,.2f}'.replace(',','T').replace('.',',').replace('T','.')+' €'
    def wrapped(d,text,x,y,size=20,max_width=1020,step=32):
        f=font(size)
        words=str(text).split(' ');s=''
        for word in words:
            candidate=(s+' '+word).strip()
            if d.textbbox((0,0),candidate,font=f)[2]>max_width and s:
                txt(d,s,x,y,size);y+=step;s=word
            else:s=candidate
        if s:txt(d,s,x,y,size);y+=step
        return y
    canvas,d,y=fresh()
    pages.append(canvas)
    y=section(d,'DATI DEL RENDICONTO',y)
    txt(d,'Appartamento',80,y,20,color=muted)
    txt(d,data['apartment_name'],80,y+31,26,True)
    txt(d,'Conduttore',660,y,20,color=muted)
    txt(d,data['tenant_name'],660,y+31,24,True)
    y+=105
    d.rounded_rectangle((65,y,1175,y+116),radius=14,fill=(239,246,240))
    txt(d,'TOTALE DA VERSARE',89,y+15,20,True)
    txt(d,eur(data['amount_cents']),89,y+49,37,True,green)
    txt(d,'SCADENZA PAGAMENTO',665,y+15,20,True)
    txt(d,fd(data['due_on']),665,y+55,32,True)
    y+=148
    y=section(d,'DETTAGLIO UTENZE',y)
    util_totals={}
    for e in data['entries']:
        u=e['utility'];util_totals[u]=util_totals.get(u,0)+e['amount_cents']
    for u in ('electricity','gas','water'):
        if u not in util_totals:continue
        txt(d,KINDS.get(u,u),80,y,24)
        d.text((1148,y),eur(util_totals[u]),font=font(24,True),anchor='ra',fill=green)
        d.line([(80,y+41),(1150,y+41)],fill=line,width=2)
        y+=63
    y+=15
    y=section(d,'FATTURE COMPRESE NEL RENDICONTO',y)
    for i,e in enumerate(data['entries'],1):
        if y>1350:
            canvas,d,y=fresh();pages.append(canvas);y=section(d,'DETTAGLIO FATTURE (CONTINUA)',y)
        d.rounded_rectangle((75,y,1165,y+245),radius=12,outline=line,width=2)
        txt(d,f'{i}. {KINDS.get(e["utility"],e["utility"])}',95,y+16,24,True)
        txt(d,'Numero fattura: '+(e.get('invoice_number') or 'non indicato'),95,y+58,20)
        txt(d,'Data fattura: '+fd(e.get('invoice_date')),695,y+58,20)
        txt(d,'Quota fissa di competenza',95,y+106,19,color=muted)
        txt(d,eur(e['fixed_cents']),95,y+134,23,True)
        txt(d,'Spesa per consumi',450,y+106,19,color=muted)
        txt(d,eur(e['variable_cents']),450,y+134,23,True)
        txt(d,'Totale quota',845,y+106,19,color=muted)
        txt(d,eur(e['amount_cents']),845,y+134,23,True,green)
        mismatch=((data.get('contract_start') and e.get('period_start') and e['period_start']<data['contract_start']) or
                  (data.get('contract_end') and e.get('period_end') and e['period_end']>data['contract_end']))
        if e.get('already_received_cents',0):
            txt(d,'Già versato: '+eur(e['already_received_cents'])+'   ·   Residuo: '+eur(e['outstanding_cents']),95,y+182,18,True)
        if mismatch:
            txt(d,'Attenzione: il periodo della fattura comprende giorni esterni alla locazione.',95,y+213,15,color=(154,93,31))
        y+=263
    if y>1430:
        canvas,d,y=fresh();pages.append(canvas)
    if data.get('already_received_cents',0):
        txt(d,'Quote complessive: '+eur(data['gross_cents'])+'   ·   Già versato: '+eur(data['already_received_cents']),80,y,18,color=muted)
        y+=41
    d.line([(65,y+7),(1175,y+7)],fill=green,width=3)
    txt(d,'IMPORTO DA VERSARE',80,y+28,27,True)
    d.text((1145,y+28),eur(data['amount_cents']),font=font(28,True),fill=green,anchor='ra')
    for idx,canvas in enumerate(pages,1):
        dr=ImageDraw.Draw(canvas)
        dr.line([(65,1670),(1175,1670)],fill=line,width=2)
        dr.text((70,1686),'Documento ZEUS Casa – Quote di competenza del conduttore',font=font(16),fill=muted)
        dr.text((1175,1686),f'Pagina {idx} di {len(pages)}',font=font(16),fill=muted,anchor='ra')
    output=BytesIO()
    pages[0].save(output,'PDF',save_all=True,append_images=pages[1:],resolution=150.0)
    return output.getvalue()
