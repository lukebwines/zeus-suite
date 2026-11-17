"""Contract handover baselines: tenant-linked physical measurements, never estimates.

Called inside the same BEGIN IMMEDIATE transaction as the archived tenant update.
Existing readings can be linked when their date and value match, but never overwritten.
"""
import datetime as dt
from decimal import Decimal
import uuid
import readings
import advanced

SLOTS = {
    'electricity': ('electricity', 'sub', 0),
    'gas': ('gas', 'sub', 0),
    'water': ('water', 'sub', 0),
    'electricity_sub2': ('electricity', 'sub2', 1),
    'electricity_main': ('electricity', 'main', None),
    'gas_main': ('gas', 'main', None),
    'water_main': ('water', 'main', None),
}

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS tenant_handover_baselines (
       tenant_id TEXT NOT NULL, slot TEXT NOT NULL,
       apartment INTEGER NOT NULL, utility TEXT NOT NULL, meter TEXT NOT NULL,
       reading_id TEXT NOT NULL, baseline_date TEXT NOT NULL,
       baseline_value TEXT NOT NULL, created_at TEXT NOT NULL,
       PRIMARY KEY (tenant_id,slot))''')
    db.execute('CREATE INDEX IF NOT EXISTS tenant_handover_reading_idx ON tenant_handover_baselines(reading_id)')

def sync(db,old,new):
    old_by={x['id']:x for x in old.get('tenants',[])}
    new_by={x['id']:x for x in new.get('tenants',[])}
    # An archived profile may be removed from the active rubrica; any old
    # tenant_handover_baselines remain as historical references to real readings.
    for tid,t in new_by.items():
        previous=old_by.get(tid)
        apt=t.get('apartment')
        if type(apt) is not int or apt not in (0,1):
            raise ValueError('Appartamento del contratto non valido.')
        begin=t.get('start','')
        if (previous is None or t.get('handoverValues')) and not begin:
            raise ValueError('Inserisci la data di decorrenza della locazione.')
        if begin: readings.date(begin)
        hand=t.get('handoverValues') or {}
        if not isinstance(hand,dict) or set(hand)-set(SLOTS):
            raise ValueError('Letture di consegna non valide.')
        # Missing values are legitimate; they must be recorded subsequently, not fabricated.
        has_values=any(str(v).strip() for v in hand.values())
        raw_date=t.get('handoverDate','')
        if not isinstance(raw_date,str):raise ValueError('Data di consegna non valida.')
        when=readings.date(raw_date or begin) if has_values else (readings.date(raw_date) if raw_date else '')
        if has_values:t['handoverDate']=when
        if previous is not None and t.get('apartment')!=previous.get('apartment') and db.execute(
            'SELECT 1 FROM tenant_handover_baselines WHERE tenant_id=?',(tid,)).fetchone():
            raise PermissionError('Non spostare un inquilino con letture iniziali collegate: crea una nuova scheda per il nuovo rapporto.')
        existing={r[0]:(r[1],r[2]) for r in db.execute(
            'SELECT slot,baseline_date,baseline_value FROM tenant_handover_baselines WHERE tenant_id=?',(tid,))}
        for slot in SLOTS:
            raw=hand.get(slot,'')
            if raw is None:raw=''
            if not isinstance(raw,(str,int,float)) or len(str(raw))>32:
                raise ValueError('Valore della lettura iniziale non valido.')
            if not str(raw).strip():
                if slot in existing:raise PermissionError('Una lettura iniziale già registrata non può essere cancellata dalla scheda. Richiedi una rettifica tracciata.')
                continue
            util,meter,required_apt=SLOTS[slot]
            if required_apt is not None and apt!=required_apt:
                raise ValueError('Il contatore indicato non appartiene a questo appartamento.')
            val=readings.dec(raw)
            value=str(val)
            if slot in existing:
                old_when,old_val=existing[slot]
                if old_when!=when or Decimal(old_val)!=val:
                    raise PermissionError('Lettura di consegna già salvata: non modificarla implicitamente. Serve una rettifica controllata.')
                continue
            row=db.execute('SELECT id,value FROM meter_readings WHERE utility=? AND meter=? AND date=?',
                           (util,meter,when)).fetchone()
            if row:
                if Decimal(row[1])!=val:
                    raise ValueError(f'{util}/{meter}: il {when} è già presente una lettura di valore diverso. Verifica il registro.')
                rid=row[0]
            else:
                rid=uuid.uuid4().hex
                note=f'Lettura di consegna contratto · {t.get("name", "")[:85]} · appartamento {apt+1}'
                db.execute('INSERT INTO meter_readings(id,utility,meter,date,value,note,created_at) VALUES(?,?,?,?,?,?,?)',
                           (rid,util,meter,when,value,note,readings.stamp()))
            db.execute('''INSERT INTO tenant_handover_baselines
               (tenant_id,slot,apartment,utility,meter,reading_id,baseline_date,baseline_value,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)''',
               (tid,slot,apt,util,meter,rid,when,value,readings.stamp()))
            advanced.log(db,'lettura_iniziale_locazione',f'{tid[:12]} {slot} {when}')
            if slot=='electricity_sub2':
                readings.activate_second_sub_on_first_reading(db,when)
