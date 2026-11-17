"""ZEUS Casa: letture, fatture in attesa e confronti. SQLite, stdlib only.

No monetary posting happens without an explicit confirmation. Existing bills are
never silently re-imported or re-created. Meter snapshots are not provider readings.
"""
from __future__ import annotations
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json, uuid, sqlite3
import advanced
import consumption_carry

UTILITIES={'electricity':'kWh','gas':'m³','water':'m³'}
METERS={'main','sub','sub2'}
INVOICE_TYPES={'stimata','effettiva','mista','conguaglio','non_indicata'}

def stamp(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def uid(): return uuid.uuid4().hex
def date(value, required=True):
    if not value and not required:return ''
    if not isinstance(value,str):raise ValueError('Data non valida')
    try:
        if dt.date.fromisoformat(value).isoformat()!=value:raise ValueError()
    except ValueError:raise ValueError('La data deve essere valida (AAAA-MM-GG)')
    return value

def text(value, maxlen=500, required=False):
    if not isinstance(value,str) or len(value)>maxlen or required and not value.strip():
        raise ValueError('Testo obbligatorio o troppo lungo')
    return value.strip()

def dec(value, maxabs='999999999999', places=4, positive=False):
    try:
        # Do not accept locale thousand separators: unambiguous numeric input.
        d=Decimal(str(value).strip().replace(',','.'))
        if (not d.is_finite() or d<0 or positive and d<=0 or d>Decimal(maxabs)
                or d.as_tuple().exponent < -places):raise ValueError()
        return d
    except (ValueError,InvalidOperation,TypeError):raise ValueError('Valore numerico non valido (massimo quattro decimali)')

def money(value):
    d=dec(value,'99999999',2)
    return int(d*100)

def utility(v):
    if v not in UTILITIES:raise ValueError('Utenza non valida')
    return v

def meter(v, u=None):
    if v not in METERS or v=='sub2' and u not in (None,'electricity'):
        raise ValueError('Il secondo sottocontatore è previsto soltanto per l’elettricità')
    return v

def unit(u): return 'kWh' if u=='electricity' else 'Smc' if u=='gas' else 'm³'

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS meter_readings (
      id TEXT PRIMARY KEY, utility TEXT NOT NULL, meter TEXT NOT NULL,
      date TEXT NOT NULL, value TEXT NOT NULL, note TEXT NOT NULL,
      created_at TEXT NOT NULL, UNIQUE(utility,meter,date));
    CREATE INDEX IF NOT EXISTS meter_readings_idx ON meter_readings(utility,meter,date);
    CREATE TABLE IF NOT EXISTS reading_pending (
      id TEXT PRIMARY KEY, utility TEXT NOT NULL, invoice_number TEXT NOT NULL,
      invoice_date TEXT NOT NULL, period_start TEXT NOT NULL,
      period_end TEXT NOT NULL, invoice_type TEXT NOT NULL,
      provider_consumption TEXT NOT NULL, provider_unit TEXT NOT NULL,
      total_cents INTEGER NOT NULL, fixed_cents INTEGER NOT NULL,
      note TEXT NOT NULL, linked_bill_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
      pdf_name TEXT NOT NULL DEFAULT '', pdf_data BLOB);
    CREATE TABLE IF NOT EXISTS electricity_meter_setup (
      id INTEGER PRIMARY KEY CHECK(id=1), second_sub_from TEXT NOT NULL DEFAULT '');
    INSERT OR IGNORE INTO electricity_meter_setup (id,second_sub_from) VALUES (1,'');
    CREATE TABLE IF NOT EXISTS meter_bill_links (
      bill_id TEXT PRIMARY KEY, invoice_type TEXT NOT NULL,
      provider_start TEXT NOT NULL, provider_end TEXT NOT NULL,
      provider_consumption TEXT NOT NULL, provider_unit TEXT NOT NULL,
      mode TEXT NOT NULL, snap_json TEXT NOT NULL,
      note TEXT NOT NULL, updated_at TEXT NOT NULL);
    ''')
    # Immutable audit trail for administrative voids. Voids are always allowed;
    # already-posted bills retain their historic snapshots and are flagged for review.
    db.executescript('''
    CREATE TABLE IF NOT EXISTS meter_reading_voids (
      id TEXT PRIMARY KEY, reading_id TEXT NOT NULL UNIQUE, utility TEXT NOT NULL,
      meter TEXT NOT NULL, reading_date TEXT NOT NULL, value TEXT NOT NULL,
      note TEXT NOT NULL, original_created_at TEXT NOT NULL,
      voided_at TEXT NOT NULL, reason TEXT NOT NULL,
      handover_links_json TEXT NOT NULL, affected_bills_json TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS reading_voids_meter_idx ON meter_reading_voids(utility,meter,voided_at);
    CREATE TABLE IF NOT EXISTS meter_reading_review_flags (
      bill_id TEXT NOT NULL, reading_id TEXT NOT NULL, reason TEXT NOT NULL,
      flagged_at TEXT NOT NULL, PRIMARY KEY(bill_id,reading_id));
    ''')
    # Upward-compatible schema migration: do not erase existing invoices or PDFs.
    existing={r[1] for r in db.execute('PRAGMA table_info(reading_pending)')}
    for col in ('provider_start_value','provider_end_value','provider_start_date','provider_end_date','provider_consumption_source'):
        if col not in existing:
            db.execute(f"ALTER TABLE reading_pending ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    link_cols={r[1] for r in db.execute('PRAGMA table_info(meter_bill_links)')}
    if 'conversion_c' not in existing:
        db.execute("ALTER TABLE reading_pending ADD COLUMN conversion_c TEXT NOT NULL DEFAULT ''")
    consumption_carry.initialize(db)
    if 'provider_consumption_source' not in link_cols:
        db.execute("ALTER TABLE meter_bill_links ADD COLUMN provider_consumption_source TEXT NOT NULL DEFAULT ''")

def archive(db):return advanced.get_archive(db)

def bills(db):return {str(b['id']):b for b in archive(db).get('bills',[])}

def latest_pair(db,u,m,asof):
    rows=db.execute('''SELECT id,date,value FROM meter_readings WHERE utility=? AND meter=? AND date<=?
          ORDER BY date DESC LIMIT 2''',(u,m,asof)).fetchall()
    if len(rows)<2:return None
    first, second=rows[1],rows[0]
    delta=Decimal(second[2])-Decimal(first[2])
    return {'start_id':first[0],'end_id':second[0], 'start_date':first[1],
            'end_date':second[1], 'start':first[2], 'end':second[2], 'consumption':str(delta)}

def second_sub_from(db):
    row=db.execute('SELECT second_sub_from FROM electricity_meter_setup WHERE id=1').fetchone()
    return row[0] if row else ''


def activate_second_sub_on_first_reading(db,when):
    """Start two-submeter electricity only from a real dated measurement.

    Do not reinterpret already approved single-sub invoices retroactively.
    """
    existing=second_sub_from(db)
    if existing:
        if when<existing:
            raise PermissionError('La lettura precede l’attivazione già configurata del secondo sottocontatore. Verifica le date.')
        return False
    for (snapjson,) in db.execute("SELECT snap_json FROM meter_bill_links WHERE mode='applicata'"):
        sn=json.loads(snapjson)
        if sn.get('utility')=='electricity' and sn.get('as_of','')>=when:
            raise PermissionError('Esistono ripartizioni già approvate che potrebbero cambiare: attivazione automatica non consentita.')
    db.execute('UPDATE electricity_meter_setup SET second_sub_from=? WHERE id=1',(when,))
    advanced.log(db,'secondo_sottocontatore_attivato_da_prima_lettura',when)
    return True

def snapshot(db,u,asof,period_start=None,period_end=None):
    """The second electric submeter is enabled only for periods after installation.

    Its first reading is an offset, not measured consumption. No estimate is made
    when the invoiced interval straddles the new submeter's installation date.
    """
    date(asof)
    active_from=second_sub_from(db) if u=='electricity' else ''
    two=bool(active_from and (period_end or asof)>=active_from)
    main=latest_pair(db,u,'main',asof)
    sub=latest_pair(db,u,'sub',asof)
    sub2=latest_pair(db,u,'sub2',asof) if two else None
    out={'available':False,'utility':u,'as_of':asof,'main':main,'sub':sub,'sub2':sub2,
         'meter_mode':'two_subs' if two else 'single_sub','ratio':None,'scarto':None,
         'warnings':[], 'second_sub_from':active_from}
    if two and period_start and period_start<active_from<=period_end:
        out['warnings'].append('La fattura attraversa la data di attivazione del secondo sottocontatore: ripartizione manuale necessaria; non sommare consumi di periodi diversi.')
        return out
    if not main or not sub or two and not sub2:
        out['warnings'].append('Mancano due letture per ciascun contatore richiesto: la prima è solo il riferimento iniziale.')
        return out
    a=Decimal(main['consumption']);b=Decimal(sub['consumption'])
    c=Decimal(sub2['consumption']) if two else Decimal(0)
    if a<0 or b<0 or c<0:
        out['warnings'].append('Lettura decrescente: verifica sostituzione o azzeramento del contatore; nessun calcolo automatico.')
        return out
    if a==0:
        out['warnings'].append('Consumo del generale nullo: verifica i dati prima della ripartizione.')
        return out
    if not two and b>a:
        out['warnings'].append('Il sottocontatore supera il generale: verifica date e misure.')
        return out
    if two and b+c<=0:
        out['warnings'].append('I sottocontatori non registrano consumi; manca una base per le percentuali.')
        return out
    out['available']=True
    denom=b+c if two else a
    out['ratio']=float((b*100/denom).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
    out['apartment1_consumption']=str(b)
    out['apartment2_consumption']=str(c if two else a-b)
    pairs=[main,sub]+([sub2] if two else [])
    aligned=all(x['start_date']==main['start_date'] and x['end_date']==main['end_date'] for x in pairs)
    if not aligned:
        out['warnings'].append('Le letture dei contatori hanno date differenti: quote solo PROVVISORIE; lo scarto rispetto al generale non è confrontabile.')
    if two and aligned:
        out['scarto']=str(a-b-c)
        if a!=b+c:
            out['warnings'].append('Scarto tra generale e somma dei due sottocontatori: '+str(a-b-c)+' kWh. Non attribuito automaticamente agli appartamenti.')
    return out

def turnover_warning(archive_data,b):
    """Conservative flag: changes of occupancy during invoiced period need manual allocation."""
    start,end=b.get('start',''),b.get('end','')
    if not start or not end:return ''
    tenants=archive_data.get('tenants',[])
    for a in (0,1):
        relevant=[t for t in tenants if t.get('apartment')==a and
                  (not t.get('start') or t['start']<=end) and
                  (not t.get('end') or t['end']>=start)]
        if len(relevant)>1 or any(t.get('start') and start<t['start']<=end or
                                  t.get('end') and start<=t['end']<end for t in relevant):
            return 'Cambio conduttore nel periodo: occorrono letture o dati di attribuzione; ripartizione per conduttore manuale.'
    return ''


def get_all(db,asof=None):
    asof=date(asof or dt.date.today().isoformat())
    archive_data=archive(db)
    rs=[dict(zip(('id','utility','meter','date','value','note','created_at'),r)) for r in db.execute(
        'SELECT id,utility,meter,date,value,note,created_at FROM meter_readings ORDER BY utility,meter,date DESC')]
    # Approved QR images are available only through authenticated administrative API.
    qr_photos={row[0][:12]:row[0] for row in db.execute(
        "SELECT id FROM qr_submissions WHERE status='approved'")}
    for item in rs:
        prefix='ZEUS Reading · approvazione QR '
        item['photo_id']=qr_photos.get(item['note'][len(prefix):],'') if item['note'].startswith(prefix) else ''
    ps=[dict(zip(('id','utility','invoice_number','invoice_date','period_start','period_end','invoice_type',
      'provider_consumption','provider_unit','total_cents','fixed_cents','note','linked_bill_id','created_at','pdf_name',
      'provider_start_value','provider_end_value','provider_start_date','provider_end_date','provider_consumption_source','conversion_c'),r))
      for r in db.execute('''SELECT id,utility,invoice_number,invoice_date,period_start,period_end,invoice_type,
        provider_consumption,provider_unit,total_cents,fixed_cents,note,linked_bill_id,created_at,pdf_name,
        provider_start_value,provider_end_value,provider_start_date,provider_end_date,provider_consumption_source,conversion_c
        FROM reading_pending ORDER BY created_at DESC''')]
    links=[dict(zip(('bill_id','invoice_type','provider_start','provider_end','provider_consumption',
        'provider_unit','mode','snapshot_json','note','updated_at','provider_consumption_source'),r)) for r in db.execute('''
        SELECT bill_id,invoice_type,provider_start,provider_end,provider_consumption,provider_unit,
        mode,snap_json,note,updated_at,provider_consumption_source FROM meter_bill_links ORDER BY updated_at DESC''')]
    for li in links:li['snapshot']=json.loads(li.pop('snapshot_json'))
    shots={u:snapshot(db,u,asof) for u in UTILITIES}
    voids=[dict(zip(('id','reading_id','utility','meter','reading_date','value',
       'note','original_created_at','voided_at','reason','handover_links_json','affected_bills_json'),row))
       for row in db.execute('''SELECT id,reading_id,utility,meter,reading_date,value,note,
          original_created_at,voided_at,reason,handover_links_json,affected_bills_json
          FROM meter_reading_voids ORDER BY voided_at DESC LIMIT 100''')]
    for rec in voids:
        rec['handover_links']=json.loads(rec.pop('handover_links_json'))
        rec['affected_bills']=json.loads(rec.pop('affected_bills_json'))
    review_flags=[{'bill_id':r[0],'reading_id':r[1],'reason':r[2],'flagged_at':r[3]}
       for r in db.execute('SELECT bill_id,reading_id,reason,flagged_at FROM meter_reading_review_flags ORDER BY flagged_at DESC')]
    return {'names':archive_data.get('names',['Appartamento 1','Appartamento 2']),
            'bills':[{k:b.get(k) for k in ('id','utility','start','end','total','fixed',
                     'invoiceNumber','invoiceDate','fixedShare1','variableShare1','tenant1Id','tenant2Id')} | {'turnover_warning':turnover_warning(archive_data,b)}
                     for b in archive_data.get('bills',[])],
            'readings':rs,'voided_readings':voids,'reading_review_flags':review_flags,'pending':ps,'links':links,'snapshots':shots,'as_of':asof,
            'electricity_second_sub_from':second_sub_from(db),
            'closures':[r[0] for r in db.execute('SELECT bill_id FROM closures')]}

def mutate(db,route,obj):
    if route=='/api/readings/setup':
        start=date(obj.get('second_sub_from'),False)
        if start!=second_sub_from(db):
            applied=db.execute("SELECT snap_json FROM meter_bill_links WHERE mode='applicata'").fetchall()
            if any(json.loads(row[0]).get('meter_mode')=='two_subs' for row in applied):
                raise PermissionError('Esistono già ripartizioni approvate con due sottocontatori: data non modificabile senza rettifica tracciata.')
            db.execute('UPDATE electricity_meter_setup SET second_sub_from=? WHERE id=1',(start,))
            advanced.log(db,'sottocontatore_elettrico_2_configurato',start or 'non attivo')
        return {'ok':True,'second_sub_from':start}
    if route=='/api/readings/add':
        u=utility(obj.get('utility'));m=meter(obj.get('meter'),u);when=date(obj.get('date'))
        value=dec(obj.get('value'));note=text(obj.get('note',''),600)
        try:db.execute('INSERT INTO meter_readings VALUES (?,?,?,?,?,?,?)',
                       (uid(),u,m,when,str(value),note,stamp()))
        except sqlite3.IntegrityError:raise ValueError('Per questa utenza e contatore esiste già una lettura in tale data.')
        if u=='electricity' and m=='sub2': activate_second_sub_on_first_reading(db,when)
        advanced.log(db,'lettura_aggiunta',f'{u}/{m} {when}')
        return {'ok':True}
    if route in ('/api/readings/delete','/api/readings/void'):
        # Administrative void is NEVER blocked by contract handovers, meter
        # activation or past billing. Financial records are left unchanged;
        # the affected invoices are instead flagged for an explicit review.
        rid=text(obj.get('id'),64,True)
        reason=text(obj.get('reason',''),500,route=='/api/readings/void')
        if not reason:
            reason='Annullamento eseguito tramite interfaccia precedente'
        row=db.execute('''SELECT id,utility,meter,date,value,note,created_at
                            FROM meter_readings WHERE id=?''',(rid,)).fetchone()
        if not row:
            if db.execute('SELECT 1 FROM meter_reading_voids WHERE reading_id=?',(rid,)).fetchone():
                raise ValueError('Questa lettura è già stata annullata; consulta lo storico.')
            raise ValueError('Lettura non trovata')
        reading_id,u,m,when,val,note,created=row
        handover_rows=db.execute('''SELECT tenant_id,slot,apartment,utility,meter,
            reading_id,baseline_date,baseline_value,created_at FROM tenant_handover_baselines
            WHERE reading_id=?''',(rid,)).fetchall()
        handover_cols=('tenant_id','slot','apartment','utility','meter','reading_id',
                       'baseline_date','baseline_value','created_at')
        links=[dict(zip(handover_cols,r)) for r in handover_rows]
        archive_data=archive(db)
        affected={}
        # Snapshot-ID matches are authoritative; same-utility overlapping bills
        # are included conservatively where historic invoices predate snapshots.
        for bid,b in bills(db).items():
            if b.get('utility')==u and (b.get('start','')<=when<=b.get('end','')
                       if b.get('start') and b.get('end') else True):
                affected[bid]='Lettura annullata nel periodo fatturato; verificare la ripartizione precedente.'
        for bill_id,snapjson in db.execute('SELECT bill_id,snap_json FROM meter_bill_links'):
            sn=json.loads(snapjson)
            if any(isinstance(sn.get(k),dict) and rid in (sn[k].get('start_id'),sn[k].get('end_id'))
                   for k in ('main','sub','sub2')):
                affected[bill_id]='Lettura annullata utilizzata nella riconciliazione salvata.'
        # Atomic removal from operational readings. UNIQUE(utility,meter,date)
        # becomes available to record the correct replacement measurement.
        db.execute('DELETE FROM tenant_handover_baselines WHERE reading_id=?',(rid,))
        if links:
            for t in archive_data.get('tenants',[]):
                for link in links:
                    if str(t.get('id'))==link['tenant_id']:
                        hand=t.get('handoverValues')
                        if isinstance(hand,dict):hand.pop(link['slot'],None)
            revision=db.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]
            db.execute('UPDATE state SET revision=?,archive=? WHERE id=1',
                (revision+1,json.dumps(archive_data,ensure_ascii=False,separators=(',',':'))))
        db.execute('DELETE FROM meter_readings WHERE id=?',(rid,))
        moment=stamp()
        db.execute('''INSERT INTO meter_reading_voids
            (id,reading_id,utility,meter,reading_date,value,note,original_created_at,
             voided_at,reason,handover_links_json,affected_bills_json)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
             (uid(),rid,u,m,when,val,note,created,moment,reason,
              json.dumps(links,ensure_ascii=False),json.dumps(sorted(affected),ensure_ascii=False)))
        for bid,why in affected.items():
            db.execute('''INSERT OR IGNORE INTO meter_reading_review_flags
                 (bill_id,reading_id,reason,flagged_at) VALUES (?,?,?,?)''',
                 (bid,rid,why,moment))
        if u=='electricity' and m=='sub2' and when==second_sub_from(db):
            posted_two=any(json.loads(r[0]).get('meter_mode')=='two_subs'
                           for r in db.execute("SELECT snap_json FROM meter_bill_links WHERE mode='applicata'"))
            if not posted_two:
                row2=db.execute("SELECT MIN(date) FROM meter_readings WHERE utility='electricity' AND meter='sub2'").fetchone()
                new_activation=row2[0] or ''
                db.execute('UPDATE electricity_meter_setup SET second_sub_from=? WHERE id=1',(new_activation,))
                advanced.log(db,'data_secondo_contatore_ricalcolata',new_activation or 'non attivo')
            # Otherwise keep historic activation date for posted invoices; the
            # deleted baseline is archived, and any impacted bill is flagged.
        advanced.log(db,'lettura_annullata',f'{u}/{m} {when} id={rid[:12]} motivo={reason[:170]}')
        return {'ok':True,'affected_bills':sorted(affected),'linked_contracts':len(links),
                'message':('Lettura annullata e conservata nello storico.' + (' Controlla le bollette segnalate: gli addebiti precedenti non sono stati modificati.' if affected else '') + (' Per reinserire una lettura iniziale collegata al contratto, utilizza Anagrafica inquilini → Modifica.' if links else ''))}
    if route in ('/api/readings/pending/add','/api/readings/pending/update'):
        is_update=route.endswith('/update')
        edit_id=text(obj.get('id'),64,True) if is_update else ''
        if is_update and not db.execute("SELECT 1 FROM reading_pending WHERE id=? AND linked_bill_id=''",(edit_id,)).fetchone():
            raise PermissionError('Fattura non presente o già contabilizzata: modifica non consentita.')
        u=utility(obj.get('utility'));itype=obj.get('invoice_type')
        if itype not in INVOICE_TYPES:raise ValueError('Tipologia di fattura non valida')
        pstart=date(obj.get('period_start'));pend=date(obj.get('period_end'))
        if pstart>=pend:raise ValueError('Il periodo deve avere fine successiva all’inizio')
        idate=date(obj.get('invoice_date'),False)
        ino=text(obj.get('invoice_number',''),80)
        prov=(str(dec(obj['provider_consumption'])) if str(obj.get('provider_consumption','')).strip() else '')
        start_v=(str(dec(obj['provider_start_value'])) if str(obj.get('provider_start_value','')).strip() else '')
        end_v=(str(dec(obj['provider_end_value'])) if str(obj.get('provider_end_value','')).strip() else '')
        start_date=date(obj.get('provider_start_date'),False)
        end_date=date(obj.get('provider_end_date'),False)
        if bool(start_v)!=bool(end_v):
            raise ValueError('Inserisci entrambe le letture del fornitore, oppure nessuna.')
        if start_v and Decimal(end_v)<Decimal(start_v):
            raise ValueError('La lettura finale del fornitore è inferiore a quella iniziale: verifica eventuale cambio contatore.')
        if start_date and end_date and start_date>end_date:
            raise ValueError('Le date delle letture del fornitore non sono in ordine.')
        # At least ONE documented quantity is necessary. Never synthesize absolute readings
        # from billed consumption; a difference can only be derived from an entire pair.
        if not prov and not start_v:
            raise ValueError('Inserisci il consumo fatturato oppure entrambe le letture rilevate dal fornitore.')
        source='fattura'
        provider_unit=unit(u)
        if not prov:
            prov=str(Decimal(end_v)-Decimal(start_v))
            source='calcolato_dalle_letture'
            if u=='gas':
                # Meter indexes are m³, NOT Smc. Preserve the unit and the origin;
                # never claim a billed Smc amount without the conversion factor.
                provider_unit='m³'
        total=money(obj.get('total'));fixed=money(obj.get('fixed'))
        if fixed>total:raise ValueError('La quota fissa supera la bolletta')
        note=text(obj.get('note',''),1000)
        conv=str(dec(obj['conversion_c'],'10',6,positive=True)) if str(obj.get('conversion_c','')).strip() else ''
        if conv and u!='gas':raise ValueError('Il coefficiente C si utilizza solo per il gas')
        # Block accidental duplicate imports of the same provider invoice.
        if ino and db.execute('SELECT 1 FROM reading_pending WHERE utility=? AND invoice_number=? AND period_start=? AND period_end=?',
                             (u,ino,pstart,pend)).fetchone() and not is_update:
            raise ValueError('Questa fattura è già stata registrata in ZEUS Casa.')
        if is_update and ino and db.execute('SELECT 1 FROM reading_pending WHERE utility=? AND invoice_number=? AND period_start=? AND period_end=? AND id<>?',(u,ino,pstart,pend,edit_id)).fetchone():
            raise ValueError('Questa fattura è già stata registrata in ZEUS Casa.')
        key=edit_id if is_update else uid()
        if is_update:
            result=db.execute('''UPDATE reading_pending SET utility=?,invoice_number=?,invoice_date=?,period_start=?,period_end=?,invoice_type=?,provider_consumption=?,provider_unit=?,total_cents=?,fixed_cents=?,note=?,provider_start_value=?,provider_end_value=?,provider_start_date=?,provider_end_date=?,provider_consumption_source=?,conversion_c=? WHERE id=? AND linked_bill_id='' ''',
              (u,ino,idate,pstart,pend,itype,prov,provider_unit,total,fixed,note,start_v,end_v,start_date,end_date,source,conv,key))
            if result.rowcount!=1: raise PermissionError('Fattura non modificabile')
            advanced.log(db,'fattura_in_attesa_modificata',key)
            return {'id':key,'updated':True}
        db.execute('''INSERT INTO reading_pending
          (id,utility,invoice_number,invoice_date,period_start,period_end,invoice_type,provider_consumption,
          provider_unit,total_cents,fixed_cents,note,created_at,provider_start_value,provider_end_value,
          provider_start_date,provider_end_date,provider_consumption_source,conversion_c) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (key,u,ino,idate,pstart,pend,itype,prov,provider_unit,total,fixed,note,stamp(),start_v,end_v,start_date,end_date,source,conv))
        advanced.log(db,'fattura_in_attesa_creata',key)
        return {'id':key}
    if route=='/api/readings/pending/delete':
        key=text(obj.get('id'),64,True)
        if db.execute('DELETE FROM reading_pending WHERE id=? AND linked_bill_id=?',(key,'')).rowcount!=1:
            raise PermissionError('Documento inesistente o già collegato: non eliminabile da questa sezione')
        advanced.log(db,'fattura_in_attesa_eliminata',key)
        return {'ok':True}
    if route=='/api/readings/pending/link':
        key=text(obj.get('id'),64,True);bid=text(obj.get('bill_id'),100,True)
        pending=db.execute('SELECT utility,invoice_number,invoice_date,period_start,period_end,total_cents FROM reading_pending WHERE id=? AND linked_bill_id=?',(key,'')).fetchone()
        if not pending:raise ValueError('Documento non trovato o già collegato')
        bill=bills(db).get(bid)
        if not bill or bill.get('utility')!=pending[0]:raise ValueError('Bolletta di destinazione inesistente o utenza non coincidente')
        if (bill.get('start')!=pending[3] or bill.get('end')!=pending[4] or
                advanced.cents(bill.get('total'))!=pending[5]):
            raise ValueError('Periodo o importo della bolletta non coincide con il documento in attesa')
        if pending[1] and bill.get('invoiceNumber') and bill['invoiceNumber']!=pending[1]:
            raise ValueError('Numero fattura differente: verifica il documento da collegare')
        if pending[2] and bill.get('invoiceDate') and bill['invoiceDate']!=pending[2]:
            raise ValueError('Data fattura differente: verifica il documento da collegare')
        if db.execute('SELECT 1 FROM reading_pending WHERE linked_bill_id=?',(bid,)).fetchone():
            raise ValueError('Questa bolletta ha già un documento differito collegato')
        db.execute('UPDATE reading_pending SET linked_bill_id=? WHERE id=?',(bid,key))
        advanced.log(db,'fattura_in_attesa_collegata',key+' -> '+bid)
        return {'ok':True}
    if route=='/api/readings/review':
        bid=text(obj.get('bill_id'),100,True)
        b=bills(db).get(bid)
        if not b:raise ValueError('Bolletta non trovata')
        kind=obj.get('invoice_type')
        if kind not in INVOICE_TYPES:raise ValueError('Tipologia documento non valida')
        st=date(obj.get('provider_start'),False);end=date(obj.get('provider_end'),False)
        if bool(st)!=bool(end) or st and end and st>end:
            raise ValueError('Inserisci entrambe le date di lettura del fornitore, in ordine corretto')
        provider=(str(dec(obj['provider_consumption'])) if str(obj.get('provider_consumption','')).strip() else '')
        note=text(obj.get('note',''),1000)
        cutoff=date(obj.get('as_of'))
        s=snapshot(db,b['utility'],cutoff,b.get('start'),b.get('end'))
        apply=obj.get('apply') is True
        if apply:
            if turnover_warning(archive(db),b):
                raise PermissionError('Cambio conduttore durante il periodo: serve una ripartizione manuale motivata.')
            if not s['available']:
                raise ValueError('Mancano letture idonee: non è possibile applicare la ripartizione.')
            if db.execute('SELECT 1 FROM closures WHERE bill_id=?',(bid,)).fetchone():
                raise PermissionError('Rendiconto chiuso: non modificabile.')
            percent=s['ratio']
            # Keep the fixed-cost rule exactly as currently configured.
            advanced.mutate(db,'/api/advanced/split',{'bill_id':bid,
                       'fixedShare1':b.get('fixedShare1') if b.get('fixedShare1') is not None else 50, 'variableShare1':percent})
        previous=db.execute('SELECT mode,snap_json FROM meter_bill_links WHERE bill_id=?',(bid,)).fetchone()
        # A saved applied split remains tied to its historic snapshot until a new
        # split is explicitly approved. Reviewing supplier data is not a reversal.
        if not apply and previous and previous[0]=='applicata':
            mode='applicata'
            saved_snapshot=previous[1]
        else:
            mode='applicata' if apply else 'confronto'
            saved_snapshot=json.dumps(s,ensure_ascii=False)
        db.execute('''INSERT INTO meter_bill_links
          (bill_id,invoice_type,provider_start,provider_end,provider_consumption,provider_unit,mode,snap_json,note,updated_at,provider_consumption_source)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(bill_id) DO UPDATE SET invoice_type=excluded.invoice_type,
             provider_start=excluded.provider_start,provider_end=excluded.provider_end,
             provider_consumption=excluded.provider_consumption,provider_unit=excluded.provider_unit,
             provider_consumption_source=excluded.provider_consumption_source,
             mode=excluded.mode,snap_json=excluded.snap_json,note=excluded.note,
             updated_at=excluded.updated_at''',
          (bid,kind,st,end,provider,unit(b['utility']),mode,saved_snapshot,note,stamp(),'fattura' if provider else ''))
        advanced.log(db,'confronto_registrato',bid+' mode='+mode)
        return {'ok':True,'mode':mode,'snapshot':s}
    raise ValueError('Operazione letture non disponibile')

def add_pdf(db,key,filename,raw):
    key=text(key,64,True)
    filename=text(filename,160,True).replace('/','_').replace('\\','_')
    if not filename.lower().endswith('.pdf') or len(raw)<8 or not raw.startswith(b'%PDF-') or len(raw)>15*1024*1024:
        raise ValueError('Carica un PDF valido, massimo 15 MB')
    if db.execute('UPDATE reading_pending SET pdf_name=?,pdf_data=? WHERE id=? AND linked_bill_id=?',
                  (filename,raw,key,'')).rowcount!=1:
        raise ValueError('Documento inesistente o già collegato: non modificabile')
    advanced.log(db,'pdf_fattura_in_attesa',key)
    return {'ok':True}
