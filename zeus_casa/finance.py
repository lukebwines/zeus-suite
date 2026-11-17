"""ZEUS Finance: property-only cash ledger and transparent 12-month projection.

Not a tax calculator. Utilities reimbursed by tenants are excluded from the ledger.
All amounts stored in integer euro cents. Recurrences are a forecast, NOT cash
movements; only explicit settlement inserts into the actual cash ledger.
"""
from calendar import monthrange
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import re
import secrets
import debts

KINDS = {'income', 'expense', 'capital_in', 'capital_out'}
FREQUENCIES = {'once': 0, 'monthly': 1, 'quarterly': 3, 'semiannual': 6, 'annual': 12}
PLACES = {'0', '1', 'common'}
RE_UTILITIES = re.compile(r'(?:^|\W)(bollett[ae]|luce|elettricit[aà]|gas|acqua|utenze|partite di giro)(?:\W|$)', re.I)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS finance_entries (
        id TEXT PRIMARY KEY, entry_date TEXT NOT NULL, kind TEXT NOT NULL,
        apartment TEXT NOT NULL, category TEXT NOT NULL, label TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
        schedule_id TEXT, occurrence_date TEXT, notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    # Nullable for historical movements: never fabricate an undocumented share.
    if 'common_share_ap1' not in {c[1] for c in db.execute('PRAGMA table_info(finance_entries)')}:
        db.execute('ALTER TABLE finance_entries ADD COLUMN common_share_ap1 INTEGER CHECK(common_share_ap1 BETWEEN 0 AND 100)')
    db.execute('''CREATE TABLE IF NOT EXISTS finance_schedules (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, apartment TEXT NOT NULL,
        category TEXT NOT NULL, label TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
        frequency TEXT NOT NULL, day INTEGER NOT NULL CHECK(day BETWEEN 1 AND 31),
        start_date TEXT NOT NULL, end_date TEXT,
        tenant_id TEXT, tenant_label TEXT, notes TEXT NOT NULL DEFAULT '',
        common_share_ap1 INTEGER NOT NULL DEFAULT 50 CHECK(common_share_ap1 BETWEEN 0 AND 100),
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS finance_one_settlement_per_due
        ON finance_entries(schedule_id, occurrence_date)
        WHERE schedule_id IS NOT NULL AND occurrence_date IS NOT NULL''')
    db.execute('''CREATE TABLE IF NOT EXISTS finance_entry_audit (
        id TEXT PRIMARY KEY, entry_id TEXT NOT NULL, original_json TEXT NOT NULL,
        action TEXT NOT NULL, reason TEXT NOT NULL, recorded_at TEXT NOT NULL)''')
    # Additive migration: retains all existing schedules, forecasts and settlements.
    columns={r[1] for r in db.execute('PRAGMA table_info(finance_schedules)')}
    if 'replaces_schedule_id' not in columns:
        db.execute('ALTER TABLE finance_schedules ADD COLUMN replaces_schedule_id TEXT')
    if 'superseded_by' not in columns:
        db.execute('ALTER TABLE finance_schedules ADD COLUMN superseded_by TEXT')
    db.execute('''CREATE TABLE IF NOT EXISTS finance_schedule_audit (
        id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, successor_id TEXT,
        before_json TEXT NOT NULL, after_json TEXT NOT NULL,
        action TEXT NOT NULL, recorded_at TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS finance_schedule_audit_schedule ON finance_schedule_audit(schedule_id,recorded_at)')
    db.execute('CREATE INDEX IF NOT EXISTS finance_entry_dates ON finance_entries(entry_date)')
    db.execute('CREATE INDEX IF NOT EXISTS finance_schedules_active ON finance_schedules(active,start_date)')
    db.execute('''CREATE TABLE IF NOT EXISTS finance_debt_settlements (
        loan_id TEXT NOT NULL, occurrence_date TEXT NOT NULL, payment_date TEXT NOT NULL,
        principal_cents INTEGER NOT NULL, interest_cents INTEGER NOT NULL, fee_cents INTEGER NOT NULL,
        movement_ids TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        PRIMARY KEY(loan_id, occurrence_date))''')
    db.execute('''CREATE TABLE IF NOT EXISTS finance_debt_settlement_audit (
        id TEXT PRIMARY KEY, loan_id TEXT NOT NULL, occurrence_date TEXT NOT NULL,
        snapshot_json TEXT NOT NULL, reason TEXT NOT NULL, recorded_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS finance_debt_skips (
        loan_id TEXT NOT NULL, occurrence_date TEXT NOT NULL, reason TEXT NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY(loan_id,occurrence_date))''')
    db.execute('''CREATE TABLE IF NOT EXISTS finance_debt_skip_audit (
        id TEXT PRIMARY KEY, loan_id TEXT NOT NULL, occurrence_date TEXT NOT NULL,
        action TEXT NOT NULL, reason TEXT NOT NULL, recorded_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS finance_debt_links (
        loan_id TEXT PRIMARY KEY, apartment TEXT NOT NULL,
        start_date TEXT NOT NULL, first_due TEXT,
        principal_installment_cents INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    # A settled schedule carries its explicit historical share; do not infer the
    # percentage of unrelated legacy entries.
    db.execute('''UPDATE finance_entries SET common_share_ap1=(
        SELECT common_share_ap1 FROM finance_schedules
        WHERE finance_schedules.id=finance_entries.schedule_id)
        WHERE common_share_ap1 IS NULL AND schedule_id IS NOT NULL
        AND apartment='common' AND EXISTS(
        SELECT 1 FROM finance_schedules WHERE id=finance_entries.schedule_id)''')
    import finance_balance
    finance_balance.initialize(db)


def _date(s, field, nullable=False):
    if nullable and (s is None or s == ''):
        return None
    if not isinstance(s,str): raise ValueError(f'{field}: data non valida')
    try:
        d=date.fromisoformat(s)
    except ValueError:
        raise ValueError(f'{field}: usa AAAA-MM-GG') from None
    if d.isoformat()!=s: raise ValueError(f'{field}: formato AAAA-MM-GG')
    return s


def _amount(value):
    if isinstance(value, bool):raise ValueError('Importo non valido')
    try:d=Decimal(str(value).replace(',','.'))
    except (InvalidOperation, ValueError):raise ValueError('Importo non valido') from None
    if not d.is_finite() or d<=0 or d>Decimal('100000000') or d*100!=(d*100).to_integral_value():
        raise ValueError('Importo positivo, massimo due decimali')
    return int(d*100)


def _str(value, name, limit=120, required=False):
    if not isinstance(value,str) or len(value)>limit or (required and not value.strip()):
        raise ValueError(f'{name} non valido')
    return value.strip()


def _place(value):
    if value not in PLACES:raise ValueError('Appartamento non valido')
    return value


def _kind(value, schedule=False):
    allowed={'income','expense'} if schedule else KINDS
    if value not in allowed:raise ValueError('Tipologia movimento non valida')
    return value


def _prohibited(category, kind):
    if kind in {'income','expense'} and RE_UTILITIES.search(category):
        raise ValueError('Le utenze rimborsate sono partite di giro: non registrarle in ZEUS Finance. Inserisci solo eventuali spese realmente a carico della proprietà sotto una categoria di costo della proprietà.')


def _row_dicts(db, q, args=()):
    cursor=db.execute(q,args); columns=[x[0] for x in cursor.description]
    return [dict(zip(columns,row)) for row in cursor.fetchall()]


def _tenants(db):
    raw=db.execute('SELECT archive FROM state WHERE id=1').fetchone()
    if not raw:return []
    data=json.loads(raw[0]);return [dict(id=t.get('id'), name=t.get('name'), apartment=str(t.get('apartment')),
                    start=t.get('start') or '', end=t.get('end') or '', active=bool(t.get('active')))
                    for t in data.get('tenants',[]) if t.get('id')]


def _ts():return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _uid():return secrets.token_hex(16)


def _now_date():return date.today().isoformat()


def _common_share(raw):
    if isinstance(raw,bool):raise ValueError('Percentuale di ripartizione non valida')
    try:share=int(raw)
    except (ValueError,TypeError):raise ValueError('Percentuale di ripartizione non valida') from None
    if str(raw).strip() not in (str(share),) or not 0<=share<=100:
        raise ValueError('Quota comune: inserisci una percentuale intera tra 0 e 100')
    return share


def _validate_movement(obj):
    if not isinstance(obj, dict): raise ValueError('Dati del movimento non validi')
    kind=_kind(obj.get('kind'))
    ap=_place(obj.get('apartment'))
    if kind in {'capital_in','capital_out'} and ap!='common':
        raise ValueError('Apporti e prelievi vanno registrati a livello complessivo')
    category=_str(obj.get('category',''), 'Categoria',70,True)
    _prohibited(category, kind)
    return dict(entry_date=_date(obj.get('date'), 'Data movimento'),kind=kind,apartment=ap,
                category=category,label=_str(obj.get('label',''),'Descrizione',180,True),
                amount_cents=_amount(obj.get('amount')),notes=_str(obj.get('notes',''),'Note',800),
                common_share_ap1=_common_share(obj.get('common_share_ap1',50)))


def _validate_schedule(db,obj):
    kind=_kind(obj.get('kind'),True)
    apartment=_place(obj.get('apartment'))
    if obj.get('frequency') not in FREQUENCIES:raise ValueError('Periodicità non valida')
    start=_date(obj.get('start_date'), 'Inizio programma')
    end=_date(obj.get('end_date'), 'Fine programma',True)
    if end and end<start:raise ValueError('La fine precede l’inizio')
    try: day=int(obj.get('day'))
    except (ValueError,TypeError):raise ValueError('Giorno di scadenza non valido') from None
    if not 1<=day<=31:raise ValueError('Giorno di scadenza tra 1 e 31')
    tenant_id=obj.get('tenant_id') or None
    tenant_label=None
    if tenant_id:
        if kind!='income' or category_is_not_rent(obj.get('category','')):
            raise ValueError('Il collegamento al contratto è previsto per i canoni')
        tenants={t['id']:t for t in _tenants(db)}
        t=tenants.get(tenant_id)
        if not t:raise ValueError('Contratto non trovato nella rubrica. Usa un movimento libero.')
        if t['apartment']!=apartment:raise ValueError('Appartamento diverso da quello del contratto')
        if t['start'] and start<t['start']:raise ValueError('La ricorrenza precede la decorrenza del contratto')
        if t['end'] and (not end or end>t['end']):
            raise ValueError('Imposta la fine della ricorrenza non oltre la fine del contratto')
        tenant_label=t['name']
    try:share=int(obj.get('common_share_ap1',50))
    except (ValueError,TypeError):raise ValueError('Quota spese comuni non valida') from None
    if not 0<=share<=100:raise ValueError('Percentuale tra 0 e 100')
    category=_str(obj.get('category',''),'Categoria',70,True)
    _prohibited(category,kind)
    return dict(kind=kind,apartment=apartment,category=category,
                label=_str(obj.get('label',''),'Descrizione',180,True),
                amount_cents=_amount(obj.get('amount')),frequency=obj['frequency'],
                day=day,start_date=start,end_date=end,tenant_id=tenant_id,tenant_label=tenant_label,
                notes=_str(obj.get('notes',''),'Note',800),common_share_ap1=share)


def category_is_not_rent(category):
    return 'canone' not in str(category).lower() and 'affitto' not in str(category).lower()


def _due_dates(schedule, start, end):
    """Generate due dates; anchor repeating month to the *start month*, clipping 29/30/31."""
    base=date.fromisoformat(schedule['start_date']); last=date.fromisoformat(schedule['end_date']) if schedule['end_date'] else end
    if last<start:return
    month_idx=base.year*12+base.month-1
    end_idx=end.year*12+end.month-1
    stride=FREQUENCIES[schedule['frequency']]
    if stride==0:
        if start<=base<=end and base<=last:yield base.isoformat()
        return
    # cap to a practical 50-year horizon, for user-requested forecasts
    for k in range(0,min(620,(end_idx-month_idx)//stride+1)):
        idx=month_idx+k*stride
        y,m=divmod(idx,12); m+=1
        if y>9999:break
        d=date(y,m,min(schedule['day'],monthrange(y,m)[1]))
        if base<=d<=last and start<=d<=end:yield d.isoformat()


def _fiscal_year(year):
    try:y=int(year)
    except (ValueError,TypeError):raise ValueError('Anno non valido') from None
    if not 2000<=y<=2100:raise ValueError('Anno non valido')
    return y


def _linked_loan_data(db):
    links=_row_dicts(db,'SELECT * FROM finance_debt_links ORDER BY created_at')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='debt_loans'").fetchone():return links,{},{}
    loans={l['id']:l for l in debts.loans(db)}
    operations=debts.movements(db)
    by_loan={}
    for op in operations:by_loan.setdefault(op['loan_id'],[]).append(op)
    return links,loans,by_loan


def _debt_cash_actual(links,loans,operations):
    """Only movements dated after explicit Finance linkage, never double-save a debt movement."""
    events=[]
    for link in links:
        loan=loans.get(link['loan_id'])
        if not loan:continue
        for op in operations.get(loan['id'],[]):
            if op['date']<link['start_date']:continue
            kind=op['kind']
            if kind=='fee':continue  # Fees charged != money actually paid
            signed=op['amount_cents'] if kind=='disbursement' else -op['amount_cents']
            events.append(dict(date=op['date'],amount_cents=signed,kind=kind,
                               loan_id=loan['id'],loan_label=loan['label'],apartment=link['apartment']))
    return events


def _payment_date(loan,link,index):
    """Use Debt's payment calendar, except a legacy explicit Finance override."""
    override=link.get('first_due') or ''
    if override:
        return debts.anniversary(date.fromisoformat(override),index-1)
    return debts.installment_date(loan,index)


def _first_payment_index_after(loan,link,day):
    day=day if isinstance(day,date) else date.fromisoformat(day)
    # Legacy Finance override remains supported for previously linked loans.
    if not link.get('first_due'):
        return debts.next_installment_index(loan,day)
    base=date.fromisoformat(link['first_due'])
    n=max(1,(day.year-base.year)*12+day.month-base.month+1)
    while _payment_date(loan,link,n)<=day:n+=1
    return n


def _debt_forecast(links,loans,operations,until,as_of,paid_due=frozenset(),skipped_due=frozenset()):
    """Forecast using date-effective revisions, without modifying the actual ledger."""
    results=[]
    anchor=date.fromisoformat(as_of);limit=date.fromisoformat(until)
    for link in links:
        loan=loans.get(link['loan_id'])
        if not loan:continue
        opened=date.fromisoformat(loan['opening_date'])
        start=max(anchor,opened,date.fromisoformat(link['start_date']))
        if start>=limit:continue
        try: balance=debts.ledger(loan,operations.get(loan['id'],[]),start.isoformat())
        except ValueError:continue
        principal=balance['principal_cents'];due_interest=balance['interest_due_cents'];fees=balance['fees_due_cents']
        if principal<=0 and due_interest<=0 and fees<=0:continue
        idx=_first_payment_index_after(loan,link,start)
        previous=start
        for n in range(1200):
            due=_payment_date(loan,link,idx+n)
            if due>limit:break
            plan=debts.plan_on(loan,due)
            scheduled=plan['repayment_plan']!='free'
            fixed_total=scheduled and plan['installment_kind']=='fixed_total'
            installment=plan['installment_cents'] if scheduled else link['principal_installment_cents']
            earned=debts.interest_for_period(loan,principal,previous,due)
            due_interest+=max(0,earned)
            previous=due
            if (loan['id'],due.isoformat()) in paid_due or (loan['id'],due.isoformat()) in skipped_due:
                continue
            if installment<=0:continue
            interest=min(due_interest,installment) if fixed_total else due_interest
            capital=min(principal,max(0,installment-interest)) if fixed_total else min(principal,installment)
            fee=fees
            if not (capital or interest or fee):break
            results.append(dict(date=due.isoformat(),loan_id=loan['id'],loan_label=loan['label'],
                                apartment=link['apartment'],principal_cents=capital,
                                interest_cents=interest,fee_cents=fee,amount_cents=capital+interest+fee,
                                fixed_total=fixed_total))
            principal-=capital;due_interest-=interest;fees=0
            if principal==0 and due_interest==0:break
    return results


def _overdue_debt(links,loans,operations,as_of,paid_due,skipped_due):
    """Show missed installments, without treating them as cash outflows.

    A payment entered directly in Gestione del Debito isn't silently matched to a
    particular installment: overdue items are flagged for verification.
    """
    overdue=[]; today=date.fromisoformat(as_of)
    for link in links:
        loan=loans.get(link['loan_id'])
        if not loan:continue
        if debts.plan_on(loan,as_of)['repayment_plan']=='free' and not link['principal_installment_cents']:continue
        # A fully extinguished loan must not show obsolete unpaid installments.
        try: bal=debts.ledger(loan,operations.get(loan['id'],[]),as_of)
        except ValueError:continue
        if bal['total_debt_cents']<=0:continue
        begin=date.fromisoformat(link['start_date'])
        for i in range(1,1201):
            due=_payment_date(loan,link,i)
            if due>today:break
            if due<begin:continue
            terms=debts.plan_on(loan,due)
            if terms['repayment_plan']=='free' and not link['principal_installment_cents']:continue
            key=(loan['id'],due.isoformat())
            if key in paid_due or key in skipped_due:continue
            # Don't assert contractual default; just expose a missing confirmation.
            overdue.append(dict(loan_id=loan['id'],loan_label=loan['label'],apartment=link['apartment'],
                                date=due.isoformat(),occurrence_date=due.isoformat(),
                                status='overdue',amount_cents=0,principal_cents=0,interest_cents=0,fee_cents=0))
    return overdue


def _project(db,year):
    y=_fiscal_year(year)
    start=f'{y}-01-01';end=f'{y}-12-31'
    today=_now_date()
    entries=_row_dicts(db,'SELECT * FROM finance_entries ORDER BY entry_date DESC, created_at DESC')
    schedules=_row_dicts(db,'SELECT * FROM finance_schedules ORDER BY label')
    links,loans,ops=_linked_loan_data(db)
    actual_debts=_debt_cash_actual(links,loans,ops)
    import finance_balance
    deposits=finance_balance.deposit_events(db)
    def deposit_signed(r):return r['amount_cents'] if r['kind']=='received' else -r['amount_cents']
    paid_due={(r['loan_id'],r['occurrence_date']) for r in _row_dicts(db,'SELECT loan_id,occurrence_date FROM finance_debt_settlements')}
    skips=_row_dicts(db,'SELECT * FROM finance_debt_skips ORDER BY occurrence_date')
    skipped_due={(r['loan_id'],r['occurrence_date']) for r in skips}
    future_debts=_debt_forecast(links,loans,ops,end,today,paid_due,skipped_due)
    overdue_debts=_overdue_debt(links,loans,ops,today,paid_due,skipped_due)
    # A historical year must show only opening cash actually recorded. For future
    # years carry every prior as-yet-unsettled forecast forward, without converting
    # forecast events to receipts or double-counting settled schedules.
    def signed(r):return r['amount_cents'] if r['kind'] in {'income','capital_in'} else -r['amount_cents']
    opening=sum(signed(r) for r in entries if r['entry_date']<start)
    opening+=sum(r['amount_cents'] for r in actual_debts if r['date']<start)
    opening+=sum(deposit_signed(r) for r in deposits if r['event_date']<start)
    months=[dict(month=f'{y}-{m:02d}',income_actual=0,expense_actual=0,
                 income_expected=0,expense_expected=0,capital_net=0,capital_in_actual=0,capital_out_actual=0,
                 debt_actual=0,debt_paid_actual=0,debt_funding_actual=0,debt_expected=0,interest_actual=0,interest_expected=0,
                 debt_principal_actual=0,debt_principal_expected=0,deposit_received=0,deposit_returned=0,
                 cash_due_income=0,cash_due_expense=0,
                 capital_future=0,forecast_net=0,actual_net=0,cash_projection=0,cash_actual=0,
                 expected_count=0,paid_count=0) for m in range(1,13)]
    used={(r['schedule_id'],r['occurrence_date']) for r in entries if r['schedule_id'] and r['occurrence_date']}
    forecast_occ=[]
    if y>int(today[:4]):
        # Bring forward future expected cash from today through 31 December of previous year.
        from_date=date.fromisoformat(today)
        to_date=date(y-1,12,31)
        for s in schedules:
            if not s['active']:continue
            for due in _due_dates(s,from_date,to_date):
                if (s['id'],due) not in used:
                    opening+=s['amount_cents']*(1 if s['kind']=='income' else -1)
        opening-=sum(r['amount_cents'] for r in future_debts if r['date']<start)
    for s in schedules:
        if not s['active']:continue
        for due in _due_dates(s,date(y,1,1),date(y,12,31)):
            is_settled=(s['id'],due) in used
            forecast_occ.append(dict(schedule_id=s['id'],occurrence_date=due,kind=s['kind'],
                                     label=s['label'],apartment=s['apartment'],amount_cents=s['amount_cents'],
                                     category=s['category'],settled=is_settled))
            if not is_settled:
                bucket=months[int(due[5:7])-1]
                bucket['income_expected' if s['kind']=='income' else 'expense_expected']+=s['amount_cents']
                if due>=today:
                    bucket['cash_due_income' if s['kind']=='income' else 'cash_due_expense']+=s['amount_cents']
                bucket['expected_count']+=1
    for r in entries:
        if not start<=r['entry_date']<=end:continue
        bucket=months[int(r['entry_date'][5:7])-1]
        if r['entry_date']>today:
            # Legacy future-dated actual entries remain in the ledger for audit,
            # but must not be counted as money already received or paid.
            if r['kind'] in ('income','expense'):
                bucket['income_expected' if r['kind']=='income' else 'expense_expected']+=r['amount_cents']
                bucket['cash_due_income' if r['kind']=='income' else 'cash_due_expense']+=r['amount_cents']
            else:
                bucket['capital_future']+=signed(r)
            bucket['expected_count']+=1
            continue
        if r['kind']=='income':bucket['income_actual']+=r['amount_cents']
        elif r['kind']=='expense':bucket['expense_actual']+=r['amount_cents']
        else:
            bucket['capital_net']+=signed(r)
            bucket['capital_in_actual' if r['kind']=='capital_in' else 'capital_out_actual']+=r['amount_cents']
        bucket['paid_count']+=1
    for r in deposits:
        if start<=r['event_date']<=end:
            bucket=months[int(r['event_date'][5:7])-1]
            bucket['capital_net']+=deposit_signed(r)
            bucket['deposit_received' if r['kind']=='received' else 'deposit_returned']+=r['amount_cents']
    for r in actual_debts:
        if start<=r['date']<=end:
            bucket=months[int(r['date'][5:7])-1]
            bucket['debt_actual']+=r['amount_cents']
            if r['amount_cents']<0:bucket['debt_paid_actual']+=-r['amount_cents']
            else:bucket['debt_funding_actual']+=r['amount_cents']
            if r['kind']=='principal_payment':bucket['debt_principal_actual']+=-r['amount_cents']
            if r['kind']=='interest_payment':bucket['interest_actual']+=-r['amount_cents']
    for r in future_debts:
        if start<=r['date']<=end:
            bucket=months[int(r['date'][5:7])-1]
            bucket['debt_expected']+=r['amount_cents']
            bucket['debt_principal_expected']+=r['principal_cents']
            bucket['interest_expected']+=r['interest_cents']
    cash_actual=opening;cash_forecast=opening
    for m in months:
        m['actual_net']=m['income_actual']-m['expense_actual']+m['capital_net']+m['debt_actual']
        m['forecast_net']=m['actual_net']+m['cash_due_income']-m['cash_due_expense']-m['debt_expected']+m['capital_future']
        cash_actual+=m['actual_net'];cash_forecast+=m['forecast_net']
        m['cash_actual']=cash_actual;m['cash_projection']=cash_forecast
    current_balance=sum(signed(r) for r in entries if r['entry_date']<=today)
    current_balance+=sum(r['amount_cents'] for r in actual_debts if r['date']<=today)
    current_balance+=sum(deposit_signed(r) for r in deposits if r['event_date']<=today)
    totals={k:sum(m[k] for m in months) for k in ('income_actual','expense_actual','income_expected','expense_expected',
           'capital_net','debt_actual','debt_paid_actual','debt_funding_actual','debt_expected','interest_actual','interest_expected',
           'debt_principal_actual','debt_principal_expected','deposit_received','deposit_returned')}
    non_economic_actual=sum(r['amount_cents']*(1 if r['kind']=='income' else -1)
        for r in entries if start<=r['entry_date']<=min(end,today) and r['kind'] in ('income','expense')
        and r.get('economic_nature','economic')!='economic')
    non_economic_expected=sum(s['amount_cents']*(1 if s['kind']=='income' else -1)
        for s in schedules if s['active'] and s.get('economic_nature','economic')!='economic'
        for due in _due_dates(s,date(y,1,1),date(y,12,31)) if (s['id'],due) not in used)
    non_economic_expected+=sum(r['amount_cents']*(1 if r['kind']=='income' else -1)
        for r in entries if max(start,today)<r['entry_date']<=end
        and r['kind'] in ('income','expense') and r.get('economic_nature','economic')!='economic')
    totals['operating_actual']=totals['income_actual']-totals['expense_actual']-non_economic_actual
    totals['operating_forecast']=totals['operating_actual']+totals['income_expected']-totals['expense_expected']-non_economic_expected
    totals['after_interest_forecast']=totals['operating_forecast']-totals['interest_actual']-totals['interest_expected']
    totals['common_share_unverified_count']=sum(1 for r in entries if r['apartment']=='common'
        and r['kind'] in ('income','expense') and start<=r['entry_date']<=end
        and r.get('common_share_ap1') is None)
    totals['end_cash_forecast']=months[-1]['cash_projection']
    totals['end_cash_actual_only']=months[-1]['cash_actual']
    ap_result={'0':0,'1':0}
    def add_split(amount,ap,share=50):
        if ap in ('0','1'):ap_result[ap]+=amount
        else:
            a=round(amount*share/100)
            ap_result['0']+=a;ap_result['1']+=amount-a
    for r in entries:
        if start<=r['entry_date']<=min(end,today) and r['kind'] in ('income','expense') and r.get('economic_nature','economic')=='economic':
            if r['apartment']!='common' or r.get('common_share_ap1') is not None:
                add_split(r['amount_cents']*(1 if r['kind']=='income' else -1),r['apartment'],r.get('common_share_ap1') or 0)
    for s in schedules:
        if not s['active'] or s.get('economic_nature','economic')!='economic':continue
        for due in _due_dates(s,date(y,1,1),date(y,12,31)):
            if (s['id'],due) not in used:
                add_split(s['amount_cents']*(1 if s['kind']=='income' else -1),s['apartment'],s['common_share_ap1'])
    settled_debt={(r['loan_id'],r['occurrence_date']) for r in _row_dicts(db,'SELECT loan_id,occurrence_date FROM finance_debt_settlements')}
    loan_due=[]
    for row in future_debts:
        if start<=row['date']<=end and (row['loan_id'],row['date']) not in settled_debt:
            loan_due.append(dict(row,occurrence_date=row['date'],kind='debt',status='future',settled=False))
    for row in overdue_debts:
        if start<=row['date']<=end:
            loan_due.append(dict(row,kind='debt',settled=False))
    linked_view=[]
    for link in links:
        loan=loans.get(link['loan_id'])
        if loan:
            current=debts.plan_on(loan,today)
            linked_view.append(dict(**link,loan_label=loan['label'],loan_plan=current['repayment_plan'],
                       loan_rate=current['annual_rate'],loan_installment_cents=current['installment_cents'],loan_installment_kind=current['installment_kind']))
    available=[dict(id=l['id'],label=l['label'],property_name=l['property_name'],
                    repayment_plan=debts.plan_on(l,today)['repayment_plan'],installment_cents=debts.plan_on(l,today)['installment_cents'],
                    annual_rate=debts.plan_on(l,today)['annual_rate'],disbursement_date=l['disbursement_date'],installment_kind=debts.plan_on(l,today)['installment_kind'])
               for l in loans.values()]
    return dict(year=y,as_of=today,opening_cash=opening,current_cash=current_balance,months=months,
                totals=totals,apartment_results=ap_result,occurrences=forecast_occ,
                debt_links=linked_view,available_debt_loans=available,
                debt_due_events=loan_due, debt_skips=skips, debt_settlements=_row_dicts(db,'SELECT * FROM finance_debt_settlements ORDER BY payment_date DESC'),
                debt_expected_events=[r for r in future_debts if start<=r['date']<=end],
                debt_actual_events=[r for r in actual_debts if start<=r['date']<=end])

def overview(db,year=None):
    y=_fiscal_year(year or date.today().year)
    result=_project(db,y)
    result['entries']=_row_dicts(db,'SELECT * FROM finance_entries ORDER BY entry_date DESC,created_at DESC')
    result['schedules']=_row_dicts(db,'SELECT * FROM finance_schedules ORDER BY label')
    result['schedule_history']=_row_dicts(db,'SELECT * FROM finance_schedule_audit ORDER BY recorded_at DESC')
    result['tenants']=_tenants(db)
    import finance_balance
    result['categories']=finance_balance.categories(db)
    result['deposits_outstanding_cents']=sum(d['outstanding_cents'] for d in finance_balance.deposits(db))
    result['names']=json.loads(db.execute('SELECT archive FROM state WHERE id=1').fetchone()[0]).get('names',['Appartamento 1','Appartamento 2'])
    return result


def debt_quote(db,loan_id,payment_date):
    day=_date(payment_date,'Data pagamento')
    if day>_now_date(): raise ValueError('Non puoi confermare un pagamento futuro')
    links={l['loan_id']:l for l in _row_dicts(db,'SELECT * FROM finance_debt_links')}
    link=links.get(loan_id)
    loan=next((l for l in debts.loans(db) if l['id']==loan_id),None)
    if not loan or not link:raise ValueError('Finanziamento non collegato a ZEUS Finance')
    if day<link['start_date'] or day<loan['opening_date']:
        raise ValueError('Pagamento precedente all’inizio del finanziamento collegato')
    bal=debts.ledger(loan,debts.movements(db,loan_id),day)
    plan=debts.plan_on(loan,day)
    fixed=plan['installment_kind']=='fixed_total' and plan['repayment_plan']!='free'
    amount=plan['installment_cents']
    cap= max(0,amount-min(amount,bal['interest_due_cents'])) if fixed else (amount if plan['repayment_plan']!='free' else link['principal_installment_cents'])
    suggested_interest=min(amount,bal['interest_due_cents']) if fixed else bal['interest_due_cents']
    return {'loan_id':loan_id,'date':day,'principal_available_cents':bal['principal_cents'],
            'interest_available_cents':bal['interest_due_cents'], 'fee_available_cents':bal['fees_due_cents'],
            'principal_suggested_cents':min(bal['principal_cents'],cap),
            'interest_suggested_cents':suggested_interest,
            'fixed_total':fixed,'installment_total_cents':amount if fixed else 0,
            'fee_suggested_cents':bal['fees_due_cents'],
            'loan_label':loan['label']}


def _settle_debt(db,obj):
    loan_id=_str(obj.get('loan_id'),'Finanziamento',100,True)
    due=_date(obj.get('occurrence_date'),'Scadenza')
    paid=_date(obj.get('date'),'Data effettiva')
    if (loan_id,due) in {(r['loan_id'],r['occurrence_date']) for r in _row_dicts(db,'SELECT loan_id,occurrence_date FROM finance_debt_settlements')}:
        raise ValueError('Questa rata risulta già confermata in ZEUS Finance')
    linked=_row_dicts(db,'SELECT * FROM finance_debt_links WHERE loan_id=?',(loan_id,))
    if not linked:raise ValueError('Finanziamento non collegato')
    if due < linked[0]['start_date'] or paid < linked[0]['start_date']:
        raise ValueError('Data precedente al collegamento')
    if db.execute('SELECT 1 FROM finance_debt_skips WHERE loan_id=? AND occurrence_date=?',(loan_id,due)).fetchone():
        raise ValueError('Rata contrassegnata come saltata: riattivala prima di registrare il pagamento')
    quote=debt_quote(db,loan_id,paid)
    def cents(name,available):
        v=obj.get(name,'0')
        n=0 if v in ('',None) else debts.money(v,positive=True)
        if n>available:raise ValueError('La quota '+name+' supera il residuo disponibile alla data del pagamento')
        return n
    cap=cents('principal',quote['principal_available_cents'])
    intr=cents('interest',quote['interest_available_cents'])
    fee=cents('fee',quote['fee_available_cents'])
    if cap+intr+fee<=0:raise ValueError('Inserisci almeno una quota da pagare')
    note=_str(obj.get('notes',''),'Note',800)
    reference='ZEUS Finance · rata '+due
    ids=[]
    # IMPORTANT: one outer BEGIN IMMEDIATE, all debt movements and link committed together.
    for kind,amount in (('interest_payment',intr),('fee_payment',fee),('principal_payment',cap)):
        if amount:
            r=debts.mutate(db,'/api/debts/movement',dict(loan_id=loan_id,date=paid,kind=kind,
                amount=str(Decimal(amount)/100),reference=reference,note=note))
            ids.append(r['id'])
    db.execute('''INSERT INTO finance_debt_settlements
      (loan_id,occurrence_date,payment_date,principal_cents,interest_cents,fee_cents,movement_ids,note,created_at)
      VALUES(?,?,?,?,?,?,?,?,?)''',(loan_id,due,paid,cap,intr,fee,json.dumps(ids),note,_ts()))
    return {'ok':True,'loan_id':loan_id,'movement_ids':ids,'total_cents':cap+intr+fee}


def _void_debt(db,obj):
    loan_id=_str(obj.get('loan_id'),'Finanziamento',100,True)
    due=_date(obj.get('occurrence_date'),'Scadenza')
    reason=_str(obj.get('reason',''),'Motivo annullamento',500,True)
    row=_row_dicts(db,'SELECT * FROM finance_debt_settlements WHERE loan_id=? AND occurrence_date=?',(loan_id,due))
    if not row:raise ValueError('Rata non trovata')
    ids=json.loads(row[0]['movement_ids'])
    if any(not db.execute('SELECT 1 FROM debt_movements WHERE id=? AND loan_id=?',(i,loan_id)).fetchone() for i in ids):
        raise ValueError('Movimenti del debito alterati: annullamento non sicuro. Verifica lo storico.')
    db.executemany('DELETE FROM debt_movements WHERE id=? AND loan_id=?',[(i,loan_id) for i in ids])
    loan=next(l for l in debts.loans(db) if l['id']==loan_id)
    debts.ledger(loan,debts.movements(db,loan_id),debts.today())
    db.execute('INSERT INTO finance_debt_settlement_audit VALUES(?,?,?,?,?,?)',
               (_uid(),loan_id,due,json.dumps(row[0],ensure_ascii=False),reason,_ts()))
    db.execute('DELETE FROM finance_debt_settlements WHERE loan_id=? AND occurrence_date=?',(loan_id,due))
    debts.audit(db,'rata_finance_annullata',f'{loan_id} {due} {reason[:120]}')
    return {'ok':True}


def mutate(db, route,obj):
    if not isinstance(obj,dict):raise ValueError('Dati non validi')
    now=_ts()
    if route.startswith('/api/finance/category/') or route.startswith('/api/finance/deposit/'):
        import finance_balance
        return finance_balance.mutate(db,route,obj)
    if route=='/api/finance/debt/skip':
        loan_id=_str(obj.get('loan_id'),'Finanziamento',100,True)
        due=_date(obj.get('occurrence_date'),'Scadenza')
        reason=_str(obj.get('reason',''),'Motivo salto rata',500,True)
        if due>_now_date():raise ValueError('Puoi saltare soltanto rate arrivate a scadenza')
        links=_row_dicts(db,'SELECT * FROM finance_debt_links WHERE loan_id=?',(loan_id,))
        if not links or due<links[0]['start_date']:
            raise ValueError('Rata non appartenente a un finanziamento collegato')
        loan=next((l for l in debts.loans(db) if l['id']==loan_id),None)
        if not loan:raise ValueError('Finanziamento non trovato')
        if due not in (_payment_date(loan,links[0],n).isoformat() for n in range(1,1201)):
            raise ValueError('Data non corrispondente a una rata programmata')
        if db.execute('SELECT 1 FROM finance_debt_settlements WHERE loan_id=? AND occurrence_date=?',(loan_id,due)).fetchone():
            raise ValueError('Rata già pagata: non può essere saltata')
        if db.execute('SELECT 1 FROM finance_debt_skips WHERE loan_id=? AND occurrence_date=?',(loan_id,due)).fetchone():
            raise ValueError('Rata già contrassegnata come saltata')
        db.execute('INSERT INTO finance_debt_skips VALUES(?,?,?,?)',(loan_id,due,reason,_ts()))
        db.execute('INSERT INTO finance_debt_skip_audit VALUES(?,?,?,?,?,?)',(_uid(),loan_id,due,'skip',reason,_ts()))
        return {'ok':True}
    if route=='/api/finance/debt/unskip':
        loan_id=_str(obj.get('loan_id'),'Finanziamento',100,True)
        due=_date(obj.get('occurrence_date'),'Scadenza')
        reason=_str(obj.get('reason',''),'Motivo riattivazione',500,True)
        if not db.execute('DELETE FROM finance_debt_skips WHERE loan_id=? AND occurrence_date=?',(loan_id,due)).rowcount:
            raise ValueError('Rata saltata non trovata')
        db.execute('INSERT INTO finance_debt_skip_audit VALUES(?,?,?,?,?,?)',(_uid(),loan_id,due,'unskip',reason,_ts()))
        return {'ok':True}
    if route=='/api/finance/debt/settle':return _settle_debt(db,obj)
    if route=='/api/finance/debt/void':return _void_debt(db,obj)
    if route=='/api/finance/debt/link':
        loan_id=_str(obj.get('loan_id'),'Finanziamento',100,True)
        loans={l['id']:l for l in debts.loans(db)}
        loan=loans.get(loan_id)
        if not loan:raise ValueError('Finanziamento non trovato')
        apartment=_place(obj.get('apartment'))
        start_date=_date(obj.get('start_date'),'Decorrenza collegamento')
        first_due=_date(obj.get('first_due'),'Prima rata prevista',True)
        if first_due and first_due<start_date:raise ValueError('La prima rata deve seguire l’inizio collegamento')
        amount=obj.get('monthly_principal')
        cents=_amount(amount) if amount not in ('',None) else 0
        plan=debts.plan_on(loan,debts.today())
        if cents<=0 and (plan['repayment_plan']=='free' or plan['installment_cents']<=0):
            # A free repayment loan may be linked for actual transactions alone.
            cents=0
        if db.execute('SELECT 1 FROM finance_debt_settlements WHERE loan_id=? AND (payment_date<? OR occurrence_date<?) LIMIT 1',
                      (loan_id,start_date,start_date)).fetchone():
            raise ValueError('Il nuovo collegamento escluderebbe rate già pagate')
        db.execute("""INSERT INTO finance_debt_links
            (loan_id,apartment,start_date,first_due,principal_installment_cents,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(loan_id) DO UPDATE SET apartment=excluded.apartment,
            start_date=excluded.start_date,first_due=excluded.first_due,
            principal_installment_cents=excluded.principal_installment_cents,updated_at=excluded.updated_at""",
            (loan_id,apartment,start_date,first_due,cents,now,now))
        return {'ok':True,'loan_id':loan_id}
    if route=='/api/finance/debt/unlink':
        loan_id=_str(obj.get('loan_id'),'Finanziamento',100,True)
        if db.execute('SELECT 1 FROM finance_debt_settlements WHERE loan_id=? LIMIT 1',(loan_id,)).fetchone():
            raise ValueError('Finanziamento con rate confermate: annulla le rate gestite da Finance prima di scollegarlo')
        if db.execute('SELECT 1 FROM finance_debt_skips WHERE loan_id=? LIMIT 1',(loan_id,)).fetchone():
            raise ValueError('Finanziamento con rate saltate: riattivale prima di scollegarlo')
        if not db.execute('DELETE FROM finance_debt_links WHERE loan_id=?',(loan_id,)).rowcount:
            raise ValueError('Collegamento non trovato')
        return {'ok':True}
    if route=='/api/finance/entry/add':
        rec=_validate_movement(obj)
        if rec['entry_date']>_now_date():
            raise ValueError('Una data futura non è un movimento effettivo: usa una previsione programmata')
        import finance_balance
        nature=finance_balance.nature_for(db,rec['kind'],rec['category'])
        id_=_uid(); db.execute('''INSERT INTO finance_entries
            (id,entry_date,kind,apartment,category,label,amount_cents,notes,created_at,updated_at,economic_nature,common_share_ap1)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (id_,rec['entry_date'],rec['kind'],rec['apartment'],rec['category'],
                  rec['label'],rec['amount_cents'],rec['notes'],now,now,nature,
                  rec['common_share_ap1'] if rec['apartment']=='common' else None))
        return {'ok':True,'id':id_}
    if route=='/api/finance/entry/delete':
        id_=_str(obj.get('id'),'ID',100,True)
        found=_row_dicts(db,'SELECT * FROM finance_entries WHERE id=?',(id_,))
        if not found:raise ValueError('Movimento non trovato')
        reason=_str(obj.get('reason',''),'Motivo annullamento',500,True)
        db.execute('INSERT INTO finance_entry_audit(id,entry_id,original_json,action,reason,recorded_at) VALUES(?,?,?,?,?,?)',
                   (_uid(),id_,json.dumps(found[0],ensure_ascii=False),'annullato',reason,now))
        db.execute('DELETE FROM finance_entries WHERE id=?',(id_,))
        return {'ok':True}
    if route=='/api/finance/schedule/add':
        rec=_validate_schedule(db,obj);sid=_uid()
        import finance_balance
        nature=finance_balance.nature_for(db,rec['kind'],rec['category'])
        if rec['tenant_id']:
            for old in _row_dicts(db,'SELECT start_date,end_date FROM finance_schedules WHERE tenant_id=? AND active=1',(rec['tenant_id'],)):
                if (not old['end_date'] or old['end_date']>=rec['start_date']) and (not rec['end_date'] or rec['end_date']>=old['start_date']):
                    raise ValueError('Esiste già un canone programmato per questo contratto e periodo. Chiudi il precedente prima di aggiungerne uno nuovo.')
        db.execute('''INSERT INTO finance_schedules (id,kind,apartment,category,label,amount_cents,
            frequency,day,start_date,end_date,tenant_id,tenant_label,notes,common_share_ap1,active,created_at,updated_at,economic_nature)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)''',
            (sid,rec['kind'],rec['apartment'],rec['category'],rec['label'],rec['amount_cents'],rec['frequency'],
             rec['day'],rec['start_date'],rec['end_date'],rec['tenant_id'],rec['tenant_label'],rec['notes'],rec['common_share_ap1'],now,now,nature))
        return {'ok':True,'id':sid}
    if route=='/api/finance/schedule/update':
        sid=_str(obj.get('id'),'ID',100,True)
        old_rows=_row_dicts(db,'SELECT * FROM finance_schedules WHERE id=?',(sid,))
        if not old_rows:raise ValueError('Programmazione non trovata')
        old=old_rows[0]
        if old.get('superseded_by'):
            raise ValueError('Versione storica: modifica invece la programmazione che l’ha sostituita')
        rec=_validate_schedule(db,obj)
        import finance_balance
        nature=finance_balance.nature_for(db,rec['kind'],rec['category'])
        last_settled=db.execute('SELECT MAX(occurrence_date) FROM finance_entries WHERE schedule_id=?',(sid,)).fetchone()[0]
        if old['frequency']=='once' and last_settled:
            raise ValueError('Previsione una tantum già registrata: il movimento effettivo deve rimanere invariato. Annulla il movimento con motivazione prima di modificare la previsione.')
        if rec['frequency']=='once' and last_settled:
            raise ValueError('Non puoi trasformare una programmazione con movimenti registrati in una tantum')
        # Check other, overlapping rent schedules. This one may be superseded in the same transaction.
        if rec['tenant_id']:
            for other in _row_dicts(db,'SELECT id,start_date,end_date FROM finance_schedules WHERE tenant_id=? AND active=1 AND id<>?',(rec['tenant_id'],sid)):
                if (not other['end_date'] or other['end_date']>=rec['start_date']) and (not rec['end_date'] or rec['end_date']>=other['start_date']):
                    raise ValueError('Un altro canone è già programmato per questo contratto nel periodo indicato')
        # Recurring schedules already underway are versioned, not rewritten retrospectively.
        versioned=old['frequency']!='once' and (old['start_date']<_now_date() or last_settled is not None)
        if versioned:
            if rec['start_date']<_now_date():
                raise ValueError('Per una ricorrenza già iniziata, imposta una nuova decorrenza da oggi in avanti per conservare le scadenze precedenti')
            if rec['start_date']<=old['start_date']:
                raise ValueError('La nuova decorrenza deve essere successiva a quella della versione precedente')
            if last_settled and rec['start_date']<=last_settled:
                raise ValueError('La nuova decorrenza deve essere successiva all’ultima scadenza già registrata')
            cutoff=(date.fromisoformat(rec['start_date'])-timedelta(days=1)).isoformat()
            if old['end_date'] and old['end_date']<cutoff:cutoff=old['end_date']
            new_id=_uid()
            db.execute('UPDATE finance_schedules SET end_date=?,superseded_by=?,updated_at=? WHERE id=?',(cutoff,new_id,now,sid))
            db.execute('''INSERT INTO finance_schedules (id,kind,apartment,category,label,amount_cents,
                frequency,day,start_date,end_date,tenant_id,tenant_label,notes,common_share_ap1,
                active,created_at,updated_at,replaces_schedule_id,economic_nature)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (new_id,rec['kind'],rec['apartment'],rec['category'],rec['label'],rec['amount_cents'],
                 rec['frequency'],rec['day'],rec['start_date'],rec['end_date'],rec['tenant_id'],rec['tenant_label'],
                 rec['notes'],rec['common_share_ap1'],old['active'],now,now,sid,nature))
            action='nuova_versione';updated_id=new_id
            updated=_row_dicts(db,'SELECT * FROM finance_schedules WHERE id=?',(new_id,))[0]
        else:
            # Safe for a one-off not settled, or a recurring schedule that hasn't started.
            db.execute('''UPDATE finance_schedules SET kind=?,apartment=?,category=?,label=?,
                amount_cents=?,frequency=?,day=?,start_date=?,end_date=?,tenant_id=?,
                tenant_label=?,notes=?,common_share_ap1=?,updated_at=?,economic_nature=? WHERE id=?''',
                (rec['kind'],rec['apartment'],rec['category'],rec['label'],rec['amount_cents'],
                 rec['frequency'],rec['day'],rec['start_date'],rec['end_date'],rec['tenant_id'],
                 rec['tenant_label'],rec['notes'],rec['common_share_ap1'],now,nature,sid))
            action='modifica';updated_id=sid
            updated=_row_dicts(db,'SELECT * FROM finance_schedules WHERE id=?',(sid,))[0]
        db.execute('''INSERT INTO finance_schedule_audit
            (id,schedule_id,successor_id,before_json,after_json,action,recorded_at)
            VALUES(?,?,?,?,?,?,?)''',(_uid(),sid,updated_id,json.dumps(old,ensure_ascii=False),
                                       json.dumps(updated,ensure_ascii=False),action,now))
        return {'ok':True,'id':updated_id,'versioned':versioned}
    if route=='/api/finance/schedule/close':
        sid=_str(obj.get('id'),'ID',100,True)
        cutoff=_date(obj.get('end_date'),'Fine programmazione')
        row=db.execute('SELECT start_date,superseded_by FROM finance_schedules WHERE id=?',(sid,)).fetchone()
        if not row:raise ValueError('Programmazione non trovata')
        if row[1]:raise ValueError('Versione storica non modificabile')
        if cutoff<row[0]:raise ValueError('La fine precede la decorrenza')
        last_settled=db.execute('SELECT MAX(occurrence_date) FROM finance_entries WHERE schedule_id=?',(sid,)).fetchone()[0]
        if last_settled and cutoff<last_settled:
            raise ValueError('Non puoi chiudere la programmazione prima di un pagamento già registrato')
        db.execute('UPDATE finance_schedules SET end_date=?,updated_at=? WHERE id=?',(cutoff,now,sid))
        return {'ok':True}
    if route=='/api/finance/schedule/toggle':
        sid=_str(obj.get('id'),'ID',100,True)
        row=db.execute('SELECT active,superseded_by FROM finance_schedules WHERE id=?',(sid,)).fetchone()
        if row is None:raise ValueError('Programmazione non trovata')
        if row[1]:raise ValueError('Versione storica non modificabile')
        db.execute('UPDATE finance_schedules SET active=?,updated_at=? WHERE id=?',(0 if row[0] else 1,now,sid))
        return {'ok':True}
    if route=='/api/finance/schedule/delete':
        sid=_str(obj.get('id'),'ID',100,True)
        # Keep schedule snapshots if used in the real cash ledger. Disable instead.
        if db.execute('SELECT 1 FROM finance_schedules WHERE id=? AND superseded_by IS NOT NULL',(sid,)).fetchone():
            raise ValueError('Versione storica: non eliminabile')
        if db.execute('SELECT 1 FROM finance_entries WHERE schedule_id=? LIMIT 1',(sid,)).fetchone():
            raise ValueError('Programmazione già utilizzata: disattivala, non eliminarla')
        if not db.execute('DELETE FROM finance_schedules WHERE id=?',(sid,)).rowcount:raise ValueError('Programmazione non trovata')
        return {'ok':True}
    if route=='/api/finance/schedule/settle':
        sid=_str(obj.get('id'),'ID',100,True)
        occurrence=_date(obj.get('occurrence_date'),'Scadenza')
        row=_row_dicts(db,'SELECT * FROM finance_schedules WHERE id=?',(sid,))
        if not row or not row[0]['active']:raise ValueError('Programmazione non attiva')
        s=row[0]
        if occurrence not in _due_dates(s,date.fromisoformat(occurrence),date.fromisoformat(occurrence)):
            raise ValueError('Scadenza non prevista dal programma')
        recdate=_date(obj.get('date'),'Data effettiva')
        if recdate>_now_date():raise ValueError('Non puoi registrare un pagamento futuro come effettuato')
        amount=_amount(obj.get('amount')) if obj.get('amount') not in ('',None) else s['amount_cents']
        if db.execute('SELECT 1 FROM finance_entries WHERE schedule_id=? AND occurrence_date=?',(sid,occurrence)).fetchone():
            raise ValueError('Scadenza già registrata')
        id_=_uid()
        db.execute('''INSERT INTO finance_entries
            (id,entry_date,kind,apartment,category,label,amount_cents,schedule_id,occurrence_date,notes,created_at,updated_at,economic_nature,common_share_ap1)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (id_,recdate,s['kind'],s['apartment'],s['category'],s['label'],amount,sid,occurrence,
             _str(obj.get('notes',''),'Note',800),now,now,s['economic_nature'],
             s['common_share_ap1'] if s['apartment']=='common' else None))
        return {'ok':True,'id':id_}
    raise ValueError('Operazione ZEUS Finance sconosciuta')
