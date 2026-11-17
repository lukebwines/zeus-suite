"""ZEUS Vault: private document archive, folders, deadlines and immutable files.

All records live in the existing SQLite database; file bytes live in /data/vault/files.
Public QR service intentionally never imports or exposes this module.
"""
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import date, datetime, timezone, timedelta
from calendar import monthrange
from pathlib import Path
from zoneinfo import ZoneInfo
import advanced

ROOT = Path('/data/vault/files')
ALLOWED = {'.pdf':'application/pdf', '.jpg':'image/jpeg', '.jpeg':'image/jpeg',
           '.png':'image/png', '.txt':'text/plain; charset=utf-8',
           '.doc':'application/msword', '.xls':'application/vnd.ms-excel',
           '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
           '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
MAX_FILE = 15 * 1024 * 1024

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def uid(): return secrets.token_hex(16)
def field(data, key, max_len=180, required=False):
    val=data.get(key,'')
    if val is None:val=''
    if not isinstance(val,str) or len(val)>max_len or (required and not val.strip()):
        raise ValueError('Campo non valido: '+key)
    return val.strip()
def iso(s, required=False):
    if not s and not required: return ''
    try:
        if date.fromisoformat(s).isoformat()!=s:raise ValueError()
    except (TypeError,ValueError):raise ValueError('Data non valida (AAAA-MM-GG)')
    return s

def initialize(db,data_dir):
    global ROOT
    ROOT=Path(data_dir)/'vault'/'files';ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS vault_folders(
      id TEXT PRIMARY KEY,title TEXT NOT NULL,category TEXT NOT NULL DEFAULT '',
      description TEXT NOT NULL DEFAULT '',link_kind TEXT NOT NULL DEFAULT '',
      link_id TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT);
    CREATE TABLE IF NOT EXISTS vault_documents(
      id TEXT PRIMARY KEY,folder_id TEXT REFERENCES vault_folders(id),
      title TEXT NOT NULL,category TEXT NOT NULL DEFAULT '',document_date TEXT NOT NULL DEFAULT '',
      description TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT);
    CREATE TABLE IF NOT EXISTS vault_files(
      id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES vault_documents(id),
      filename TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,
      disk_name TEXT NOT NULL UNIQUE,replaces_id TEXT,created_at TEXT NOT NULL,deleted_at TEXT);
    CREATE TABLE IF NOT EXISTS vault_deadlines(
      id TEXT PRIMARY KEY,title TEXT NOT NULL,folder_id TEXT REFERENCES vault_folders(id),
      document_id TEXT REFERENCES vault_documents(id),due_date TEXT NOT NULL,
      recurrence TEXT NOT NULL DEFAULT 'none',notice_days INTEGER NOT NULL DEFAULT 30,
      status TEXT NOT NULL DEFAULT 'pending',description TEXT NOT NULL DEFAULT '',
      anchor_day INTEGER NOT NULL DEFAULT 0, last_notified_for TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,deleted_at TEXT);
    CREATE TABLE IF NOT EXISTS vault_events(
      id TEXT PRIMARY KEY,object_type TEXT NOT NULL,object_id TEXT NOT NULL,
      action TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS ix_vault_documents_folder ON vault_documents(folder_id);
    CREATE INDEX IF NOT EXISTS ix_vault_files_document ON vault_files(document_id);
    CREATE INDEX IF NOT EXISTS ix_vault_deadlines_due ON vault_deadlines(due_date,status);
    CREATE INDEX IF NOT EXISTS ix_vault_events_ref ON vault_events(object_type,object_id,created_at);
    ''')
    if 'anchor_day' not in [r[1] for r in db.execute('PRAGMA table_info(vault_deadlines)')]:
        db.execute('ALTER TABLE vault_deadlines ADD COLUMN anchor_day INTEGER NOT NULL DEFAULT 0')

def rows(db,sql,args=()):
    cur=db.execute(sql,args)
    return [dict(zip([c[0] for c in cur.description],r)) for r in cur.fetchall()]

def get(db):
    folders=rows(db,'SELECT * FROM vault_folders WHERE deleted_at IS NULL ORDER BY updated_at DESC')
    docs=rows(db,'SELECT * FROM vault_documents WHERE deleted_at IS NULL ORDER BY updated_at DESC')
    attachments=rows(db,'''SELECT id,document_id,filename,mime,size,sha256,replaces_id,created_at
        FROM vault_files WHERE deleted_at IS NULL AND document_id IN (SELECT id FROM vault_documents WHERE deleted_at IS NULL) ORDER BY created_at DESC''')
    deadlines=rows(db,'SELECT * FROM vault_deadlines WHERE deleted_at IS NULL ORDER BY due_date,id')
    recent=rows(db,'SELECT object_type,object_id,action,details,created_at FROM vault_events ORDER BY created_at DESC LIMIT 100')
    trash=rows(db,"SELECT id,title,'document' AS kind,deleted_at FROM vault_documents WHERE deleted_at IS NOT NULL UNION ALL SELECT id,title,'folder',deleted_at FROM vault_folders WHERE deleted_at IS NOT NULL UNION ALL SELECT id,title,'deadline',deleted_at FROM vault_deadlines WHERE deleted_at IS NOT NULL UNION ALL SELECT id,filename,'file',deleted_at FROM vault_files WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT 100")
    today=datetime.now(ZoneInfo('Europe/Rome')).date().isoformat()
    return {'folders':folders,'documents':docs,'files':attachments,'deadlines':deadlines,
        'events':recent,'trash':trash,'today':today,'counts':{
          'documents':len(docs),'folders':len(folders),'attachments':len(attachments),
          'upcoming':sum(x['status']=='pending' and today<=x['due_date']<=(date.fromisoformat(today)+timedelta(days=30)).isoformat() for x in deadlines),
          'overdue':sum(x['status']=='pending' and x['due_date']<today for x in deadlines)}}

def exists(db,table,key,optional=True):
    if not key and optional:return None
    if not re.fullmatch(r'[0-9a-f]{32}',str(key)) or not db.execute(f'SELECT 1 FROM {table} WHERE id=? AND deleted_at IS NULL',(key,)).fetchone():
        raise ValueError('Elemento collegato non trovato o archiviato')
    return key

def log(db,kind,obj,action,details=''):
    db.execute('INSERT INTO vault_events VALUES (?,?,?,?,?,?)',(uid(),kind,obj,action,str(details)[:500],now()))

def next_date(due,frequency,anchor_day=None):
    dt=date.fromisoformat(due)
    if frequency=='monthly':
        year=dt.year+(dt.month//12);month=dt.month%12+1
        return date(year,month,min(anchor_day or dt.day,monthrange(year,month)[1])).isoformat()
    if frequency=='yearly':
        return date(dt.year+1,dt.month,min(anchor_day or dt.day,monthrange(dt.year+1,dt.month)[1])).isoformat()
    return due

def mutate(db,route,data):
    if not isinstance(data,dict):raise ValueError('Dati non validi')
    t=now();id=field(data,'id',32)
    if route=='/api/vault/folder/save':
        title=field(data,'title',180,True);category=field(data,'category',90)
        desc=field(data,'description',2000);kind=field(data,'link_kind',30);ref=field(data,'link_id',120)
        if kind not in ('','apartment','loan','other'):raise ValueError('Collegamento non valido')
        if not kind:ref=''
        if kind=='apartment' and ref not in ('0','1'):raise ValueError('Seleziona l’appartamento da collegare')
        if id:
            exists(db,'vault_folders',id,False)
            db.execute('UPDATE vault_folders SET title=?,category=?,description=?,link_kind=?,link_id=?,updated_at=? WHERE id=?',
                (title,category,desc,kind,ref,t,id));log(db,'folder',id,'modificato')
        else:
            id=uid();db.execute('INSERT INTO vault_folders VALUES (?,?,?,?,?,?,?,?,NULL)',(id,title,category,desc,kind,ref,t,t));log(db,'folder',id,'creato')
    elif route=='/api/vault/document/save':
        title=field(data,'title',180,True);category=field(data,'category',90)
        document_date=iso(field(data,'document_date',10));description=field(data,'description',2000)
        folder_id=exists(db,'vault_folders',field(data,'folder_id',32))
        if id:
            exists(db,'vault_documents',id,False)
            db.execute('UPDATE vault_documents SET title=?,category=?,document_date=?,description=?,folder_id=?,updated_at=? WHERE id=?',
                (title,category,document_date,description,folder_id,t,id));log(db,'document',id,'modificato')
        else:
            id=uid();db.execute('INSERT INTO vault_documents VALUES (?,?,?,?,?,?,?,?,NULL)',
                (id,folder_id,title,category,document_date,description,t,t));log(db,'document',id,'creato')
    elif route=='/api/vault/deadline/save':
        title=field(data,'title',180,True);due=iso(field(data,'due_date',10),True)
        freq=field(data,'recurrence',20) or 'none'
        if freq not in ('none','monthly','yearly'):raise ValueError('Ripetizione non valida')
        days=data.get('notice_days',30)
        if type(days) is not int or not 0<=days<=365:raise ValueError('Preavviso non valido')
        description=field(data,'description',2000)
        folder_id=exists(db,'vault_folders',field(data,'folder_id',32))
        document_id=exists(db,'vault_documents',field(data,'document_id',32))
        if id:
            exists(db,'vault_deadlines',id,False)
            db.execute('''UPDATE vault_deadlines SET title=?,folder_id=?,document_id=?,due_date=?,recurrence=?,notice_days=?,description=?,anchor_day=?,last_notified_for='',updated_at=? WHERE id=?''',
                  (title,folder_id,document_id,due,freq,days,description,date.fromisoformat(due).day,t,id));log(db,'deadline',id,'modificata',due)
        else:
            id=uid();db.execute('INSERT INTO vault_deadlines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)',
                  (id,title,folder_id,document_id,due,freq,days,'pending',description,date.fromisoformat(due).day,'',t,t));log(db,'deadline',id,'creata',due)
    elif route=='/api/vault/deadline/complete':
        exists(db,'vault_deadlines',id,False)
        due,freq,status,anchor=db.execute('SELECT due_date,recurrence,status,anchor_day FROM vault_deadlines WHERE id=?',(id,)).fetchone()
        if status=='completed':raise ValueError('Scadenza già completata')
        log(db,'deadline',id,'completata',due)
        if freq=='none':db.execute("UPDATE vault_deadlines SET status='completed',updated_at=? WHERE id=?",(t,id))
        else:
            nd=next_date(due,freq,anchor)
            db.execute("UPDATE vault_deadlines SET due_date=?,status='pending',last_notified_for='',updated_at=? WHERE id=?",(nd,t,id))
            log(db,'deadline',id,'prossima_ricorrenza',nd)
    elif route=='/api/vault/deadline/reopen':
        exists(db,'vault_deadlines',id,False)
        db.execute("UPDATE vault_deadlines SET status='pending',last_notified_for='',updated_at=? WHERE id=?",(t,id));log(db,'deadline',id,'riaperta')
    elif route=='/api/vault/item/delete':
        kind=field(data,'kind',20)
        if kind=='document':
            exists(db,'vault_documents',id,False)
            db.execute('UPDATE vault_documents SET deleted_at=? WHERE id=?',(t,id))
        elif kind=='folder':
            exists(db,'vault_folders',id,False)
            if db.execute('SELECT 1 FROM vault_documents WHERE folder_id=? AND deleted_at IS NULL',(id,)).fetchone() or db.execute('SELECT 1 FROM vault_deadlines WHERE folder_id=? AND deleted_at IS NULL',(id,)).fetchone():
                raise ValueError('Sposta prima i documenti e le scadenze collegate al fascicolo')
            db.execute('UPDATE vault_folders SET deleted_at=? WHERE id=?',(t,id))
        elif kind=='deadline':
            exists(db,'vault_deadlines',id,False)
            db.execute('UPDATE vault_deadlines SET deleted_at=? WHERE id=?',(t,id))
        else:raise ValueError('Tipo elemento non valido')
        log(db,kind,id,'cestinato')
    elif route=='/api/vault/item/restore':
        kind=field(data,'kind',20)
        table={'folder':'vault_folders','document':'vault_documents','deadline':'vault_deadlines','file':'vault_files'}.get(kind)
        if not table or not re.fullmatch('[0-9a-f]{32}',id):raise ValueError('Elemento non valido')
        if not db.execute(f'SELECT 1 FROM {table} WHERE id=? AND deleted_at IS NOT NULL',(id,)).fetchone():raise ValueError('Elemento non presente nel cestino')
        if kind=='file':
            if not db.execute('''SELECT 1 FROM vault_files f JOIN vault_documents d ON f.document_id=d.id
                   WHERE f.id=? AND d.deleted_at IS NULL''',(id,)).fetchone():
                raise ValueError('Ripristina prima il documento collegato')
            db.execute('UPDATE vault_files SET deleted_at=NULL WHERE id=?',(id,))
        else:
            db.execute(f'UPDATE {table} SET deleted_at=NULL,updated_at=? WHERE id=?',(t,id))
        log(db,kind,id,'ripristinato')
    elif route=='/api/vault/file/delete':
        exists(db,'vault_files',id,False)
        db.execute('UPDATE vault_files SET deleted_at=? WHERE id=?',(t,id));log(db,'file',id,'cestinato')
    else:raise ValueError('Operazione ZEUS Vault sconosciuta')
    return {'ok':True,'id':id}

def purge_trash(db):
    """Purge trashed records in a transaction; unlink only after successful COMMIT."""
    # Preserve live deadlines, detach references to documents being permanently deleted.
    db.execute("""UPDATE vault_deadlines SET document_id=NULL WHERE deleted_at IS NULL
        AND document_id IN (SELECT id FROM vault_documents WHERE deleted_at IS NOT NULL)""")
    disks = [r[0] for r in db.execute("""SELECT disk_name FROM vault_files
        WHERE deleted_at IS NOT NULL OR document_id IN
        (SELECT id FROM vault_documents WHERE deleted_at IS NOT NULL)""")]
    db.execute("""DELETE FROM vault_files WHERE deleted_at IS NOT NULL OR document_id IN
        (SELECT id FROM vault_documents WHERE deleted_at IS NOT NULL)""")
    counts = {'documents': db.execute('DELETE FROM vault_documents WHERE deleted_at IS NOT NULL').rowcount,
              'deadlines': db.execute('DELETE FROM vault_deadlines WHERE deleted_at IS NOT NULL').rowcount,
              'folders': db.execute('DELETE FROM vault_folders WHERE deleted_at IS NOT NULL').rowcount,
              'files': len(disks)}
    return counts,disks


def cleanup_disks(disks):
    """Best effort unlink after SQLite COMMIT. Never use user-controlled paths."""
    for disk in disks:
        if not re.fullmatch(r'[0-9a-f]{32}\.bin',disk):
            logging.error('Vault: filename non sicuro nel database, non cancellato')
            continue
        try: (ROOT/disk).unlink(missing_ok=True)
        except OSError: logging.exception('Vault: eliminazione fisica non riuscita')

def upload(db,doc_id,filename,contents):
    exists(db,'vault_documents',doc_id,False)
    if not isinstance(contents,bytes) or not 1<=len(contents)<=MAX_FILE:raise ValueError('Documento troppo grande: massimo 15 MB')
    name=field({'filename':filename},'filename',180,True)
    if '/' in name or '\\' in name or '\x00' in name or any(ord(c)<32 for c in name):raise ValueError('Nome file non valido')
    extension=Path(name).suffix.lower()
    if extension not in ALLOWED:raise ValueError('Formato non supportato: PDF, immagini, Word, Excel, TXT')
    if extension=='.pdf' and not contents.startswith(b'%PDF-'):raise ValueError('PDF non valido')
    if extension=='.png' and not contents.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Immagine PNG non valida')
    if extension in ('.jpg','.jpeg') and not contents.startswith(b'\xff\xd8\xff'):raise ValueError('Immagine JPEG non valida')
    if extension in ('.doc','.xls') and not contents.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):raise ValueError('Documento Office non valido')
    if extension in ('.docx','.xlsx') and not contents.startswith(b'PK\x03\x04'):raise ValueError('Documento Office non valido')
    if extension=='.txt':
        try:contents.decode('utf-8')
        except UnicodeDecodeError:raise ValueError('Il file TXT deve essere UTF-8')
    id=uid();disk=id+'.bin';temp=ROOT/(disk+'.part');dest=ROOT/disk
    try:
        with temp.open('xb') as f:
            os.chmod(temp,0o600);f.write(contents);f.flush();os.fsync(f.fileno())
        os.replace(temp,dest)
        db.execute('INSERT INTO vault_files VALUES (?,?,?,?,?,?,?,?,?,NULL)',
            (id,doc_id,name,ALLOWED[extension],len(contents),hashlib.sha256(contents).hexdigest(),disk,None,now()))
        db.execute('UPDATE vault_documents SET updated_at=? WHERE id=?',(now(),doc_id))
        log(db,'document',doc_id,'allegato_aggiunto',name)
    except BaseException:
        temp.unlink(missing_ok=True)
        # Orphan immutable files are harmless and can be reclaimed offline.
        raise
    return {'ok':True,'id':id,'filename':name}

def download(db,file_id):
    exists(db,'vault_files',file_id,False)
    row=db.execute('''SELECT f.filename,f.mime,f.size,f.sha256,f.disk_name
        FROM vault_files f JOIN vault_documents d ON f.document_id=d.id
        WHERE f.id=? AND d.deleted_at IS NULL''',(file_id,)).fetchone()
    if not row:raise ValueError('Allegato non disponibile: documento archiviato')
    filename,mime,size,digest,disk=row
    contents=(ROOT/disk).read_bytes()
    if len(contents)!=size or hashlib.sha256(contents).hexdigest()!=digest:
        raise OSError('Allegato mancante o non integro')
    return filename,mime,contents

def reminder_loop(db_factory,options_file):
    while True:
        try:
            opt=json.loads(Path(options_file).read_text()) if Path(options_file).exists() else {}
            if opt.get('notifications_enabled') and opt.get('notify_service'):
                moment=datetime.now(ZoneInfo('Europe/Rome'))
                today=moment.date()
                if moment.strftime('%H:%M')>=opt.get('notify_hour','09:00'):
                    with db_factory() as db:
                        due=rows(db,"SELECT id,title,due_date,notice_days,last_notified_for FROM vault_deadlines WHERE deleted_at IS NULL AND status='pending'")
                        for item in due:
                            delta=(date.fromisoformat(item['due_date'])-today).days
                            if delta>item['notice_days'] or item['last_notified_for']==item['due_date']:continue
                            suffix=f'mancano {delta} giorni' if delta>=0 else f'scaduta da {-delta} giorni (da verificare)'
                            advanced.send_notice(opt['notify_service'],f'ZEUS Vault · {item["title"]}: {item["due_date"]} ({suffix}).')
                            db.execute('UPDATE vault_deadlines SET last_notified_for=? WHERE id=?',(item['due_date'],item['id']))
                            db.commit()
        except Exception:logging.exception('ZEUS Vault: invio promemoria non riuscito')
        time.sleep(300)

def start_reminders(db_factory,options_file):
    threading.Thread(target=reminder_loop,args=(db_factory,options_file),daemon=True,name='zeus-vault-reminders').start()
