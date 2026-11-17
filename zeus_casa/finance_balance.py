"""ZEUS Finance v1.7.6: classification, management balance, refundable deposits.

Amounts in euro cents. No tax/accounting compliance assertions. Additive migration.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import secrets
import finance

DEFAULTS = {
    'income': ['Canone di locazione','Affitto','Altra entrata immobiliare'],
    'expense': ['Manutenzione ordinaria','Manutenzione straordinaria','IMU','Cedolare secca','IRPEF','Assicurazione immobile','Spese condominiali proprietà','Consulenza','Altre spese immobiliari'],
    'capital_in': ['Dotazione iniziale','Apporto di liquidità'],
    'capital_out': ['Prelievo del proprietario'],
}
NATURES = ('economic','patrimonial','pass_through')

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS finance_categories (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
      nature TEXT NOT NULL CHECK(nature IN ('economic','patrimonial','pass_through')),
      parent_id TEXT, active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(kind,name COLLATE NOCASE))''')
    db.execute('''CREATE TABLE IF NOT EXISTS finance_deposits (
      id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, tenant_label TEXT NOT NULL,
      apartment TEXT NOT NULL, contract_reference TEXT NOT NULL DEFAULT '',
      notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS finance_deposit_events (
      id TEXT PRIMARY KEY, deposit_id TEXT NOT NULL REFERENCES finance_deposits(id),
      event_date TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('received','returned')),
      amount_cents INTEGER NOT NULL CHECK(amount_cents>0), notes TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS finance_deposit_events_date ON finance_deposit_events(event_date)')
    # Migrazione additiva: gli annullamenti restano consultabili, ma non entrano nei saldi.
    for table in ('finance_deposits','finance_deposit_events'):
        existing={x[1] for x in db.execute(f'PRAGMA table_info({table})')}
        for col in ('voided_at','void_reason'):
            if col not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    db.execute('''CREATE TABLE IF NOT EXISTS finance_deposit_corrections (
      id TEXT PRIMARY KEY, deposit_id TEXT NOT NULL, event_id TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL, action TEXT NOT NULL,
      reason TEXT NOT NULL, snapshot_json TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS finance_deposit_corrections_date ON finance_deposit_corrections(created_at)')
    for table in ('finance_entries','finance_schedules'):
        cols={r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        if 'economic_nature' not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN economic_nature TEXT NOT NULL DEFAULT 'economic'")
    now=finance._ts()
    for kind,names in DEFAULTS.items():
        for name in names:
            db.execute('''INSERT OR IGNORE INTO finance_categories
                (id,name,kind,nature,parent_id,active,created_at,updated_at)
                VALUES(?,?,?,?,NULL,1,?,?)''', (finance._uid(),name,kind,'patrimonial' if kind in ('capital_in','capital_out') else 'economic',now,now))
    # Nonstandard historical names remain valid and visible; never modify existing money records.
    for table in ('finance_entries','finance_schedules'):
        for kind,name in db.execute(f'SELECT DISTINCT kind,category FROM {table}').fetchall():
            if kind in DEFAULTS and name:
                db.execute('''INSERT OR IGNORE INTO finance_categories
                (id,name,kind,nature,parent_id,active,created_at,updated_at)
                VALUES(?,?,?,?,NULL,1,?,?)''', (finance._uid(),name,kind,'patrimonial' if kind in ('capital_in','capital_out') else 'economic',now,now))

def categories(db):
    return finance._row_dicts(db,'''SELECT * FROM finance_categories
        ORDER BY kind,parent_id IS NOT NULL,name COLLATE NOCASE''')

def nature_for(db,kind,name):
    if kind in ('capital_in','capital_out'):
        return 'patrimonial'
    row=db.execute('''SELECT nature,active FROM finance_categories WHERE kind=? AND name=? COLLATE NOCASE''',(kind,name)).fetchone()
    if row is None:
        raise ValueError('Categoria non riconosciuta: creala in ZEUS Finance → Categorie')
    if not row[1]:raise ValueError('Categoria disattivata: scegli una categoria attiva')
    return row[0]

def deposit_events(db):
    return finance._row_dicts(db,'''SELECT e.*,d.apartment,d.tenant_label,d.contract_reference
      FROM finance_deposit_events e JOIN finance_deposits d ON e.deposit_id=d.id
      WHERE d.voided_at='' AND e.voided_at='' ORDER BY e.event_date,e.created_at''')

def deposits(db,include_voided=False):
    sql='SELECT * FROM finance_deposits'
    if not include_voided:sql+=" WHERE voided_at=''"
    rows=finance._row_dicts(db,sql+' ORDER BY created_at DESC')
    for d in rows:
        history=finance._row_dicts(db,'SELECT * FROM finance_deposit_events WHERE deposit_id=? ORDER BY event_date,created_at',(d['id'],))
        d['events']=history
        d['original_received_cents']=sum(x['amount_cents'] for x in history if x['kind']=='received')
        d['original_returned_cents']=sum(x['amount_cents'] for x in history if x['kind']=='returned')
        current=[x for x in history if not x['voided_at'] and not d['voided_at']]
        d['received_cents']=sum(x['amount_cents'] for x in current if x['kind']=='received')
        d['returned_cents']=sum(x['amount_cents'] for x in current if x['kind']=='returned')
        d['outstanding_cents']=d['received_cents']-d['returned_cents']
    return rows

def maintenance(db):
    # Riepilogo correzioni per il solo account autenticato.
    return {'deposits':deposits(db,include_voided=True),
            'corrections':finance._row_dicts(db,
              'SELECT * FROM finance_deposit_corrections ORDER BY created_at DESC LIMIT 500')}

def _cancel_reason(obj):
    reason=finance._str(obj.get('reason',''),'Motivazione dell’annullamento',400,True)
    if len(reason.strip())<8:raise ValueError('Specifica una motivazione di almeno 8 caratteri')
    return reason

def _audit_correction(db,action,deposit_id,event_id,reason,existing,now):
    db.execute('''INSERT INTO finance_deposit_corrections
      (id,deposit_id,event_id,created_at,action,reason,snapshot_json)
      VALUES(?,?,?,?,?,?,?)''',
      (finance._uid(),deposit_id,event_id,now,action,reason,json.dumps(existing,ensure_ascii=False,sort_keys=True)))


def mutate(db,route,obj):
    now=finance._ts()
    if route=='/api/finance/category/add':
        kind=finance._kind(obj.get('kind'),True)
        name=finance._str(obj.get('name'),'Categoria',70,True)
        nature=obj.get('nature')
        if nature not in NATURES:raise ValueError('Classificazione non valida')
        finance._prohibited(name,kind)  # utilities reimbursed must remain in Casa
        parent_id=obj.get('parent_id') or None
        if parent_id:
            p=db.execute('SELECT kind,parent_id,active,nature FROM finance_categories WHERE id=?',(parent_id,)).fetchone()
            if not p or p[0]!=kind or p[1] is not None or not p[2] or p[3]!=nature:
                raise ValueError('Sottocategoria: scegli una categoria principale attiva della stessa tipologia e natura')
        uid=finance._uid()
        try:
            db.execute('INSERT INTO finance_categories VALUES(?,?,?,?,?,?,?,?)',(uid,name,kind,nature,parent_id,1,now,now))
        except __import__('sqlite3').IntegrityError:
            raise ValueError('Categoria già presente') from None
        return {'ok':True,'id':uid}
    if route=='/api/finance/category/update':
        cid=finance._str(obj.get('id'),'Categoria',100,True)
        row=db.execute('SELECT id,name,kind,nature,parent_id,active FROM finance_categories WHERE id=?',(cid,)).fetchone()
        if not row:raise ValueError('Categoria non trovata')
        name=finance._str(obj.get('name'),'Categoria',70,True)
        nature=obj.get('nature',row[3])
        if nature not in NATURES:raise ValueError('Classificazione non valida')
        finance._prohibited(name,row[2])
        old_use=any(db.execute(f'SELECT 1 FROM {tbl} WHERE kind=? AND category=? COLLATE NOCASE LIMIT 1',(row[2],row[1])).fetchone() for tbl in ('finance_entries','finance_schedules'))
        if old_use and (name!=row[1] or nature!=row[3]):
            raise ValueError('Categoria già usata: per preservare lo storico puoi solo disattivarla e crearne una nuova')
        children=db.execute('SELECT 1 FROM finance_categories WHERE parent_id=? LIMIT 1',(cid,)).fetchone()
        if children and nature!=row[3]:raise ValueError('Non modificare la natura delle categorie con sottocategorie')
        if row[4]:
            parent=db.execute('SELECT nature FROM finance_categories WHERE id=?',(row[4],)).fetchone()
            if not parent or nature!=parent[0]:raise ValueError('La natura deve coincidere con quella della categoria principale')
        active=1 if obj.get('active',True) is True else 0
        try:db.execute('UPDATE finance_categories SET name=?,nature=?,active=?,updated_at=? WHERE id=?',(name,nature,active,now,cid))
        except __import__('sqlite3').IntegrityError:raise ValueError('Nome categoria già in uso') from None
        return {'ok':True}
    if route=='/api/finance/deposit/return/void':
        did=finance._str(obj.get('id'),'Deposito',100,True)
        eid=finance._str(obj.get('event_id'),'Restituzione',100,True)
        reason=_cancel_reason(obj)
        dep=db.execute('SELECT voided_at FROM finance_deposits WHERE id=?',(did,)).fetchone()
        ev=finance._row_dicts(db,'''SELECT * FROM finance_deposit_events
          WHERE id=? AND deposit_id=? AND kind='returned' AND voided_at='' ''',(eid,did))
        if not dep or dep[0] or not ev:raise ValueError('Restituzione non valida o già annullata')
        _audit_correction(db,'annullamento_restituzione',did,eid,reason,ev[0],now)
        db.execute("UPDATE finance_deposit_events SET voided_at=?,void_reason=? WHERE id=? AND voided_at=''",(now,reason,eid))
        return {'ok':True,'id':did,'event_id':eid,'action':'annullamento_restituzione'}
    if route=='/api/finance/deposit/void':
        did=finance._str(obj.get('id'),'Deposito',100,True)
        reason=_cancel_reason(obj)
        rows=finance._row_dicts(db,"SELECT * FROM finance_deposits WHERE id=? AND voided_at=''",(did,))
        if not rows:raise ValueError('Deposito non trovato o già annullato')
        events=finance._row_dicts(db,'SELECT * FROM finance_deposit_events WHERE deposit_id=?',(did,))
        if any(e['kind']=='returned' and not e['voided_at'] for e in events):
            raise ValueError('Il deposito ha restituzioni attive. Annulla prima le restituzioni dalla Manutenzione dati.')
        _audit_correction(db,'annullamento_deposito',did,'',reason,{'deposit':rows[0],'events':events},now)
        db.execute("UPDATE finance_deposits SET voided_at=?,void_reason=? WHERE id=? AND voided_at=''",(now,reason,did))
        return {'ok':True,'id':did,'action':'annullamento_deposito'}
    if route=='/api/finance/deposit/add':
        tenant_id=finance._str(obj.get('tenant_id'),'Contratto',100,True)
        tenant=next((t for t in finance._tenants(db) if t['id']==tenant_id),None)
        if not tenant or tenant['apartment'] not in ('0','1'):
            raise ValueError('Seleziona un contratto di locazione esistente')
        day=finance._date(obj.get('date'),'Data ricezione')
        if day>finance._now_date():raise ValueError('Non registrare incassi futuri come effettivi')
        amount=finance._amount(obj.get('amount'))
        reference=finance._str(obj.get('contract_reference',''),'Riferimento contratto',140)
        notes=finance._str(obj.get('notes',''),'Note',800)
        did=finance._uid()
        db.execute('INSERT INTO finance_deposits (id,tenant_id,tenant_label,apartment,contract_reference,notes,created_at) VALUES(?,?,?,?,?,?,?)',(did,tenant_id,tenant['name'],tenant['apartment'],reference,notes,now))
        db.execute('INSERT INTO finance_deposit_events (id,deposit_id,event_date,kind,amount_cents,notes,created_at) VALUES(?,?,?,?,?,?,?)',(finance._uid(),did,day,'received',amount,notes,now))
        return {'ok':True,'id':did}
    if route=='/api/finance/deposit/return':
        did=finance._str(obj.get('id'),'Deposito',100,True)
        row=db.execute("SELECT id FROM finance_deposits WHERE id=? AND voided_at=''",(did,)).fetchone()
        if not row:raise ValueError('Deposito non trovato')
        when=finance._date(obj.get('date'),'Data restituzione')
        if when>finance._now_date():raise ValueError('Non registrare restituzioni future come effettive')
        amount=finance._amount(obj.get('amount'))
        ev=db.execute('SELECT event_date,amount_cents FROM finance_deposit_events WHERE deposit_id=? AND kind="received"',(did,)).fetchone()
        if not ev or when<ev[0]:raise ValueError('La restituzione precede il versamento')
        done=db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM finance_deposit_events WHERE deposit_id=? AND kind='returned' AND voided_at=''",(did,)).fetchone()[0]
        if amount>ev[1]-done:raise ValueError('Restituzione superiore al deposito ancora da restituire')
        notes=finance._str(obj.get('notes',''),'Motivazione/riferimento',800)
        db.execute('INSERT INTO finance_deposit_events (id,deposit_id,event_date,kind,amount_cents,notes,created_at) VALUES(?,?,?,?,?,?,?)',(finance._uid(),did,when,'returned',amount,notes,now))
        return {'ok':True}
    raise ValueError('Operazione bilancio non disponibile')

def balance(db,year):
    y=finance._fiscal_year(year or date.today().year)
    start=f'{y}-01-01';end=f'{y}-12-31'
    model=finance._project(db,y)
    catdefs={(r['kind'],r['name'].lower()):r for r in categories(db)}
    entries=finance._row_dicts(db,'SELECT * FROM finance_entries WHERE entry_date BETWEEN ? AND ?',(start,end))
    schedules=finance._row_dicts(db,'SELECT * FROM finance_schedules')
    used={(r['schedule_id'],r['occurrence_date']) for r in finance._row_dicts(db,'SELECT schedule_id,occurrence_date FROM finance_entries WHERE schedule_id IS NOT NULL')}
    groups={}
    split_actual={'0':0,'1':0}
    def assign(kind,name,nature,ap,amount,forecast=False,share=50):
        master=catdefs.get((kind,name.lower()))
        parent=next((r for r in categories_cache if r['id']==master['parent_id']),None) if master and master['parent_id'] else None
        group=parent['name'] if parent else name
        key=(kind,group,name,nature)
        row=groups.setdefault(key,{'kind':kind,'group':group,'category':name,'nature':nature,'actual_cents':0,'forecast_cents':0})
        row['forecast_cents' if forecast else 'actual_cents']+=amount
        if not forecast and nature=='economic' and share is not None:
            sign=1 if kind=='income' else -1
            if ap in ('0','1'):split_actual[ap]+=sign*amount
            elif ap=='common':
                a=round(amount*share/100)
                split_actual['0']+=sign*a;split_actual['1']+=sign*(amount-a)
    categories_cache=categories(db)
    for r in entries:
        if r['kind'] in ('income','expense'):
            assign(r['kind'],r['category'],r.get('economic_nature') or 'economic',r['apartment'],
                r['amount_cents'],forecast=r['entry_date']>finance._now_date(),
                share=r.get('common_share_ap1') if r['apartment']=='common' else 50)
    for s in schedules:
        if not s['active']:continue
        for due in finance._due_dates(s,date(y,1,1),date(y,12,31)):
            if (s['id'],due) not in used:
                assign(s['kind'],s['category'],s.get('economic_nature') or 'economic',s['apartment'],s['amount_cents'],True,s['common_share_ap1'])
    income=sum(r['actual_cents'] for r in groups.values() if r['kind']=='income' and r['nature']=='economic')
    costs=sum(r['actual_cents'] for r in groups.values() if r['kind']=='expense' and r['nature']=='economic')
    income_due=sum(r['forecast_cents'] for r in groups.values() if r['kind']=='income' and r['nature']=='economic')
    costs_due=sum(r['forecast_cents'] for r in groups.values() if r['kind']=='expense' and r['nature']=='economic')
    debt=model['debt_actual_events']
    interest=sum(-d['amount_cents'] for d in debt if d['kind']=='interest_payment' and d['amount_cents']<0)
    fees=sum(-d['amount_cents'] for d in debt if d['kind']=='fee_payment' and d['amount_cents']<0)
    expected_interest=model['totals']['interest_expected']
    ev=deposit_events(db)
    outstanding=sum(x['amount_cents']*(1 if x['kind']=='received' else -1) for x in ev if x['event_date']<=finance._now_date())
    received=sum(x['amount_cents'] for x in ev if x['kind']=='received' and start<=x['event_date']<=end)
    returned=sum(x['amount_cents'] for x in ev if x['kind']=='returned' and start<=x['event_date']<=end)
    # Expose all settlements for audit; never infer that a deposit is taxable income.
    return {'year':y,'as_of':finance._now_date(),'categories':categories_cache,
        'groups':sorted(groups.values(),key=lambda r:(r['kind'],r['group'],r['category'])),
        'economic':{'income_cents':income,'costs_cents':costs,'interest_cents':interest,'fees_cents':fees,
           'operating_cents':income-costs,'result_cents':income-costs-interest-fees,
           'income_unconfirmed_cents':income_due,'costs_unconfirmed_cents':costs_due,
           'interest_expected_cents':expected_interest},
        'cash':{'current_cents':model['current_cash'],'opening_cents':model['opening_cash'],
           'end_forecast_cents':model['totals']['end_cash_forecast'],
           'deposit_received_cents':received,'deposit_returned_cents':returned,
           'deposit_outstanding_cents':outstanding,'net_of_deposits_cents':model['current_cash']-outstanding},
        'apartments':split_actual,'unverified_common_entries':[
           {'id':r['id'],'date':r['entry_date'],'label':r['label'],'amount_cents':r['amount_cents']}
           for r in entries if r['apartment']=='common' and r.get('common_share_ap1') is None
           and r['entry_date']<=finance._now_date()],
        'deposits':deposits(db),'deposit_events':ev,
        'months':model['months'], 'actual_debt_events':debt, 'forecast_debt_events':model['debt_expected_events'],
        'names':json.loads(db.execute('SELECT archive FROM state WHERE id=1').fetchone()[0]).get('names',['Appartamento 1','Appartamento 2'])}
