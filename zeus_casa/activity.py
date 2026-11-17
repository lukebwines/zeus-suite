"""ZEUS v1.7.4 – durable, low-disclosure operational audit.

Events are inserted in the same SQLite transaction as successful mutations.
Only validated path segments, safe identifiers and action codes are persisted.
No user-supplied document content, passwords, tokens or payment descriptions.
"""
from datetime import datetime, timezone
import re

MODULES = {'debts':'ZEUS Lending','finance':'ZEUS Finance',
           'vault':'ZEUS Vault','advanced':'ZEUS Casa',
           'readings':'ZEUS Casa','qr-admin':'ZEUS Casa',
           'documents':'ZEUS Casa','invoices':'ZEUS Casa',
           'workflow':'ZEUS Casa'}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS zeus_activity(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL, actor TEXT NOT NULL, module TEXT NOT NULL,
        action TEXT NOT NULL, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '')''')
    db.execute('CREATE INDEX IF NOT EXISTS ix_zeus_activity_date ON zeus_activity(created_at DESC,id DESC)')


def event(db,actor,module,action,object_type='',object_id='',detail=''):
    # Actor comes from configured account (not the request body).
    actor = str(actor)[:100]
    module = str(module)[:50]
    action = str(action)[:100]
    # IDs often contain an external reference; keep only an opaque token.
    object_id = str(object_id or '')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',object_id):object_id=''
    if detail not in ('','Nomi appartamenti aggiornati','Bollette aggiornate',
                      'Inquilini aggiornati','Archivio aggiornato'):
        detail=''
    db.execute('''INSERT INTO zeus_activity(created_at,actor,module,action,object_type,object_id,detail)
      VALUES(?,?,?,?,?,?,?)''', (datetime.now(timezone.utc).isoformat(timespec='seconds'),
       actor,module,action,str(object_type)[:80],object_id,detail))


def mutation(db,actor,route,request=None,result=None):
    bits=[x for x in route.split('/') if x]
    if len(bits)<2 or bits[0]!='api':return
    section=bits[1]
    module=MODULES.get(section,'ZEUS Casa')
    action='/'.join(bits[2:]) if len(bits)>2 else 'aggiornamento'
    identifier = request.get('id') if isinstance(request,dict) else None
    if not identifier and isinstance(result,dict):identifier=result.get('id')
    event(db,actor,module,action,section,identifier)


def state_change(db,actor,old,new):
    # Changes are categorized without persisting tenants' names or bill amounts.
    if old.get('names') != new.get('names'):
        detail='Nomi appartamenti aggiornati'
    elif old.get('tenants') != new.get('tenants'):
        detail='Inquilini aggiornati'
    elif old.get('bills') != new.get('bills'):
        detail='Bollette aggiornate'
    else: detail='Archivio aggiornato'
    event(db,actor,'ZEUS Casa','salvataggio','archivio','',''+detail)


def list_events(db,limit=300,module='',since=''):
    if type(limit) is not int or not 1<=limit<=1000:raise ValueError('Limite non valido')
    if module and module not in ('ZEUS Casa','ZEUS Lending','ZEUS Finance','ZEUS Vault'):raise ValueError('Modulo non valido')
    if since and not re.fullmatch(r'\d{4}-\d{2}-\d{2}',since):raise ValueError('Data non valida')
    filters=[]; args=[]
    if module:filters.append('module=?');args.append(module)
    if since:filters.append('created_at>=?');args.append(since)
    where=' WHERE '+' AND '.join(filters) if filters else ''
    sql=('SELECT id,created_at,actor,module,action,object_type,object_id,detail '
         'FROM zeus_activity'+where+' ORDER BY id DESC LIMIT ?')
    args.append(limit)
    cur=db.execute(sql,args)
    return [dict(zip([d[0] for d in cur.description],r)) for r in cur.fetchall()]
