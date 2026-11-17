"""Independent loan/debt ledger for Casa Consumi (stdlib only).

Interest convention: ACT/360, fixed nominal annual percentage; date intervals [start,end).
Cash movements affect capital/interest from their value date. At an anniversary,
accrued interest is posted, then any unpaid amount is capitalized only if enabled.
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import calendar
import json
import sqlite3
import uuid

LIMIT_CENTS = 1_000_000_000_00
MONEY = Decimal('0.01')

def today():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('Europe/Rome')).date().isoformat()

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

def ident(): return uuid.uuid4().hex

def checkdate(value, *, required=True):
    if value is None or value == '':
        if not required: return ''
        raise ValueError('Inserire una data.')
    if not isinstance(value,str): raise ValueError('Data non valida.')
    try:
        v=date.fromisoformat(value)
        if v.isoformat()!=value: raise ValueError()
        return v
    except ValueError: raise ValueError('Usare una data valida nel formato AAAA-MM-GG.')

def money(value, *, positive=False, zero_ok=True):
    try:
        d=Decimal(str(value).replace(',','.'))
        if not d.is_finite() or d.as_tuple().exponent < -2 or abs(d)>Decimal(LIMIT_CENTS)/100:
            raise ValueError()
        n=int(d*100)
        if positive and (n<0 or (not zero_ok and n==0)): raise ValueError()
        return n
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError('Importo non valido: usare euro, massimo due decimali.')

def rate(value):
    try:
        d=Decimal(str(value).replace(',','.'))
        if not d.is_finite() or d<0 or d>100 or d.as_tuple().exponent < -4: raise ValueError()
        return str(d)
    except (ValueError,TypeError,InvalidOperation): raise ValueError('Tasso annuo fisso non valido (0–100%, max quattro decimali).')

def st(value, maximum=200, required=False):
    if not isinstance(value,str) or len(value)>maximum or (required and not value.strip()):
        raise ValueError('Campo testuale obbligatorio o troppo lungo.')
    return value.strip()

def anniversary(anchor, nth):
    """Monthly anniversaries always anchored to the original disbursement day."""
    idx=anchor.year*12+(anchor.month-1)+nth
    y, mon = divmod(idx,12)
    mon+=1
    return date(y,mon,min(anchor.day,calendar.monthrange(y,mon)[1]))

def installment_date(loan, number):
    """Contractual due date, with optional first repayment independent of disbursement.

    Legacy loans without first_due_date retain their exact old monthly schedule,
    including month-end clipping of an original day 29/30/31.
    """
    if number < 1:raise ValueError('Numero rata non valido')
    custom=loan.get('first_due_date') or ''
    if custom:
        return anniversary(checkdate(custom), number-1)
    return anniversary(checkdate(loan['disbursement_date']), number)


def first_installment(loan):
    return installment_date(loan,1).isoformat()


def next_installment_index(loan, after):
    """First due date strictly after 'after'; works across long opening balances."""
    after=checkdate(after) if isinstance(after,str) else after
    start=installment_date(loan,1)
    if start>after:return 1
    index=max(1,(after.year-start.year)*12+after.month-start.month)
    while installment_date(loan,index)<=after:
        index+=1
    return index


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS debt_loans (
        id TEXT PRIMARY KEY, label TEXT NOT NULL, lender TEXT NOT NULL,
        lender_type TEXT NOT NULL, property_name TEXT NOT NULL,
        source_mode TEXT NOT NULL CHECK(source_mode IN ('full','opening')),
        original_cents INTEGER NOT NULL CHECK(original_cents>=0),
        opening_principal_cents INTEGER NOT NULL CHECK(opening_principal_cents>=0),
        opening_interest_cents INTEGER NOT NULL DEFAULT 0 CHECK(opening_interest_cents>=0),
        opening_fees_cents INTEGER NOT NULL DEFAULT 0 CHECK(opening_fees_cents>=0),
        disbursement_date TEXT NOT NULL, opening_date TEXT NOT NULL,
        annual_rate TEXT NOT NULL, capitalization TEXT NOT NULL CHECK(capitalization IN ('none','monthly')),
        repayment_plan TEXT NOT NULL CHECK(repayment_plan IN ('free','scheduled','mixed')),
        installment_cents INTEGER NOT NULL DEFAULT 0 CHECK(installment_cents>=0),
        installment_note TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS debt_movements (
        id TEXT PRIMARY KEY, loan_id TEXT NOT NULL REFERENCES debt_loans(id),
        date TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN
        ('principal_payment','interest_payment','disbursement','fee','fee_payment')),
        amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
        reference TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS debt_movement_order ON debt_movements(loan_id,date,created_at,id);
    CREATE TABLE IF NOT EXISTS debt_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
        action TEXT NOT NULL, details TEXT NOT NULL);
    ''')
    db.executescript("""
    CREATE TABLE IF NOT EXISTS debt_plan_revisions (
      id TEXT PRIMARY KEY, loan_id TEXT NOT NULL REFERENCES debt_loans(id),
      effective_date TEXT NOT NULL, annual_rate TEXT NOT NULL,
      repayment_plan TEXT NOT NULL CHECK(repayment_plan IN ('free','scheduled','mixed')),
      installment_kind TEXT NOT NULL CHECK(installment_kind IN ('principal','fixed_total')),
      installment_cents INTEGER NOT NULL CHECK(installment_cents>=0),
      installment_note TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL,
      created_at TEXT NOT NULL, UNIQUE(loan_id,effective_date));
    CREATE INDEX IF NOT EXISTS debt_revisions_loan_day ON debt_plan_revisions(loan_id,effective_date);
    CREATE TABLE IF NOT EXISTS debt_plan_revision_audit (
      id TEXT PRIMARY KEY, loan_id TEXT NOT NULL, at TEXT NOT NULL,
      action TEXT NOT NULL, old_data TEXT NOT NULL, new_data TEXT NOT NULL);
    """)
    # Additive migration: preserve all existing debt_loans, movements and references.
    cols={r[1] for r in db.execute('PRAGMA table_info(debt_loans)')}
    if 'installment_kind' not in cols:
        db.execute("ALTER TABLE debt_loans ADD COLUMN installment_kind TEXT NOT NULL DEFAULT 'principal'")
    if 'first_due_date' not in cols:
        db.execute("ALTER TABLE debt_loans ADD COLUMN first_due_date TEXT NOT NULL DEFAULT ''")

def audit(db,action,details):
    db.execute('INSERT INTO debt_events(at,action,details) VALUES(?,?,?)',(now(),action,details[:900]))

PLAN_FIELDS=('annual_rate','repayment_plan','installment_kind','installment_cents','installment_note')

def plan_on(loan, when):
    """Plan agreed for a given value date; original terms never overwritten."""
    day=checkdate(when).isoformat() if isinstance(when,str) else when.isoformat()
    result={k:loan[k] for k in PLAN_FIELDS}
    for revision in sorted(loan.get('plan_revisions',[]),key=lambda r:r['effective_date']):
        if revision['effective_date']>day:break
        result.update({k:revision[k] for k in PLAN_FIELDS})
    return result

def next_plan_date(loan, after=None):
    """Next contractual repayment date, strictly after the reference date."""
    after=checkdate(after or today())
    return installment_date(loan,next_installment_index(loan,after)).isoformat()

def interest_for_period(loan, amount_cents, start_day, end_day):
    """Actual days / 360 with any annual-rate revisions inside the interval."""
    start=checkdate(start_day) if isinstance(start_day,str) else start_day
    end=checkdate(end_day) if isinstance(end_day,str) else end_day
    if end<=start:return 0
    cuts=[start]+[checkdate(r['effective_date']) for r in loan.get('plan_revisions',[]) if start<checkdate(r['effective_date'])<end]+[end]
    cuts=sorted(set(cuts)); earned=Decimal(0)
    for a,b in zip(cuts,cuts[1:]):
        earned+=Decimal(amount_cents)*Decimal(plan_on(loan,a)['annual_rate'])/Decimal(100)*Decimal((b-a).days)/Decimal(360)
    return _rounded(earned)

def loans(db):
    cols='id,label,lender,lender_type,property_name,source_mode,original_cents,opening_principal_cents,opening_interest_cents,opening_fees_cents,disbursement_date,opening_date,annual_rate,capitalization,repayment_plan,installment_cents,installment_note,notes,created_at,installment_kind,first_due_date'
    names=cols.split(',')
    results=[dict(zip(names,r)) for r in db.execute('SELECT '+cols+' FROM debt_loans ORDER BY created_at,id')]
    rev_cols='id,loan_id,effective_date,annual_rate,repayment_plan,installment_kind,installment_cents,installment_note,reason,created_at'
    revisions={}
    for row in db.execute('SELECT '+rev_cols+' FROM debt_plan_revisions ORDER BY effective_date'):
        entry=dict(zip(rev_cols.split(','),row));revisions.setdefault(entry['loan_id'],[]).append(entry)
    for loan in results:
        loan['plan_revisions']=revisions.get(loan['id'],[])
        loan['first_payment_date']=first_installment(loan)
        loan['current_plan']=plan_on(loan,today())
        loan['next_plan_date']=next_plan_date(loan)
    return results

def movements(db, loan_id=None):
    names=['id','loan_id','date','kind','amount_cents','reference','note','created_at']
    clause=' WHERE loan_id=?' if loan_id else ''
    rows=db.execute('SELECT '+','.join(names)+' FROM debt_movements'+clause+' ORDER BY date,created_at,id', (loan_id,) if loan_id else ())
    return [dict(zip(names,row)) for row in rows]

def _rounded(value):return int(Decimal(value).quantize(Decimal('1'),rounding=ROUND_HALF_UP))

def ledger(loan, operations, as_of):
    """Project to as_of date, incl. closed periods and latest unfinished partial period.

    Interest is earned on [prev_day, event_day) and posted on each anniversary.
    On the processing day: interest payments, other transactions, then capitalization.
    """
    asof=checkdate(as_of)
    start=checkdate(loan['opening_date']); anchor=checkdate(loan['disbursement_date'])
    if asof < start: raise ValueError('La data di consultazione precede la posizione iniziale del finanziamento.')
    if anchor > start:raise ValueError('La data del finanziamento non può seguire quella del saldo di apertura.')
    op=[m for m in operations if start <= checkdate(m['date']) <= asof]
    op.sort(key=lambda m:(m['date'],m.get('created_at',''),m['id']))
    # Events with a value date on 'asof' must be included, while interest is earned up to midnight asof.
    events={}
    for m in op:events.setdefault(date.fromisoformat(m['date']),[]).append(m)
    principal=loan['opening_principal_cents']; interest_due=loan['opening_interest_cents']; fees_due=loan['opening_fees_cents']
    repaid=loan['original_cents']-loan['opening_principal_cents'] if loan['source_mode']=='opening' else 0
    disbursed=loan['original_cents']
    if repaid<0:raise ValueError('Saldo iniziale superiore al capitale erogato.')
    earned=interest_due; paid_interest=0; capitalized=0; paid_fees=0; charged_fees=fees_due
    paid_principal=repaid
    d=start; rate_decimal=Decimal(plan_on(loan,start)['annual_rate'])/Decimal(100)
    intervals=[]
    # Interest postings follow contractual installment anniversaries. The first
    # period runs from disbursement/opening date to the agreed first due date.
    idx=next_installment_index(loan,start)
    next_ann=installment_date(loan,idx)
    start_period=start; period_initial=principal
    interest_raw=Decimal(0)
    row={'principal_start':principal,'principal_paid':0,'new_disbursement':0,
         'interest_paid':0,'fee_added':0,'fee_paid':0,'capitalized':0}
    revision_days={checkdate(r['effective_date']) for r in loan.get('plan_revisions',[]) if start<checkdate(r['effective_date'])<=asof}
    points=sorted(set(events)|{asof}|revision_days|set(installment_date(loan,k) for k in range(idx,idx+1201) if installment_date(loan,k)<=asof))
    # prevent many years of excessive computation; asof max 100 years from start
    if (asof-start).days>36525:raise ValueError('Periodo oltre 100 anni: non supportato.')
    for point in points:
        if point<d:continue
        elapsed=(point-d).days
        if elapsed:
            interest_raw+=Decimal(principal)*rate_decimal*Decimal(elapsed)/Decimal(360)
        d=point
        # Interest accrued up to the anniversary is recognized before postings on that day.
        # Thus an interest payment dated at the anniversary can settle current-period interest.
        boundary=point==next_ann
        if boundary:
            preinterest=_rounded(interest_raw)
            interest_due+=preinterest;earned+=preinterest;interest_raw=Decimal(0)
        for mv in events.get(point,[]):
            amount=mv['amount_cents'];kind=mv['kind']
            if kind=='principal_payment':
                if amount>principal:raise ValueError('Rimborso di capitale superiore al residuo alla data '+point.isoformat())
                principal-=amount;paid_principal+=amount;row['principal_paid']+=amount
            elif kind=='disbursement':
                principal+=amount;disbursed+=amount;row['new_disbursement']+=amount
            elif kind=='interest_payment':
                avail=interest_due+_rounded(interest_raw)
                if amount>avail:raise ValueError('Pagamento interessi superiore al maturato alla data '+point.isoformat())
                interest_due-=amount
                paid_interest+=amount;row['interest_paid']+=amount
                # Payments in partially accrued month are applied immediately to owed interest.
                # Negative interest_due is permitted temporarily and offset by eventual accrual.
            elif kind=='fee':fees_due+=amount;charged_fees+=amount;row['fee_added']+=amount
            elif kind=='fee_payment':
                if amount>fees_due:raise ValueError('Pagamento spese superiore al dovuto alla data '+point.isoformat())
                fees_due-=amount;paid_fees+=amount;row['fee_paid']+=amount
            else: raise ValueError('Operazione finanziaria sconosciuta.')
            if principal>LIMIT_CENTS or abs(interest_due)>LIMIT_CENTS or fees_due>LIMIT_CENTS:raise ValueError('Valore complessivo troppo elevato.')
        if point in revision_days:
            rate_decimal=Decimal(plan_on(loan,point)['annual_rate'])/Decimal(100)
        if boundary:
            # Boundary's interest was already added to due; do not add again.
            nextrow=dict(row)
            if loan['capitalization']=='monthly' and interest_due>0:
                capitalized+=interest_due;principal+=interest_due
                row['capitalized']+=interest_due;interest_due=0
            nextrow=dict(row)
            period_plan=plan_on(loan,point)
            nextrow.update({'start':start_period.isoformat(),'end':point.isoformat(),
                 'closed':True,'interest_cents':preinterest,
                 'scheduled_principal_cents':period_plan['installment_cents'] if period_plan['repayment_plan'] != 'free' and period_plan['installment_kind']=='principal' else 0,
                 'scheduled_total_cents':period_plan['installment_cents'] if period_plan['repayment_plan'] != 'free' and period_plan['installment_kind']=='fixed_total' else 0,
                 'principal_end':principal,'interest_due_end':interest_due,'fees_due_end':fees_due})
            intervals.append(nextrow)
            start_period=point;row={'principal_start':principal,'principal_paid':0,'new_disbursement':0,
                'interest_paid':0,'fee_added':0,'fee_paid':0,'capitalized':0}
            idx+=1;next_ann=installment_date(loan,idx)
    # Partial interest: not posted, shown separately as accrued/estimated until next anniversary.
    partial=_rounded(interest_raw)
    if start_period < asof or (not intervals and op):
        partialrow=dict(row)
        partialrow.update({'start':start_period.isoformat(),'end':asof.isoformat(),
            'closed':False,'interest_cents':partial,'scheduled_principal_cents':0,'scheduled_total_cents':0,'principal_end':principal,
            'interest_due_end':interest_due+partial,'fees_due_end':fees_due})
        intervals.append(partialrow)
    current_interest=interest_due+partial
    if current_interest < 0:
        # Payments can be entered for unposted interest of the current period.
        # The user-facing total should include recognized and accrued interest.
        raise ValueError('Pagamento interessi superiore a quelli maturati')
    return {'as_of':asof.isoformat(),'principal_cents':principal,'initial_cents':loan['original_cents'],
        'disbursed_cents':disbursed,'principal_repaid_cents':paid_principal,
        'interest_due_cents':current_interest,'interest_pending_cents':partial,
        'interest_earned_cents':earned+partial,'interest_paid_cents':paid_interest,
        'capitalized_cents':capitalized,'fees_due_cents':fees_due,
        'fees_paid_cents':paid_fees,'fees_charged_cents':charged_fees,
        'total_debt_cents':principal+current_interest+fees_due,
        'periods':intervals, 'next_anniversary':next_ann.isoformat(),
        'mode':loan['source_mode']}

def get_all(db, at=None):
    cutoff=at or today();checkdate(cutoff)
    all_loans=loans(db);ops=movements(db)
    finance_links={}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='finance_debt_links'").fetchone():
        finance_links={row[0]:{'apartment':row[1],'start_date':row[2]} for row in db.execute('SELECT loan_id,apartment,start_date FROM finance_debt_links')}
    result=[]
    for loan in all_loans:
        try:
            snapshot=ledger(loan,[m for m in ops if m['loan_id']==loan['id']],cutoff)
        except ValueError as ex:
            if date.fromisoformat(loan['opening_date'])>date.fromisoformat(cutoff):
                snapshot=None
            else: raise
        # Read-only UI metadata; financial histories are stored in Finance's own table.
        link=finance_links.get(loan['id'])
        public_loan=dict(loan, finance_linked=bool(link),
                         finance_apartment=(link['apartment'] if link else 'common'),
                         finance_link_start_date=(link['start_date'] if link else ''))
        result.append({'loan':public_loan,'balance':snapshot})
    keys=('principal_cents','disbursed_cents','principal_repaid_cents','interest_due_cents',
          'interest_earned_cents','interest_paid_cents','capitalized_cents','fees_due_cents','fees_paid_cents','total_debt_cents')
    total={k:sum((r['balance'] or {}).get(k,0) for r in result) for k in keys}
    return {'loans':result,'movements':ops,'total':total,'as_of':cutoff}

def mutate(db,path,body):
    if path=='/api/debts/loan':
        label=st(body.get('label',''),120,True)
        lender=st(body.get('lender',''),160,True)
        lender_type=body.get('lender_type')
        if lender_type not in ('persona','banca','ente','altro'):raise ValueError('Tipo finanziatore non valido')
        property_name=st(body.get('property_name',''),160,True)
        mode=body.get('source_mode','full')
        if mode not in ('full','opening'):raise ValueError('Modalità iniziale non valida')
        initial=money(body.get('original'),positive=True,zero_ok=False)
        principal=initial if mode=='full' else money(body.get('opening_principal'),positive=True)
        if principal>initial:raise ValueError('Capitale residuo iniziale superiore al totale erogato noto')
        opening_interest=money(body.get('opening_interest','0'),positive=True) if mode=='opening' else 0
        opening_fees=money(body.get('opening_fees','0'),positive=True) if mode=='opening' else 0
        orig=checkdate(body.get('disbursement_date'))
        opened=checkdate(body.get('opening_date')) if mode=='opening' else orig
        if orig>opened:raise ValueError('La data del saldo non può precedere l’erogazione')
        cap=body.get('capitalization','none')
        if cap not in ('none','monthly'):raise ValueError('Gestione interessi non valida')
        plan=body.get('repayment_plan','free')
        if plan not in ('free','scheduled','mixed'):raise ValueError('Piano di rimborso non valido')
        inst=money(body.get('installment','0'),positive=True) if plan!='free' else 0
        if plan!='free' and inst==0:raise ValueError('Inserire l’importo della rata prevista.')
        installment_kind=body.get('installment_kind','principal')
        if installment_kind not in ('principal','fixed_total'):
            raise ValueError('Modalità di rata non valida')
        if plan=='free':installment_kind='principal'
        first_due_date=checkdate(body.get('first_due_date'),required=False)
        if first_due_date and first_due_date<=orig:
            raise ValueError('La prima rata deve essere successiva alla data di erogazione')
        if installment_kind=='fixed_total':
            if cap=='monthly':raise ValueError('Per la rata fissa scegli interessi non capitalizzati: piano a capitalizzazione mensile non supportato')
            # ACT/360 interest on the first actual interval must be covered by the payment.
            first_due=first_due_date or anniversary(orig,1)
            projected=_rounded(Decimal(principal)*Decimal(rate(body.get('annual_rate')))/Decimal(100)*Decimal((first_due-orig).days)/Decimal(360))
            if inst<=projected:
                raise ValueError('Rata fissa insufficiente per coprire gli interessi iniziali ACT/360')
        uid=ident()
        db.execute('''INSERT INTO debt_loans (id,label,lender,lender_type,property_name,source_mode,original_cents,opening_principal_cents,opening_interest_cents,opening_fees_cents,disbursement_date,opening_date,annual_rate,capitalization,repayment_plan,installment_cents,installment_note,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (uid,label,lender,lender_type,property_name,mode,initial,principal,opening_interest,opening_fees,
             orig.isoformat(),opened.isoformat(),rate(body.get('annual_rate')),cap,plan,inst,
             st(body.get('installment_note',''),160),st(body.get('notes',''),1000),now()))
        db.execute('UPDATE debt_loans SET installment_kind=?,first_due_date=? WHERE id=?',
                   (installment_kind,first_due_date.isoformat() if first_due_date else '',uid))
        # Explicit one-step opt-in: loan plan entered once in Gestione del Debito.
        # Keep property-only Finance isolated from unrelated loans.
        if body.get('finance_link') in ('yes', 'on', True):
            import finance
            finance.mutate(db,'/api/finance/debt/link',{
                'loan_id':uid,'apartment':body.get('finance_apartment','common'),
                'start_date':opened.isoformat(), 'first_due':'','monthly_principal':''})
        audit(db,'prestito_creato',uid+' '+label)
        return {'id':uid}
    if path=='/api/debts/loan/finance':
        uid=st(body.get('id',''),64,True)
        loan=next((item for item in loans(db) if item['id']==uid),None)
        if loan is None:raise ValueError('Finanziamento non trovato')
        enabled=body.get('finance_link') in ('yes','on',True)
        link=db.execute('SELECT apartment,start_date FROM finance_debt_links WHERE loan_id=?',(uid,)).fetchone()
        import finance
        if enabled:
            apartment=body.get('finance_apartment','common')
            if apartment not in ('0','1','common'):
                raise ValueError('Attribuzione ZEUS Finance non valida')
            if link:
                # Only update the attribution. The historical start-date, overrides
                # and settlements must never be overwritten by a checkbox edit.
                if link[0]!=apartment:
                    db.execute('UPDATE finance_debt_links SET apartment=?,updated_at=? WHERE loan_id=?',
                               (apartment,now(),uid))
                    audit(db,'finance_attribuzione_modificata',uid+' '+apartment)
                return {'ok':True,'linked':True}
            # Avoid generating fictional overdue installments for dates before opt-in.
            start=max(checkdate(loan['opening_date']),checkdate(today())).isoformat()
            finance.mutate(db,'/api/finance/debt/link',{
                'loan_id':uid,'apartment':apartment,'start_date':start,
                'first_due':'','monthly_principal':''})
            audit(db,'finance_collegamento_attivato',uid+' '+start+' '+apartment)
            return {'ok':True,'linked':True}
        if link:
            # Existing safeguards refuse unlink while Finance settlements or
            # skipped installments exist; entire update remains transactional.
            finance.mutate(db,'/api/finance/debt/unlink',{'loan_id':uid})
            audit(db,'finance_collegamento_disattivato',uid)
        return {'ok':True,'linked':False}
    if path=='/api/debts/loan/plan/update':
        uid=st(body.get('id',''),64,True)
        loan=next((l for l in loans(db) if l['id']==uid),None)
        if loan is None:raise ValueError('Finanziamento non trovato')
        effective=checkdate(body.get('effective_date')).isoformat()
        previous_first=loan.get('first_due_date') or ''
        requested_first=(checkdate(body.get('first_due_date',previous_first),required=False))
        requested_first=requested_first.isoformat() if requested_first else ''
        # The user may adjust the initial payment calendar before any original
        # installment becomes due. Changing a historical calendar would
        # retrospectively change interest postings and is therefore refused.
        if not previous_first and requested_first==first_installment(loan):
            requested_first='' # Legacy first due is already the original anniversary.
        calendar_changed=requested_first!=previous_first
        updated_loan=dict(loan,first_due_date=requested_first)
        if calendar_changed:
            if not requested_first or checkdate(requested_first)<=checkdate(loan['disbursement_date']):
                raise ValueError('Inserire la prima rata successiva alla data di erogazione')
            if checkdate(requested_first)<=checkdate(today()) or installment_date(loan,1)<=checkdate(today()):
                raise ValueError('La prima rata può essere corretta solo prima della prima scadenza originaria. Nessuna ricorrenza trascorsa viene modificata.')
            if db.execute('SELECT 1 FROM debt_movements WHERE loan_id=? LIMIT 1',(uid,)).fetchone():
                raise ValueError('Sono presenti movimenti registrati: non è consentito cambiare retroattivamente il calendario delle rate')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='finance_debt_settlements'").fetchone():
                if db.execute('SELECT 1 FROM finance_debt_settlements WHERE loan_id=? LIMIT 1',(uid,)).fetchone():
                    raise ValueError('Ci sono rate confermate in ZEUS Finance: non cambiare il calendario storico')
                if db.execute('SELECT 1 FROM finance_debt_skips WHERE loan_id=? LIMIT 1',(uid,)).fetchone():
                    raise ValueError('Ci sono rate saltate in ZEUS Finance: non cambiare il calendario storico')
        earliest=next_plan_date(updated_loan)
        if effective<earliest:raise ValueError('La modifica può decorrere solo dalla prossima rata futura: '+earliest)
        idx=next_installment_index(updated_loan,checkdate(effective))
        if effective!=installment_date(updated_loan,max(1,idx-1)).isoformat():
            raise ValueError('La decorrenza deve coincidere con una ricorrenza del piano di rimborso (giorno della prima rata).')
        if db.execute('SELECT 1 FROM debt_movements WHERE loan_id=? AND date>=? LIMIT 1',(uid,effective)).fetchone():
            raise ValueError('Esistono movimenti con data uguale o successiva alla decorrenza: non modificare il piano che li riguarda')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='finance_debt_settlements'").fetchone():
            if db.execute('SELECT 1 FROM finance_debt_settlements WHERE loan_id=? AND occurrence_date>=? LIMIT 1',(uid,effective)).fetchone():
                raise ValueError('Esistono rate già confermate dalla decorrenza prevista')
        plan=body.get('repayment_plan','free')
        if plan not in ('free','scheduled','mixed'):raise ValueError('Piano di rimborso non valido')
        kind=body.get('installment_kind','principal')
        if kind not in ('principal','fixed_total'):raise ValueError('Modalità rata non valida')
        if plan=='free':kind='principal'
        monthly=money(body.get('installment','0') or '0',positive=True)
        if plan=='free':monthly=0
        elif monthly<=0:raise ValueError('Inserire una rata mensile positiva')
        apr=rate(body.get('annual_rate'))
        note=st(body.get('installment_note',''),160)
        reason=st(body.get('reason',''),500,True)
        if kind=='fixed_total':
            if loan['capitalization']=='monthly':raise ValueError('La rata totale fissa non è compatibile con la capitalizzazione mensile')
            due=installment_date(updated_loan,idx)
            snapshot=ledger(updated_loan,movements(db,uid),effective)
            first_interest=interest_for_period(dict(updated_loan,plan_revisions=[r for r in loan.get('plan_revisions',[]) if r['effective_date']<effective]+[{'effective_date':effective,'annual_rate':apr,'repayment_plan':plan,'installment_kind':kind,'installment_cents':monthly,'installment_note':note}]), snapshot['principal_cents'],effective,due)
            if monthly<=first_interest:
                raise ValueError('La nuova rata totale fissa non copre gli interessi previsti per il primo mese')
        # Overwrite only future pending revisions. Preserve an append-only audit of replacements.
        # Changing the due day also invalidates any old *future* revision on
        # the previous monthly schedule (e.g. 14/11 vs new 15/11).
        replace_from=today() if calendar_changed else effective
        old=[r for r in loan['plan_revisions'] if r['effective_date']>=replace_from]
        updated=dict(effective_date=effective,annual_rate=apr,repayment_plan=plan,
                     installment_kind=kind,installment_cents=monthly,installment_note=note,reason=reason)
        if calendar_changed:
            db.execute('UPDATE debt_loans SET first_due_date=? WHERE id=?',(requested_first,uid))
            # Finance must inherit the authoritative Debt repayment calendar.
            # No paid/skipped installments exist by the guards above.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='finance_debt_links'").fetchone():
                db.execute("UPDATE finance_debt_links SET first_due='',updated_at=? WHERE loan_id=?",(now(),uid))
            updated['previous_first_due_date']=first_installment(loan)
            updated['first_due_date']=requested_first
        db.execute('DELETE FROM debt_plan_revisions WHERE loan_id=? AND effective_date>=?',(uid,replace_from))
        db.execute('''INSERT INTO debt_plan_revisions
            (id,loan_id,effective_date,annual_rate,repayment_plan,installment_kind,installment_cents,installment_note,reason,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',(ident(),uid,effective,apr,plan,kind,monthly,note,reason,now()))
        db.execute('INSERT INTO debt_plan_revision_audit VALUES(?,?,?,?,?,?)',
                   (ident(),uid,now(),'piano_modificato',json.dumps(old,ensure_ascii=False),json.dumps(updated,ensure_ascii=False)))
        audit(db,'piano_ammortamento_modificato',uid+' dal '+effective+' '+reason[:120])
        return {'ok':True,'id':uid,'effective_date':effective}
    if path=='/api/debts/loan/update':
        uid=st(body.get('id',''),64,True)
        if not db.execute('SELECT 1 FROM debt_loans WHERE id=?',(uid,)).fetchone():
            raise ValueError('Finanziamento non trovato')
        label=st(body.get('label',''),120,True)
        lender=st(body.get('lender',''),160,True)
        lender_type=body.get('lender_type')
        if lender_type not in ('persona','banca','ente','altro'):raise ValueError('Tipo finanziatore non valido')
        place=st(body.get('property_name',''),160,True)
        notes=st(body.get('notes',''),1000)
        db.execute('UPDATE debt_loans SET label=?, lender=?, lender_type=?, property_name=?, notes=? WHERE id=?',
            (label,lender,lender_type,place,notes,uid))
        audit(db,'anagrafica_prestito_modificata',uid)
        return {'ok':True}
    if path=='/api/debts/loan/delete':
        uid=st(body.get('id',''),64,True)
        if db.execute('SELECT 1 FROM debt_movements WHERE loan_id=? LIMIT 1',(uid,)).fetchone():
            raise ValueError('Prestito con movimenti registrati: non eliminabile.')
        db.execute('DELETE FROM debt_plan_revisions WHERE loan_id=?',(uid,))
        if db.execute('DELETE FROM debt_loans WHERE id=?',(uid,)).rowcount!=1:
            raise ValueError('Finanziamento non trovato')
        audit(db,'prestito_senza_movimenti_eliminato',uid)
        return {'ok':True}
    if path=='/api/debts/movement':
        loan_id=st(body.get('loan_id',''),64,True)
        records={p['id']:p for p in loans(db)}
        if loan_id not in records:raise ValueError('Finanziamento non trovato')
        when=checkdate(body.get('date'))
        loan=records[loan_id]
        if when < checkdate(loan['opening_date']):raise ValueError('Movimento precedente alla data di apertura')
        if when > checkdate(today()):raise ValueError('Non registrare bonifici con data valuta futura')
        kind=body.get('kind')
        if kind not in ('principal_payment','interest_payment','disbursement','fee','fee_payment'):
            raise ValueError('Tipologia di movimento non valida')
        amount=money(body.get('amount'),positive=True,zero_ok=False)
        ref=st(body.get('reference',''),160)
        note=st(body.get('note',''),1000)
        uid=ident()
        db.execute('INSERT INTO debt_movements VALUES(?,?,?,?,?,?,?,?)',(uid,loan_id,when.isoformat(),kind,amount,ref,note,now()))
        ledger(loan,movements(db,loan_id),today())
        audit(db,'movimento_debito_creato',loan_id+' '+kind+' '+str(amount)+' '+when.isoformat())
        return {'id':uid}
    if path=='/api/debts/movement/delete':
        uid=st(body.get('id',''),64,True)
        mv=db.execute('SELECT loan_id FROM debt_movements WHERE id=?',(uid,)).fetchone()
        if not mv:raise ValueError('Movimento non trovato')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='finance_debt_settlements'").fetchone():
            if db.execute("SELECT 1 FROM finance_debt_settlements WHERE movement_ids LIKE ? LIMIT 1", ('%"'+uid+'"%',)).fetchone():
                raise ValueError('Pagamento registrato da ZEUS Finance: annullalo dalla sezione Finance.')
        db.execute('DELETE FROM debt_movements WHERE id=?',(uid,))
        l=next(r for r in loans(db) if r['id']==mv[0])
        ledger(l,movements(db,mv[0]),today())
        audit(db,'movimento_debito_eliminato',uid)
        return {'ok':True}
    raise ValueError('Operazione non supportata')
