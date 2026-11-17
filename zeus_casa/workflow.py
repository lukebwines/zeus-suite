"""ZEUS Casa — guided invoice/reading flow.

Invoices in reading_pending do NOT enter the ledger until an explicit, verified
conversion. This module never invents readings or interpolates dates.
"""
import datetime as dt
import hashlib
import json
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

import advanced
import readings
import consumption_carry


def proposal(db, pending_id, as_of=None):
    if not isinstance(pending_id, str) or len(pending_id) != 32:
        raise ValueError('Identificativo fattura non valido')
    row=db.execute('''SELECT id,utility,invoice_number,invoice_date,period_start,period_end,
             invoice_type,provider_consumption,total_cents,fixed_cents,linked_bill_id,
             CASE WHEN pdf_data IS NOT NULL AND length(pdf_data)>0 THEN 1 ELSE 0 END,
             provider_start_value,provider_end_value,provider_start_date,provider_end_date,
             provider_unit,provider_consumption_source,conversion_c FROM reading_pending WHERE id=?''', (pending_id,)).fetchone()
    if not row: raise ValueError('Fattura in attesa non trovata')
    p=dict(zip(('id','utility','invoice_number','invoice_date','period_start','period_end',
                'invoice_type','provider_consumption','total_cents','fixed_cents','linked_bill_id','has_pdf',
                'provider_start_value','provider_end_value','provider_start_date','provider_end_date',
                'provider_unit','provider_consumption_source','conversion_c'),row))
    as_of=readings.date(as_of or dt.datetime.now(ZoneInfo('Europe/Rome')).date().isoformat())
    sn=readings.snapshot(db,p['utility'],as_of,p['period_start'],p['period_end'])
    warnings=list(sn['warnings'])
    if sn['available']:
        for meter in (('main','sub','sub2') if sn['meter_mode']=='two_subs' else ('main','sub')):
            pair=sn[meter]
            if pair['start_date']!=p['period_start'] or pair['end_date']!=p['period_end']:
                warnings.append('Letture '+('principale' if meter=='main' else 'sottocontatore app. 2' if meter=='sub2' else 'sottocontatore app. 1')+
                   ' dal '+pair['start_date']+' al '+pair['end_date']+
                   ': periodo diverso dalla fattura ('+p['period_start']+' – '+p['period_end']+').')
    # Supplier readings are separate from apartment meter readings. Never confuse
    # gas meter volumes (m³) with standardised billed volumes (Smc).
    source=p['provider_consumption_source']
    derived=(source=='calcolato_dalle_letture')
    discrepancy=False
    if derived:
        warnings.append('Consumo RICAVATO da due letture del fornitore, non trascritto dal consumo fatturato della bolletta.'+
                        (' Per il gas è espresso in m³ e non in Smc.' if p['utility']=='gas' else ''))
    if p['provider_start_value'] and p['provider_end_value'] and p['provider_consumption'] and not derived:
        supplier_delta=Decimal(p['provider_end_value'])-Decimal(p['provider_start_value'])
        if p['utility']=='gas':
            warnings.append('Le letture del gas sono in m³ mentre il consumo dichiarato può essere in Smc: non vengono confrontati numericamente.')
        elif supplier_delta!=Decimal(p['provider_consumption']):
            discrepancy=True
            warnings.append('La differenza tra letture del FORNITORE ('+str(supplier_delta)+
                ') non coincide con il consumo FATTURATO ('+str(p['provider_consumption'])+
                '). Verifica stime e rettifiche; nessun valore è stato sovrascritto.')
    if p['invoice_type'] in ('stimata','mista','non_indicata'):
        warnings.append('Bolletta stimata/mista: la quota è provvisoria e potrà richiedere un conguaglio.')
    if p['invoice_type']=='conguaglio':
        warnings.append('Fattura di conguaglio: verifica storni e periodi precedenti; non creare un secondo addebito per gli stessi importi.')
    if p['utility']=='gas':
        warnings.append('Gas: i contatori misurano m³; il consumo fatturato potrebbe essere in Smc. Le due unità non sono direttamente confrontabili.')
    archive=advanced.get_archive(db)
    test_bill={'start':p['period_start'],'end':p['period_end']}
    turn=readings.turnover_warning(archive,test_bill)
    if turn:warnings.append(turn)
    overlaps=[]
    for bill in archive.get('bills',[]):
        if (bill.get('utility')==p['utility'] and
            p['period_start']<bill.get('end','') and bill.get('start','')<p['period_end']):
            overlaps.append({'id':str(bill.get('id','')),'invoice':bill.get('invoiceNumber',''),
                             'period':str(bill.get('start'))+' – '+str(bill.get('end'))})
    if overlaps:warnings.append('Esiste già una bolletta della stessa utenza con periodo sovrapposto: evita un doppio addebito e verifica la gestione del conguaglio.')
    can_post=(sn['available'] and not turn and not overlaps and not p['linked_bill_id']
              and p['invoice_type']!='conguaglio' and bool(p['provider_consumption']))
    # An exact match on both meters + effective supplier bill is eligible for
    # reconciliation; all other matches must be treated as provisional.
    exact=(sn['available'] and all(sn[m]['start_date']==p['period_start'] and
               sn[m]['end_date']==p['period_end'] for m in (('main','sub','sub2') if sn['meter_mode']=='two_subs' else ('main','sub'))))
    provisional=(not exact or p['invoice_type']!='effettiva' or derived or discrepancy)
    carry=consumption_carry.preview(db,p,sn,archive)
    if not carry['available']: provisional=True
    if carry['available']:
        warnings.append('Riporto provvisorio: '+carry['reason'])
    else:
        warnings.append('Riporto automatico non disponibile: '+carry['reason']+' La quota può essere comunque confermata come provvisoria.')
    signature=hashlib.sha256(json.dumps(sn,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
    return {'signature':signature,'document':p,'snapshot':sn,'warnings':warnings,'can_post':bool(can_post),
            'exact_dates':bool(exact),'provisional':bool(provisional),'as_of':as_of,
            'overlaps':overlaps,'turnover':turn,'carryover':carry}


def overview(db, as_of=None):
    as_of=readings.date(as_of or dt.datetime.now(ZoneInfo('Europe/Rome')).date().isoformat())
    pending_ids=[r[0] for r in db.execute("SELECT id FROM reading_pending WHERE linked_bill_id='' ORDER BY created_at DESC")]
    items=[proposal(db,pid,as_of) for pid in pending_ids]
    approved=[{'bill_id':row[0], 'mode':row[1], 'date':row[2]} for row in db.execute(
          'SELECT bill_id,mode,updated_at FROM meter_bill_links ORDER BY updated_at DESC LIMIT 30')]
    return {'pending':items,'qr_pending':db.execute("SELECT count(*) FROM qr_submissions WHERE status='pending'").fetchone()[0],
            'registered':len(advanced.get_archive(db).get('bills',[])),
            'recent_links':approved,'as_of':as_of,
            'revision':db.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]}


def post_document(db, params):
    """Requires BEGIN IMMEDIATE in caller. Atomic, revisioned, no double-posting."""
    if not isinstance(params,dict):raise ValueError('Dati non validi')
    key=readings.text(params.get('id'),64,True)
    cutoff=readings.date(params.get('as_of'))
    # Concurrency check blocks stale approval by another browser.
    rev=params.get('expected_revision')
    current=db.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]
    if type(rev)!=int or rev!=current:
        raise PermissionError('Archivio modificato da un altro dispositivo. Aggiorna la pagina e ricontrolla la proposta.')
    review=proposal(db,key,cutoff)
    if not review['can_post']:
        raise PermissionError('Ripartizione non possibile: mancano letture oppure il periodo è già registrato o richiede gestione manuale.')
    if params.get('expected_signature')!=review['signature']:
        raise PermissionError('Le letture sono cambiate: ricalcola e verifica la proposta prima di confermare.')
    if params.get('confirm') is not True:
        raise ValueError('Conferma esplicita della contabilizzazione mancante')
    sn=review['snapshot'];p=review['document'];carry=review['carryover']
    derived=p['provider_consumption_source']=='calcolato_dalle_letture'
    fixed_share=params.get('fixed_share_1',50)
    if type(fixed_share) not in (int,float) or not 0<=fixed_share<=100:
        raise ValueError('Percentuale di quota fissa non valida')
    # Never transfer invoices with mismatched identifiers/dates silently.
    first=sn['main'];second=sn['sub']
    new_id=uuid.uuid4().hex
    bill={'id':new_id,'utility':p['utility'],'start':p['period_start'],'end':p['period_end'],
          'total':p['total_cents']/100,'fixed':p['fixed_cents']/100,
          'generalStart':float(Decimal(first['start'])), 'generalEnd':float(Decimal(first['end'])),
          'subStart':float(Decimal(second['start'])), 'subEnd':float(Decimal(second['end'])),
          'invoiceNumber':p['invoice_number'],'invoiceDate':p['invoice_date'],
          'providerReadingStart':p['provider_start_value'],
          'providerReadingEnd':p['provider_end_value'],
          'providerReadingStartDate':p['provider_start_date'],
          'providerReadingEndDate':p['provider_end_date'],
          'providerBilledConsumption':p['provider_consumption'] if not derived else '',
          'providerConsumptionFromReadings':p['provider_consumption'] if derived else '',
          'providerConsumptionUnit':p['provider_unit'],
          'providerConsumptionSource':p['provider_consumption_source'] or 'non_specificata',
          'providerInvoiceType':p['invoice_type'],
          'providerConversionC':p.get('conversion_c',''),
          'consumptionReconciliation':carry,
          'paid1':False,'paid2':False,'received1':0,'received2':0,
          'fixedShare1':float(fixed_share),'variableShare1':float(carry['ratio_1'] if carry['available'] else sn['ratio']),
          'notes':'Ripartizione '+('PROVVISORIA' if review['provisional'] else 'con letture coincidenti')+
                  ' · fattura archiviata in attesa · date delle letture registrate separatamente. Verificare conguagli.',}
    if sn['meter_mode']=='two_subs':
        bill['meterMode']='two_subs'
        bill['sub2Start']=float(Decimal(sn['sub2']['start']))
        bill['sub2End']=float(Decimal(sn['sub2']['end']))
        bill['notes']+=' · Elettricità con due sottocontatori; scarto rispetto al generale: '+(sn['scarto'] if sn['scarto'] is not None else 'non confrontabile')+' kWh.'
    # Validate calculation using the exact accounting engine of the existing app.
    advanced.calc(bill)
    arch=advanced.get_archive(db)
    defaults=arch.get('defaultTenants',[])
    for a in (0,1):
        tid=(defaults[a] if isinstance(defaults,list) and len(defaults)>a else '')
        tenant=next((x for x in arch.get('tenants',[]) if x.get('id')==tid and x.get('apartment')==a
                      and (not x.get('start') or x['start']<=p['period_end'])
                      and (not x.get('end') or x['end']>=p['period_start'])),None)
        if tenant:bill['tenant'+str(a+1)+'Id']=tid
    arch['bills'].append(bill)
    arch=advanced.enrich(advanced.get_archive(db),arch)
    # revision moves in the same transaction as invoice linkage
    db.execute('UPDATE state SET revision=?, archive=? WHERE id=1',
              (current+1,json.dumps(arch,ensure_ascii=False,separators=(',',':'))))
    if db.execute("UPDATE reading_pending SET linked_bill_id=? WHERE id=? AND linked_bill_id=''",
                  (new_id,key)).rowcount!=1:
        raise PermissionError('Fattura già contabilizzata. Aggiorna la pagina.')
    pdf=db.execute('SELECT pdf_name,pdf_data FROM reading_pending WHERE id=?',(key,)).fetchone()
    if pdf and pdf[1]: advanced.add_invoice(db,new_id,pdf[0],pdf[1])
    note='Creato da percorso guidato · '+('PROVVISORIO' if review['provisional'] else 'letture coincidenti')
    db.execute('''INSERT INTO meter_bill_links
       (bill_id,invoice_type,provider_start,provider_end,provider_consumption,provider_unit,mode,snap_json,note,updated_at,provider_consumption_source)
       VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
       (new_id,p['invoice_type'],p['period_start'],p['period_end'],p['provider_consumption'],
        p['provider_unit'],'applicata',json.dumps(sn,ensure_ascii=False),note,readings.stamp(),
        p['provider_consumption_source']))
    consumption_carry.save(db,new_id,p['utility'],p['period_end'],carry,readings.stamp())
    advanced.log(db,'fattura_guidata_contabilizzata',f'{key} -> {new_id} '+('provvisoria' if review['provisional'] else 'coincidente'))
    return {'ok':True,'bill_id':new_id,'provisional':review['provisional'],
            'q1_cents':advanced.calc(bill)['q1'],'q2_cents':advanced.calc(bill)['q2'],'revision':current+1,'carryover':carry}
