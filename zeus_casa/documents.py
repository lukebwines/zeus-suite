"""Original provider PDF attachments; never treated as readings or auto-posted."""
import hashlib
from datetime import datetime, timezone
import advanced

MAX_PDF=15*1024*1024

def init(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS invoice_versions (
        version_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, bill_id TEXT NOT NULL,
        filename TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
        data BLOB NOT NULL, original_added_at TEXT NOT NULL, replaced_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS invoice_versions_invoice ON invoice_versions(invoice_id);
    ''')

def validate(name,data):
    if not isinstance(data,bytes) or not 8<=len(data)<=MAX_PDF or not data.startswith(b'%PDF-'):
        raise ValueError('È richiesto un file PDF valido, massimo 15 MB.')
    filename=advanced.txt(name,160,True).replace('\\','_').replace('/','_').replace('"','_').replace('\n','_').replace('\r','_')
    if not filename.lower().endswith('.pdf'):raise ValueError('Il nome del documento deve terminare con .pdf')
    return filename

def list_documents(db):
    archive=advanced.get_archive(db)
    docs=[dict(zip(('id','bill_id','filename','size','sha256','added_at'),r)) for r in db.execute(
        'SELECT id,bill_id,filename,size,sha256,added_at FROM invoices ORDER BY added_at DESC')]
    indexed={}
    for item in docs:indexed.setdefault(item['bill_id'],[]).append(item)
    bills=[{'id':str(b['id']),'utility':b.get('utility',''),'start':b.get('start',''),
            'end':b.get('end',''),'invoiceNumber':b.get('invoiceNumber',''),
            'pdf_count':len(indexed.get(str(b['id']),[])),
            'documents':indexed.get(str(b['id']),[])} for b in archive.get('bills',[])]
    return {'bills':bills,'documents':docs,'missing':sum(not x['pdf_count'] for x in bills),
            'warning':'La bolletta non è allegata: non sarà disponibile il documento giustificativo per verificare consumi e importi. Potrai aggiungerlo successivamente.'}

def add(db,bill_id,filename,data):
    filename=validate(filename,data)
    return advanced.add_invoice(db,bill_id,filename,data)

def replace(db,doc_id,filename,data):
    filename=validate(filename,data)
    row=db.execute('SELECT id,bill_id,filename,size,sha256,data,added_at FROM invoices WHERE id=?',(doc_id,)).fetchone()
    if not row:raise ValueError('Documento da sostituire non trovato.')
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    db.execute('INSERT INTO invoice_versions VALUES (?,?,?,?,?,?,?,?,?)',
              (advanced.receipt_id(),row[0],row[1],row[2],row[3],row[4],row[5],row[6],now))
    db.execute('UPDATE invoices SET filename=?,size=?,sha256=?,data=?,added_at=? WHERE id=?',
              (filename,len(data),hashlib.sha256(data).hexdigest(),data,now,doc_id))
    advanced.log(db,'pdf_sostituito',doc_id+' '+filename)
    return {'ok':True,'id':doc_id,'previous_version_saved':True}
