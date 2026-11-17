"""ZEUS Casa – explicit, provisional metered consumption carry-forward.

A carry is an accounting allocation, NOT a dated meter reading. It is only
computed for verified comparable units and aligned physical reading intervals.
Existing invoices are never retroactively modified.
"""
from decimal import Decimal, ROUND_HALF_UP
import json

Q=Decimal('0.0001')

def d(value): return Decimal(str(value))
def fmt(value): return str(value.quantize(Q,rounding=ROUND_HALF_UP))

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS consumption_carry (
       bill_id TEXT PRIMARY KEY, utility TEXT NOT NULL, period_end TEXT NOT NULL,
       snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS consumption_carry_by_utility ON consumption_carry(utility,period_end)')

def last(db,utility):
    row=db.execute('SELECT snapshot_json FROM consumption_carry WHERE utility=? ORDER BY period_end DESC LIMIT 1',(utility,)).fetchone()
    return json.loads(row[0]) if row else None

def preview(db,p,sn,archive):
    unit='kWh' if p['utility']=='electricity' else 'm³'
    result={'available':False,'unit':unit,'reason':'','estimated':True}
    def fail(msg):
        result['reason']=msg
        return result
    if not sn['available']:
        return fail('Mancano letture idonee dei contatori.')
    if p['invoice_type']!='effettiva' or p['provider_consumption_source']!='fattura':
        return fail('Riporto automatico non applicato a stime, conguagli o consumi non dichiarati come fatturati.')
    if not p['provider_consumption']:
        return fail('Consumo fatturato non indicato.')
    mode=sn['meter_mode']
    if p['utility']=='electricity' and mode!='two_subs':
        return fail('Per la luce con due appartamenti occorrono due sottocontatori attivi.')
    # For gas/water apartment 2 is derived from the MAIN meter: the readings
    # MUST refer to the exact same physical measurement window.
    meters=['main','sub']+(['sub2'] if mode=='two_subs' else [])
    if any(sn[m]['start_date']!=sn['main']['start_date'] or sn[m]['end_date']!=sn['main']['end_date'] for m in meters):
        return fail('Date fisiche dei contatori non allineate: non è possibile determinare un riporto attendibile.')
    billed=d(p['provider_consumption'])
    coeff=None
    if p['utility']=='gas':
        val=p.get('conversion_c','')
        if not val:
            return fail('Gas: inserire il coefficiente C della bolletta per confrontare Smc fatturati e m³ rilevati.')
        coeff=d(val)
        if coeff<=0:
            return fail('Coefficiente C non valido.')
        billed=(billed/coeff).quantize(Q,rounding=ROUND_HALF_UP)
    previous=last(db,p['utility'])
    if previous and p['period_end']<=previous['period_end']:
        return fail('Periodo non successivo all’ultimo riporto: serve una verifica manuale.')
    if previous:
        # A separately posted bill after the previous carry breaks the
        # progressive reconciliation until manually re-baselined.
        other=db.execute('SELECT bill_id FROM consumption_carry WHERE utility=?',(p['utility'],)).fetchall()
        included={row[0] for row in other}
        if any(b.get('utility')==p['utility'] and b.get('id') not in included
               and b.get('end','')>previous['period_end'] for b in archive.get('bills',[])):
            return fail('Esistono bollette contabilizzate fuori dal registro dei riporti: riconciliazione automatica sospesa.')
    if not previous:
        old=[b for b in archive.get('bills',[]) if b.get('utility')==p['utility']]
        # Safely start the new ledger only from physical measurements occurring
        # after the last legacy invoice. Legacy balances are unknowable.
        if old and min(sn[m]['start_date'] for m in meters)<max(b.get('end','') for b in old):
            return fail('Sono presenti fatture precedenti senza saldo di riporto: serve una nuova lettura iniziale successiva all’ultimo periodo contabilizzato.')
        measured1=d(sn['sub']['consumption'])
        measured2=(d(sn['sub2']['consumption']) if mode=='two_subs' else
                   d(sn['main']['consumption'])-measured1)
        inherited1=inherited2=Decimal(0)
    else:
        if previous['meter_mode']!=mode:
            return fail('Configurazione contatori cambiata: occorre avviare un nuovo saldo verificato.')
        # The current end values must be later than the previously reconciled
        # meter endpoints. The latest pair alone would otherwise omit readings.
        if any(sn[m]['end_date']<=previous['end_dates'][m] for m in meters):
            return fail('Non sono disponibili nuove letture successive al riporto precedente per tutti i contatori.')
        new_main=d(sn['main']['end'])-d(previous['end_values']['main'])
        measured1=d(sn['sub']['end'])-d(previous['end_values']['sub'])
        measured2=(d(sn['sub2']['end'])-d(previous['end_values']['sub2']) if mode=='two_subs'
                   else new_main-measured1)
        inherited1=d(previous['carry_1']);inherited2=d(previous['carry_2'])
    if min(measured1,measured2)<0:
        return fail('Consumi negativi: verificare allineamento delle letture o sostituzione del contatore.')
    available1=measured1+inherited1; available2=measured2+inherited2
    available=available1+available2
    if billed>available+Q/2:
        return fail('Il consumo fatturato supera le quantità rilevate disponibili: serve un nuovo rilievo o una verifica manuale.')
    if available<=0 and billed>0:
        return fail('Nessun consumo misurato disponibile.')
    allocated1=(billed*available1/available).quantize(Q,rounding=ROUND_HALF_UP) if available>0 else Decimal(0)
    allocated2=billed-allocated1
    carry1=available1-allocated1;carry2=available2-allocated2
    if min(carry1,carry2)<-Q:
        return fail('Saldo di riporto incoerente.')
    result.update({'available':True, 'mode':'proporzionale_provvisorio', 'meter_mode':mode,
       'period_end':p['period_end'], 'measured_1':fmt(measured1),'measured_2':fmt(measured2),
       'inherited_1':fmt(inherited1),'inherited_2':fmt(inherited2),
       'available_1':fmt(available1),'available_2':fmt(available2),
       'billed':fmt(billed),'billed_source':str(p['provider_consumption']),
       'allocated_1':fmt(allocated1),'allocated_2':fmt(allocated2),
       'carry_1':fmt(carry1),'carry_2':fmt(carry2),
       'ratio_1':float(((allocated1/billed*100) if billed>0 else (available1/available*100) if available else Decimal(50)).quantize(Q,rounding=ROUND_HALF_UP)),
       'start_dates':{m:sn[m]['start_date'] for m in meters},
       'end_values':{m:str(sn[m]['end']) for m in meters},
       'end_dates':{m:sn[m]['end_date'] for m in meters},
       'conversion_c':str(coeff) if coeff is not None else '',
       'reason':'Quote provvisorie: le letture materiali possono essere successive alla chiusura del fornitore. Il riporto è un saldo contabile, non una misura fisica datata.',
       'estimated': not all(sn[m]['start_date']==p['period_start'] and sn[m]['end_date']==p['period_end'] for m in meters)})
    return result

def save(db,bill_id,utility,period_end,ledger,when):
    if not ledger.get('available'):return
    db.execute('INSERT INTO consumption_carry VALUES(?,?,?,?,?)',
      (bill_id,utility,period_end,json.dumps(ledger,ensure_ascii=False,separators=(',',':')),when))
