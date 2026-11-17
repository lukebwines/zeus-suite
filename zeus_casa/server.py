#!/usr/bin/env python3
"""Casa Consumi self-hosted: web + shared SQLite archive; no third party dependencies."""
from datetime import datetime, timezone
import io
import tempfile
import shutil
import zipfile
import advanced
import debts
import finance
import finance_balance
import readings
import qr_readings
import workflow
import auth_session
import documents
import vault
import activity
import ha_integration
import full_backup
import login_branding
import tenant_handover
import tenant_deletions
import tenant_reports
import report_corrections
import threading
import urllib.error
from urllib.parse import parse_qs
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager, closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urlencode, quote

APP = Path(__file__).resolve().parent
DATA = Path(os.environ.get('CASA_DATA_DIR', '/data'))
DB_FILE = DATA / 'casa_consumi.sqlite3'
PORT = int(os.environ.get('CASA_PORT', '8090'))
OPTIONS_FILE = Path(os.environ.get('CASA_OPTIONS_FILE', '/data/options.json'))
MAX_BODY = 24 * 1024 * 1024
# In produzione cookie Secure attivo per default; CI HTTP può usare override locale.
COOKIE_SECURE = os.environ.get("CASA_COOKIE_SECURE", "1") != "0"


def config():
    options = json.loads(OPTIONS_FILE.read_text('utf-8')) if OPTIONS_FILE.exists() else {}
    username = os.environ.get('CASA_USERNAME', options.get('username', 'admin'))
    password = os.environ.get('CASA_PASSWORD', options.get('password', ''))
    if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or len(password) < 12:
        raise RuntimeError('Imposta username e una password di almeno 12 caratteri nella scheda Configurazione di Casa Consumi.')
    return username, password


USERNAME, PASSWORD = config()


@contextmanager
def db_connect():
    """Commit/rollback like sqlite3.Connection.__exit__, always close the handle."""
    connection = sqlite3.connect(DB_FILE, timeout=15)
    try:
        # Enforce declared referential constraints on this connection.
        # Legacy orphans are reported separately, never silently deleted.
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=15000')
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    with db_connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, archive TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO state (id,revision,archive) VALUES (1,0,?)', (
            json.dumps({'names': ['Appartamento 1', 'Appartamento 2'], 'bills': []}, ensure_ascii=False),))
        advanced.initialize(db)
        debts.initialize(db)
        finance.initialize(db)
        finance_balance.initialize(db)
        readings.initialize(db)
        tenant_handover.initialize(db)
        tenant_deletions.initialize(db)
        tenant_reports.initialize(db)
        qr_readings.initialize(db)
        documents.init(db)
        vault.initialize(db, DATA)
        activity.initialize(db)
        # Existing broken references are evidence to reconcile, not disposable data.
        orphans=db.execute('PRAGMA foreign_key_check').fetchmany(15)
        if orphans:
            logging.warning('ZEUS: rilevati riferimenti SQLite non risolti: %s',orphans)


def state():
    with db_connect() as db:
        row = db.execute('SELECT revision, archive FROM state WHERE id = 1').fetchone()
    if row is None:
        raise RuntimeError('Archivio assente')
    return {'revision': row[0], 'archive': json.loads(row[1])}


def check_archive(value):
    if not isinstance(value, dict):
        raise ValueError('Formato archivio non valido')
    names = value.get('names')
    bills = value.get('bills')
    tenants = value.get('tenants', [])
    if not isinstance(names, list) or len(names) != 2 or any(not isinstance(n, str) or not n.strip() or len(n) > 60 for n in names):
        raise ValueError('Nomi degli appartamenti non validi')
    if not isinstance(bills, list) or len(bills) > 10000 or not all(isinstance(b, dict) for b in bills):
        raise ValueError('Elenco bollette non valido')
    if not isinstance(tenants, list) or len(tenants) > 1000 or not all(isinstance(t, dict) for t in tenants):
        raise ValueError('Elenco inquilini non valido')
    for bill in bills:
        doc_no = bill.get('invoiceNumber', '')
        issued = bill.get('invoiceDate', '')
        if not isinstance(doc_no, str) or len(doc_no) > 80:
            raise ValueError('Numero fattura non valido (massimo 80 caratteri)')
        if not isinstance(issued, str):
            raise ValueError('Data di emissione non valida')
        if issued:
            try:
                if datetime.strptime(issued, '%Y-%m-%d').strftime('%Y-%m-%d') != issued:
                    raise ValueError()
            except ValueError:
                raise ValueError('Data di emissione non valida (AAAA-MM-GG)')
    return value


def validate_archive_changes(new, previous):
    """Validate new or changed records; preserve unchanged legacy data for audit.

    Older archives may already contain invalid historical records. Do not silently
    rewrite/delete them and do not let an unrelated change destroy access.
    """
    old_bills = {b.get('id'): b for b in previous.get('bills', []) if isinstance(b,dict)}
    tenant_ids = {t.get('id') for t in new.get('tenants', []) if isinstance(t,dict)}
    identifiers = set()
    invoice_keys = set()
    for bill in new['bills']:
        uid = bill.get('id')
        if not isinstance(uid,str) or not uid.strip() or len(uid)>128:
            raise ValueError('Identificativo bolletta mancante o non valido')
        if uid in identifiers:
            raise ValueError('Identificativo bolletta duplicato')
        identifiers.add(uid)
        if old_bills.get(uid) == bill:
            continue  # Preserve the historical record without altering it.
        if bill.get('utility') not in advanced.ALLOWED_UTILITIES:
            raise ValueError('Utenza della bolletta non valida')
        start = advanced.datecheck(bill.get('start'),optional=False)
        end = advanced.datecheck(bill.get('end'),optional=False)
        if start>end:
            raise ValueError('Periodo della bolletta non valido')
        amount = advanced.cents(bill.get('total'))
        fixed = advanced.cents(bill.get('fixed'))
        if amount<0 or fixed<0 or fixed>amount:
            raise ValueError('Importi della bolletta non coerenti')
        if any(k not in bill for k in ('generalStart','generalEnd','subStart','subEnd')):
            raise ValueError('Letture mancanti: verifica la ripartizione prima di salvare')
        quote = advanced.calc(bill)
        for idx in (1,2):
            tenant_id = bill.get(f'tenant{idx}Id','')
            if tenant_id and tenant_id not in tenant_ids:
                # An existing historical tenant may be archived; preserve its link.
                former = old_bills.get(uid,{}).get(f'tenant{idx}Id')
                if former != tenant_id:
                    raise ValueError('Conduttore della bolletta non presente in anagrafica')
            received = bill.get(f'received{idx}')
            if received is not None:
                paid = advanced.cents(received)
                if paid<0 or paid>quote[f'q{idx}']:
                    raise ValueError('Pagamento superiore alla quota dovuta o negativo')
            for suffix in ('due','paymentDate'):
                field = f'{suffix}{idx}'
                if field in bill:
                    advanced.datecheck(bill[field],optional=True)
        number = bill.get('invoiceNumber','').strip()
        if number:
            key = (bill['utility'],number.casefold(),bill.get('invoiceDate',''))
            if key in invoice_keys:
                raise ValueError('Numero e data fattura ripetuti nella modifica')
            invoice_keys.add(key)
    return new


def guard_electricity_two_subs(db, old, new):
    """Fail safe: the legacy form cannot silently create a one-sub bill after sub2 activation."""
    activated=readings.second_sub_from(db)
    if not activated:return
    oldby={str(b.get('id')):b for b in old.get('bills',[])}
    for bill in new.get('bills',[]):
        if bill.get('utility')!='electricity' or not bill.get('start','') or not bill.get('end',''):
            continue
        before=oldby.get(str(bill.get('id')))
        if before and all(before.get(k)==bill.get(k) for k in
                          ('start','end','generalStart','generalEnd','subStart','subEnd','sub2Start','sub2End','meterMode')):
            continue  # Tenant/payments and legacy pre-existing bills remain editable.
        if bill['start']<activated<=bill['end']:
            raise PermissionError('La bolletta attraversa l’attivazione del secondo sottocontatore: verifica e ripartisci manualmente il periodo.')
        if bill['end']>=activated and bill.get('meterMode')!='two_subs':
            raise PermissionError('Per l’elettricità con due sottocontatori utilizza Percorso Bollette: il vecchio modulo non acquisisce il secondo contatore.')


def make_backup(format, output_path=None):
    """Build a restorable archive using disk-backed streaming when output_path is set.

    Existing callers can still request the tuple of bytes. HTTP exports use a
    temporary file to avoid loading the SQLite snapshot and Vault into RAM.
    """
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_UTC')
    with tempfile.TemporaryDirectory(prefix='casa-consumi-backup-') as scratch:
        snapshot = Path(scratch) / 'casa_consumi.sqlite3'
        with db_connect() as original, closing(sqlite3.connect(snapshot)) as target:
            original.backup(target)
        with closing(sqlite3.connect(snapshot)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise sqlite3.DatabaseError('Copia SQLite non valida')
            revision, archive_str = db.execute('SELECT revision, archive FROM state WHERE id=1').fetchone()
            vault_paths=[r[0] for r in db.execute('SELECT disk_name FROM vault_files')]
        if format == 'sqlite':
            if output_path is not None:
                shutil.copyfile(snapshot,output_path)
                return (None,'application/vnd.sqlite3',f'casa-consumi-{timestamp}.sqlite3')
            return (snapshot.read_bytes(),'application/vnd.sqlite3',f'casa-consumi-{timestamp}.sqlite3')
        if format != 'zip':
            raise ValueError('Formato backup non consentito')
        archive=json.loads(archive_str)
        archive_blob=json.dumps({'format':'casa-consumi-v2','archive':archive},
            ensure_ascii=False,indent=2).encode('utf-8')
        guide = ("ZEUS CASA - BACKUP COMPLETO\\n\\n"
                 "Per ripristinare completamente i dati: ZEUS Casa > Amministrazione > Backup completo.\\n"
                 "Carica questo ZIP, verifica il riepilogo, conferma RIPRISTINA ZEUS e riavvia\\n"
                 "l'add-on da Home Assistant. I dati vengono sostituiti prima dell'avvio del server.\\n"
                 "Una copia dei dati precedenti resta in /data/zeus-restore/recovery.\\n"
                 "Il ripristino JSON NON sostituisce questo backup e non comprende le altre sezioni.\\n"
                 "Le credenziali e le opzioni dell'add-on sono gestite da Home Assistant e non\\n"
                 "vengono incluse o sovrascritte da questo ZIP.\\n"
                 "Il backup contiene dati personali: conservarlo in modo protetto.\\n").encode('utf-8')
        if len(vault_paths)+4 > full_backup.MAX_FILES:
            raise ValueError('Troppi allegati per un backup ripristinabile')
        paths=[]
        expanded=snapshot.stat().st_size+len(archive_blob)+len(guide)
        for stored in vault_paths:
            if not full_backup.FILE_NAME.fullmatch(stored):
                raise ValueError('Nome allegato non valido nel database')
            path=vault.ROOT/stored
            if not path.is_file():
                raise FileNotFoundError('Allegato Vault mancante: '+stored)
            expanded+=path.stat().st_size
            if expanded>full_backup.MAX_EXPANDED:
                raise ValueError('Backup troppo grande per i limiti di ripristino: verifica i dati')
            paths.append((stored,path))
        manifest = {
            'app':'ZEUS Casa','backup_version':3,
            'created_at_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'revision':revision,
            'files':['casa_consumi.sqlite3','archivio.json','manifest.json','LEGGIMI.txt']+
                    ['vault/files/'+name for name in vault_paths],
            'sha256':{},
        }
        target=Path(output_path) if output_path is not None else io.BytesIO()
        with zipfile.ZipFile(target,mode='w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            z.write(snapshot,arcname='casa_consumi.sqlite3')
            with snapshot.open('rb') as f:
                manifest['sha256']['casa_consumi.sqlite3']=full_backup.digest(f)
            for name,payload in (('archivio.json',archive_blob),('LEGGIMI.txt',guide)):
                z.writestr(name,payload)
                manifest['sha256'][name]=hashlib.sha256(payload).hexdigest()
            for stored,path in paths:
                name='vault/files/'+stored
                # Vault files can be removed concurrently: reject incomplete exports.
                z.write(path,arcname=name)
                with path.open('rb') as source:
                    manifest['sha256'][name]=full_backup.digest(source)
            z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        size=target.stat().st_size if output_path is not None else target.tell()
        if size>full_backup.MAX_UPLOAD:
            raise ValueError('Backup ZIP superiore al limite di ripristino (512 MB)')
        filename=f'casa-consumi-completo-{timestamp}.zip'
        return (None if output_path is not None else target.getvalue(),
                'application/zip',filename)

class Handler(BaseHTTPRequestHandler):
    server_version = 'ZEUSCasa/1.7.16'
    sys_version = ''

    def _headers(self, code, content_type, length, **custom):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'SAMEORIGIN' if content_type.startswith('text/html') and urlsplit(self.path).path not in ('/login','/login.html') else 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        if content_type.startswith('text/html'):
            self.send_header('Content-Security-Policy', "frame-ancestors 'self'" if urlsplit(self.path).path not in ('/login','/login.html') else "frame-ancestors 'none'")
        for key, val in custom.items(): self.send_header(key, val)
        self.end_headers()

    def _reply(self, status, data, content_type='application/json; charset=utf-8', **headers):
        payload = (json.dumps(data, ensure_ascii=False).encode('utf-8') if content_type.startswith('application/json') else data)
        self._headers(status, content_type, len(payload), **headers)
        self.wfile.write(payload)

    def _authorized(self):
        return auth_session.valid(self.headers.get('Cookie',''))

    def _auth(self):
        if self._authorized():return True
        route=urlsplit(self.path).path
        if self.command=='GET' and not route.startswith('/api/') and not route.endswith(('.js','.css')):
            dest='/login?'+urlencode({'next':route if route.startswith('/') else '/'})
            self._reply(303,b'', 'application/octet-stream',**{'Location':dest})
        else:
            self._reply(401, {'error':'Sessione scaduta o accesso richiesto. Apri /login.'})
        return False

    def _safe_origin(self):
        origin=self.headers.get('Origin','')
        if origin and urlsplit(origin).netloc.lower()!=self.headers.get('Host','').lower():
            self._reply(403,{'error':'Origine della richiesta non valida'})
            return False
        return True

    def _ha_api_authorized(self):
        # Separate, scoped read-only token. Deliberately NEVER accept a web session cookie.
        try:
            settings = json.loads(OPTIONS_FILE.read_text('utf-8')) if OPTIONS_FILE.exists() else {}
            secret = os.environ.get('CASA_HA_API_TOKEN', settings.get('ha_api_token', ''))
        except (OSError, ValueError, TypeError):
            secret = ''
        provided = self.headers.get('Authorization', '')
        if not isinstance(secret, str) or len(secret) < 32 or len(secret) > 256:
            return False
        if not provided.startswith('Bearer ') or len(provided) > 280:
            return False
        return hmac.compare_digest(provided[7:].encode('utf-8'), secret.encode('utf-8'))

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == '/api/ha/summary':
            if not self._ha_api_authorized():
                self._reply(401, {'error': 'Token integrazione Home Assistant non valido o disabilitato'})
                return
            try:
                with db_connect() as db:
                    result = ha_integration.summary(db)
                self._reply(200, result)
            except (ValueError, sqlite3.DatabaseError, KeyError, TypeError, ArithmeticError, RuntimeError):
                logging.exception('Impossibile calcolare il riepilogo Home Assistant')
                self._reply(503, {'error': 'Riepilogo Home Assistant temporaneamente non disponibile'})
            return
        if route == '/login-cortile.jpg':
            image = login_branding.read_photo(DATA)
            if image is None:
                self._reply(404, {'error': 'Nessuna fotografia personalizzata'})
            else:
                self._reply(200, image, 'image/jpeg')
            return
        if route in ('/login','/login.html','/login.js','/login.css'):
            file={'/login':'login.html','/login.html':'login.html','/login.js':'login.js','/login.css':'login.css'}[route]
            typ='text/html; charset=utf-8' if file.endswith('.html') else 'text/css; charset=utf-8' if file.endswith('.css') else 'application/javascript; charset=utf-8'
            self._reply(200,(APP/file).read_bytes(),typ);return
        # Public static branding assets: available on login screen before a session exists.
        # Exact allow-list: never map arbitrary user-supplied paths to the filesystem.
        public_branding = {
            '/favicon.ico': ('favicon.ico', 'image/x-icon'),
            '/favicon-16.png': ('favicon-16.png', 'image/png'),
            '/favicon-32.png': ('favicon-32.png', 'image/png'),
            '/zeus-icon.svg': ('zeus-icon.svg', 'image/svg+xml; charset=utf-8'),
            '/zeus-icon-192.png': ('zeus-icon-192.png', 'image/png'),
            '/zeus-icon-512.png': ('zeus-icon-512.png', 'image/png'),
            '/zeus-maskable-512.png': ('zeus-maskable-512.png', 'image/png'),
            '/apple-touch-icon.png': ('apple-touch-icon.png', 'image/png'),
            '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json; charset=utf-8'),
        }
        if route in public_branding:
            filename, media_type = public_branding[route]
            self._reply(200, (APP / filename).read_bytes(), media_type)
            return
        if route=='/api/session/status':
            self._reply(200,{'logged_in':self._authorized()});return
        if not self._auth(): return
        if route in ('/api/activity','/api/activity/csv'):
            try:
                import csv
                q=parse_qs(urlsplit(self.path).query)
                module=q.get('module',[''])[0];since=q.get('since',[''])[0]
                with db_connect() as db:
                    events=activity.list_events(db,limit=1000 if route.endswith('/csv') else 300,
                                                 module=module,since=since)
                if route.endswith('/csv'):
                    buf=io.StringIO();writer=csv.writer(buf)
                    columns=['created_at','actor','module','action','object_type','object_id','detail']
                    writer.writerow(columns)
                    for item in events:
                        # Prevent spreadsheet formula injection even if configuration is malicious.
                        writer.writerow([("'"+str(item[c]) if str(item[c]).startswith(('=','+','-','@')) else item[c]) for c in columns])
                    self._reply(200,('\ufeff'+buf.getvalue()).encode('utf-8'),'text/csv; charset=utf-8',
                                **{'Content-Disposition':'attachment; filename="zeus-registro-attivita.csv"'})
                else:self._reply(200,{'events':events,'limit':300})
            except ValueError as exc:self._reply(400,{'error':str(exc)})
            return
        if route == '/api/reports':
            with db_connect() as db:self._reply(200,{'reports':tenant_reports.list_reports(db),'issued_bills':tenant_reports.list_issued_bills(db),'payment_preferences':tenant_reports.payment_preferences(db),'allocation_sources':tenant_reports.allocation_sources(db),'allocations':tenant_reports.list_allocations(db),'generic_sources':report_corrections.generic_sources(db),'generic_assignments':report_corrections.assignments(db),'report_corrections':report_corrections.corrections(db)})
            return
        if route.startswith('/api/reports/correction/pdf/'):
            try:
                ident=route.rsplit('/',1)[-1]
                with db_connect() as db:blob=report_corrections.correction_pdf(db,ident)
                self._reply(200,blob,'application/pdf',**{'Content-Disposition':'attachment; filename="ZEUS_Nota_Rettifica_'+ident[:12]+'.pdf"'})
            except ValueError as exc:self._reply(404,{'error':str(exc)})
            return
        if route.startswith('/api/reports/pdf/'):
            try:
                with db_connect() as db: snapshot=tenant_reports.get(db,route.split('/')[-1])
                blob=tenant_reports.make_pdf(snapshot)
                self._reply(200,blob,'application/pdf',**{'Content-Disposition':'attachment; filename="ZEUS_Rendiconto_'+snapshot['label'].replace('/','-')+'.pdf"'})
            except ValueError as exc:self._reply(404,{'error':str(exc)})
            return
        if route == '/api/vault':
            with db_connect() as db:self._reply(200,vault.get(db))
            return
        if route.startswith('/api/vault/files/'):
            fid=route[len('/api/vault/files/'): ]
            try:
                with db_connect() as db:filename,mime,payload=vault.download(db,fid)
                # Always as attachment unless explicitly previewed; never interpreted as HTML.
                mode='inline' if parse_qs(urlsplit(self.path).query).get('preview',['0'])[0]=='1' and mime in ('application/pdf','image/jpeg','image/png') else 'attachment'
                safe='documento'+Path(filename).suffix.lower()
                disposition=f"{mode}; filename=\"{safe}\"; filename*=UTF-8''{quote(filename,safe='')}"
                self._reply(200,payload,mime,**{'Content-Disposition':disposition})
            except ValueError as exc:self._reply(404,{'error':str(exc)})
            except OSError:
                logging.exception('ZEUS Vault allegato non leggibile')
                self._reply(500,{'error':'Allegato mancante o corrotto; verifica il backup'})
            return
        if route == '/api/documents':
            with db_connect() as db:self._reply(200,documents.list_documents(db))
            return
        if route == '/api/backup/restore/status':
            self._reply(200,full_backup.status(DATA));return
        if route in ('/api/backup/sqlite', '/api/backup/zip'):
            try:
                fmt = 'sqlite' if route.endswith('sqlite') else 'zip'
                with tempfile.TemporaryDirectory(prefix='zeus-export-') as scratch:
                    output=Path(scratch)/('backup.sqlite3' if fmt=='sqlite' else 'backup.zip')
                    _,kind,filename=make_backup(fmt,output)
                    self._headers(200,kind,output.stat().st_size,
                        **{'Content-Disposition':f'attachment; filename="{filename}"'})
                    with output.open('rb') as source:
                        while True:
                            part=source.read(1024*1024)
                            if not part:break
                            self.wfile.write(part)
            except (sqlite3.DatabaseError, OSError, ValueError, TypeError):
                logging.exception('Error preparing backup')
                self._reply(500, {'error': 'Impossibile creare il backup'})
            return
        if route == '/api/qr-admin':
            with db_connect() as db:
                self._reply(200, qr_readings.admin_info(db))
            return
        if route.startswith('/api/qr-admin/qr/'):
            parts=route[len('/api/qr-admin/qr/'):].split('/')
            if len(parts)!=2:
                self._reply(404,{'error':'QR non trovato'});return
            try:
                with db_connect() as db: picture=qr_readings.admin_qr_svg(db,*parts)
                self._reply(200,picture,'image/svg+xml; charset=utf-8',**{'Content-Disposition':'inline; filename=qr-contatore.svg'})
            except ValueError as ex:self._reply(404,{'error':str(ex)})
            return
        if route.startswith('/api/qr-admin/photo/'):
            key=route[len('/api/qr-admin/photo/'):]
            if len(key)!=32 or any(c not in '0123456789abcdef' for c in key):
                self._reply(404,{'error':'Fotografia non trovata'});return
            with db_connect() as db:
                record=db.execute('SELECT photo_data FROM qr_submissions WHERE id=?',(key,)).fetchone()
            if not record or not record[0]:
                self._reply(404,{'error':'Fotografia non disponibile per questa lettura'});return
            self._reply(200,record[0],'image/jpeg',**{'Content-Disposition':'inline; filename=lettura.jpg'})
            return
        if route == '/api/workflow':
            try:
                q=parse_qs(urlsplit(self.path).query)
                with db_connect() as db: self._reply(200,workflow.overview(db,q.get('as_of',[None])[0]))
            except ValueError as exc: self._reply(400, {'error':str(exc)})
            return
        if route == '/api/workflow/proposal':
            try:
                q=parse_qs(urlsplit(self.path).query)
                with db_connect() as db: self._reply(200,workflow.proposal(db,q.get('id',[''])[0],q.get('as_of',[None])[0]))
            except ValueError as exc: self._reply(400, {'error':str(exc)})
            return
        if route in ('/analisi','/analisi.html'):
            self._reply(200,(APP/'analisi.html').read_bytes(),'text/html; charset=utf-8');return
        if route in ('/analisi.js','/analisi.css'):
            self._reply(200,(APP/route[1:]).read_bytes(),
                'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8');return
        if route in ('/bollette','/bollette.html'):
            self._reply(200,(APP/'bollette.html').read_bytes(),'text/html; charset=utf-8');return
        if route in ('/bollette.js','/bollette.css'):
            self._reply(200,(APP/route[1:]).read_bytes(),
                'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8');return
        if route in ('/nuova-bolletta','/nuova-bolletta.html'):
            self._reply(200,(APP/'nuova-bolletta.html').read_bytes(),'text/html; charset=utf-8');return
        if route in ('/nuova-bolletta.js','/nuova-bolletta.css'):
            self._reply(200,(APP/route[1:]).read_bytes(),
                'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8');return
        if route in ('/percorso','/percorso.html'):
            self._reply(200,(APP/'percorso.html').read_bytes(),'text/html; charset=utf-8');return
        if route in ('/percorso.js','/percorso.css'):
            self._reply(200,(APP/route[1:]).read_bytes(),
                'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8');return
        if route == '/api/readings':
            try:
                q=parse_qs(urlsplit(self.path).query)
                with db_connect() as db:
                    self._reply(200, readings.get_all(db,q.get('as_of',[None])[0]))
            except ValueError as exc:
                self._reply(400,{'error':str(exc)})
            return
        if route.startswith('/api/readings/pending_pdf/'):
            key=route[len('/api/readings/pending_pdf/'):]
            with db_connect() as db:
                rec=db.execute('SELECT pdf_name,pdf_data FROM reading_pending WHERE id=?',(key,)).fetchone()
            if not rec or not rec[1]:
                self._reply(404,{'error':'PDF non trovato'});return
            safe=rec[0].replace('"','_').replace('\r','_').replace('\n','_')
            self._reply(200,rec[1],'application/pdf',**{'Content-Disposition':f'attachment; filename="{safe}"'})
            return
        if route in ('/letture','/letture.html'):
            self._reply(200,(APP/'letture.html').read_bytes(),'text/html; charset=utf-8');return
        if route in ('/letture.js','/letture.css','/qr-admin.js'):
            self._reply(200,(APP/route[1:]).read_bytes(),
                  'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8');return
        if route == '/api/finance/maintenance':
            with db_connect() as db: self._reply(200,finance_balance.maintenance(db))
            return
        if route == '/api/finance/balance':
            try:
                query=parse_qs(urlsplit(self.path).query)
                with db_connect() as db: self._reply(200,finance_balance.balance(db,query.get('year',[None])[0]))
            except ValueError as exc:self._reply(400,{'error':str(exc)})
            return
        if route == '/api/finance/debt/quote':
            query=parse_qs(urlsplit(self.path).query)
            loan_id=query.get('loan_id',[''])[0]
            when=query.get('date',[''])[0]
            try:
                with db_connect() as db:
                    self._reply(200,finance.debt_quote(db,loan_id,when))
            except ValueError as exc:self._reply(400,{'error':str(exc)})
            return
        if route == '/api/finance':
            try:
                query=parse_qs(urlsplit(self.path).query)
                with db_connect() as db: self._reply(200,finance.overview(db,query.get('year',[None])[0]))
            except ValueError as exc: self._reply(400,{'error':str(exc)})
            return
        if route in ('/vault','/vault.html'):
            self._reply(200,(APP/'vault.html').read_bytes(),'text/html; charset=utf-8');return
        if route in ('/vault.js','/vault.css'):
            self._reply(200,(APP/route[1:]).read_bytes(),
                'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8');return
        if route in ('/bilancio','/bilancio.html'):
            self._reply(200,(APP/'bilancio.html').read_bytes(),'text/html; charset=utf-8');return
        if route in ('/bilancio.js','/bilancio.css'):
            self._reply(200,(APP/route[1:]).read_bytes(),
                'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8');return
        if route in ('/finance','/finance.html'):
            self._reply(200,(APP/'finance.html').read_bytes(),'text/html; charset=utf-8');return
        if route in ('/finance.js','/finance.css'):
            self._reply(200,(APP/route[1:]).read_bytes(),
                'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8');return
        if route == '/api/debts':
            try:
                query=parse_qs(urlsplit(self.path).query)
                at=query.get('at',[None])[0]
                with db_connect() as db: self._reply(200, debts.get_all(db,at))
            except ValueError as exc:
                self._reply(400, {'error':str(exc)})
            return
        if route in ('/debiti','/debiti.html'):
            self._reply(200, (APP/'debiti.html').read_bytes(), 'text/html; charset=utf-8');return
        if route in ('/debiti.js','/debiti.css'):
            self._reply(200, (APP/route[1:]).read_bytes(), 'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8');return
        if route == '/api/advanced':
            with db_connect() as db:
                self._reply(200, advanced.get_all(db))
            return
        if route.startswith('/api/invoices/'):
            uid=route[len('/api/invoices/'):]
            with db_connect() as db:
                record=db.execute('SELECT filename,data FROM invoices WHERE id=?',(uid,)).fetchone()
            if not record:
                self._reply(404, {'error':'Documento non trovato'});return
            safe_name=record[0].replace('"','_').replace('\r','_').replace('\n','_')
            self._reply(200, record[1], 'application/pdf', **{'Content-Disposition': f'attachment; filename="{safe_name}"'})
            return
        if route in ('/documenti','/documenti.html'):
            self._reply(200,(APP/'documenti.html').read_bytes(),'text/html; charset=utf-8');return
        if route in ('/documenti.js','/documenti.css','/auth-ui.js','/document-helper.js'):
            self._reply(200,(APP/route[1:]).read_bytes(),'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8');return
        if route in ('/activity','/immobili','/settings','/backup','/manutenzione','/activity.js','/immobili.js','/settings.js','/manutenzione.js','/zeus-admin.css'):
            name = ('settings.html' if route=='/backup' else route[1:]+('.html' if route in ('/activity','/immobili','/settings','/manutenzione') else ''))
            typ = ('text/html; charset=utf-8' if name.endswith('.html') else
                   'text/css; charset=utf-8' if name.endswith('.css') else 'application/javascript; charset=utf-8')
            self._reply(200,(APP/name).read_bytes(),typ);return
        if route in ('/gestione','/gestione.html'):
            data=(APP/'gestione.html').read_bytes()
            self._reply(200,data,'text/html; charset=utf-8');return
        if route in ('/gestione.js','/gestione.css','/nav.js','/zeus-nav.css'):
            data=(APP/route[1:]).read_bytes()
            self._reply(200,data,'application/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8');return
        if route == '/api/state':
            try: self._reply(200, state())
            except Exception:
                logging.exception('Error reading state')
                self._reply(500, {'error':'Impossibile leggere l’archivio sul server'})
            return
        if route in ('/', '/home.html', '/home.css', '/home.js', '/shell.js'):
            name = 'shell.html' if route == '/' else route[1:]
            ctype = ('text/html; charset=utf-8' if name.endswith('.html') else
                     'text/css; charset=utf-8' if name.endswith('.css') else 'application/javascript; charset=utf-8')
            self._reply(200, (APP / name).read_bytes(), ctype)
            return
        if route in ('/casa', '/index.html', '/bootstrap.js', '/app.js'):
            name = 'index.html' if route == '/casa' else route[1:]
            data = (APP / name).read_bytes()
            ctype = 'application/javascript; charset=utf-8' if name.endswith('.js') else 'text/html; charset=utf-8'
            self._reply(200, data, ctype)
            return
        self._reply(404, {'error': 'Risorsa inesistente'})

    def do_PUT(self):
        if not self._auth() or not self._safe_origin(): return
        if full_backup.status(DATA)['armed']:
            self._reply(423,{'error':'Ripristino programmato: riavvia ZEUS da Home Assistant prima di modificare i dati.'});return
        if urlsplit(self.path).path != '/api/state':
            self._reply(404, {'error': 'Risorsa inesistente'})
            return
        if (self.headers.get('Content-Type','').split(';')[0].strip().lower() != 'application/json'
                or self.headers.get('X-Casa-Consumi') != '1'):
            self._reply(403, {'error': 'Richiesta non autorizzata'})
            return
        try: length = int(self.headers.get('Content-Length', '-1'))
        except ValueError: length = -1
        if length < 2 or length > MAX_BODY:
            self._reply(413, {'error': 'Dimensioni del file non consentite'})
            return
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or type(body.get('revision')) is not int or body['revision'] < 0:
                raise ValueError('Revisione mancante o non valida')
            archive = check_archive(body.get('archive'))
            with db_connect() as db:
                db.execute('BEGIN IMMEDIATE')
                old_rev = db.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]
                if old_rev != body['revision']:
                    db.rollback()
                    self._reply(409, {'error': 'Archivio modificato da un altro dispositivo. Ricarica.'})
                    return
                old_archive=advanced.get_archive(db)
                validate_archive_changes(archive, old_archive)
                tenant_deletions.sync(db, old_archive, archive)
                archive=advanced.enrich(old_archive,archive)
                advanced.guard_closed(db, old_archive, archive)
                guard_electricity_two_subs(db, old_archive, archive)
                tenant_handover.sync(db, old_archive, archive)
                archive_json = json.dumps(archive, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                new_rev = old_rev + 1
                db.execute('UPDATE state SET revision=?, archive=? WHERE id=1', (new_rev, archive_json))
                activity.state_change(db,USERNAME,old_archive,archive)
                db.commit()
            self._reply(200, {'revision': new_rev, 'archive': archive})
        except PermissionError as exc:
            self._reply(423, {'error': str(exc)})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._reply(400, {'error': str(exc)})
        except (sqlite3.DatabaseError, OSError):
            logging.exception('Error writing state')
            self._reply(500, {'error': 'Salvataggio server non riuscito'})

    def do_POST(self):
        parsed=urlsplit(self.path); route=parsed.path
        if not self._safe_origin():return
        if route!='/api/backup/restore/confirm' and full_backup.status(DATA)['armed']:
            self._reply(423,{'error':'Ripristino programmato: riavvia ZEUS da Home Assistant prima di modificare i dati.'});return
        if self.headers.get('X-Casa-Consumi')!='1':
            self._reply(403,{'error':'Richiesta non autorizzata'});return
        if route != '/api/session/login' and not self._auth():return
        if route == '/api/backup/restore/upload':
            if not self._auth():return
            if self.headers.get('Content-Type','').split(';')[0]!='application/zip':
                self._reply(415,{'error':'Seleziona un file di backup ZIP completo ZEUS'});return
            try:
                length=int(self.headers.get('Content-Length','-1'))
                summary=full_backup.stage_stream(DATA,self.rfile,length)
                self._reply(200,{'ok':True,'verified':True,'summary':summary})
            except (ValueError,zipfile.BadZipFile,sqlite3.DatabaseError,RuntimeError,OSError) as exc:
                logging.warning('Backup respinto: %s',exc)
                self._reply(400,{'error':str(exc)[:300]})
            return
        try:
            length=int(self.headers.get('Content-Length','-1'))
            if length<1 or length>MAX_BODY or (route=='/api/session/login' and length>4096):
                self._reply(413,{'error':'Richiesta troppo grande'});return
            raw=self.rfile.read(length)
            if route=='/api/backup/restore/confirm':
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Richiesto JSON')
                info=json.loads(raw)
                if not isinstance(info,dict):raise ValueError('Conferma non valida')
                out=full_backup.arm_restore(DATA,info.get('confirm'))
                self._reply(200,out);return
            if route=='/api/session/login':
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Richiesto JSON')
                obj=json.loads(raw)
                if not isinstance(obj,dict):raise ValueError('Credenziali non valide')
                user=obj.get('username');password=obj.get('password')
                if not isinstance(user,str) or not isinstance(password,str) or len(user)>150 or len(password)>300:
                    raise ValueError('Credenziali non valide')
                token,error=auth_session.login(user,password,USERNAME,PASSWORD,self.client_address[0])
                if error:self._reply(429 if error.startswith('Riprova') else 401,{'error':error});return
                with db_connect() as db:activity.event(db,USERNAME,'ZEUS Casa','accesso','sessione')
                self._reply(200,{'ok':True},**{'Set-Cookie':auth_session.cookie(token,COOKIE_SECURE)});return
            if not self._auth():return
            if route == '/api/login/photo':
                if self.headers.get('Content-Type', '').split(';')[0] != 'image/jpeg':
                    raise ValueError('Seleziona una fotografia JPEG.')
                if length > login_branding.MAX_UPLOAD:
                    raise ValueError('Fotografia troppo grande (massimo 8 MB).')
                self._reply(200, login_branding.save_photo(DATA, raw))
                return
            if route=='/api/session/logout':
                auth_session.logout(self.headers.get('Cookie',''))
                with db_connect() as db:activity.event(db,USERNAME,'ZEUS Casa','uscita','sessione')
                self._reply(200,{'ok':True},**{'Set-Cookie':auth_session.clear_cookie(COOKIE_SECURE)})
                return
            if route == '/api/vault/files/upload':
                if self.headers.get('Content-Type','').split(';')[0] != 'application/octet-stream':
                    raise ValueError('Formato del caricamento non valido')
                q=parse_qs(parsed.query)
                with db_connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    out=vault.upload(db,q.get('document_id',[''])[0],q.get('filename',[''])[0],raw)
                    activity.mutation(db,USERNAME,route,{},out)
                    db.commit()
                self._reply(200,out);return
            if route in ('/api/documents/upload','/api/documents/replace'):
                if self.headers.get('Content-Type','').split(';')[0]!='application/pdf':
                    raise ValueError('Richiesto PDF')
                q=parse_qs(parsed.query)
                with db_connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if route.endswith('upload'):
                        out=documents.add(db,q.get('bill_id',[''])[0],q.get('filename',[''])[0],raw)
                    else:
                        out=documents.replace(db,q.get('id',[''])[0],q.get('filename',[''])[0],raw)
                    activity.mutation(db,USERNAME,route,{},out)
                    db.commit()
                self._reply(200,out);return
            if route=='/api/readings/pending_pdf':
                if self.headers.get('Content-Type','').split(';')[0]!='application/pdf':
                    raise ValueError('Richiesto PDF')
                q=parse_qs(parsed.query)
                with db_connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    out=readings.add_pdf(db,q.get('id',[''])[0],q.get('filename',[''])[0],raw)
                    activity.mutation(db,USERNAME,route,{},out)
                    db.commit()
                self._reply(200,out);return
            if route=='/api/invoices':
                if self.headers.get('Content-Type','').split(';')[0]!='application/pdf':
                    raise ValueError('Richiesto PDF')
                q=parse_qs(parsed.query)
                bill_id=q.get('bill_id',[''])[0]; filename=q.get('filename',[''])[0]
                with db_connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    out=advanced.add_invoice(db,bill_id,filename,raw)
                    activity.mutation(db,USERNAME,route,{},out)
                    db.commit()
                self._reply(200,out);return
            if route in ('/api/reports/allocate','/api/reports/revoke-allocation','/api/reports/assign-generic','/api/reports/correct-issued'):
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Richiesto JSON')
                obj=json.loads(raw)
                with db_connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if route.endswith('revoke-allocation'):
                        out=tenant_reports.revoke_allocation(db,obj)
                    elif route.endswith('assign-generic'):
                        out=report_corrections.assign_generic(db,obj,USERNAME)
                    elif route.endswith('correct-issued'):
                        out=report_corrections.correct_issued(db,obj,USERNAME)
                    else:
                        out=tenant_reports.allocate(db,obj)
                    activity.mutation(db,USERNAME,route,obj,out)
                    db.commit()
                self._reply(200,out);return
            if route == '/api/reports/payment-settings':
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Richiesto JSON')
                obj=json.loads(raw)
                with db_connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    out=tenant_reports.save_payment_preferences(db,obj)
                    activity.mutation(db,USERNAME,route,{'apartment':obj.get('apartment')},out)
                    db.commit()
                self._reply(200,out);return
            if route in ('/api/reports/preview','/api/reports/issue'):
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Richiesto JSON')
                obj=json.loads(raw)
                with db_connect() as db:
                    if route.endswith('/preview'):
                        report=tenant_reports.preview(db,obj)
                    else:
                        db.execute('BEGIN IMMEDIATE')
                        result=tenant_reports.issue(db,obj)
                        activity.mutation(db,USERNAME,route,obj,result)
                        db.commit()
                if route.endswith('/preview'):
                    self._reply(200,tenant_reports.make_pdf(report),'application/pdf',**{
                      'Content-Disposition':'attachment; filename="ZEUS_Anteprima_Rendiconto.pdf"'})
                else:self._reply(200,result)
                return
            if route=='/api/workflow/post':
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Richiesto JSON')
                obj=json.loads(raw)
                with db_connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    out=workflow.post_document(db,obj)
                    activity.mutation(db,USERNAME,route,{},out)
                    db.commit()
                self._reply(200,out);return
            if route=='/api/advanced/invoice/delete':
                obj=json.loads(raw)
                with db_connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if db.execute('DELETE FROM invoices WHERE id=?',(obj.get('id',''),)).rowcount!=1:
                        raise ValueError('Fattura non trovata')
                    advanced.log(db,'fattura_eliminata',str(obj.get('id','')))
                    activity.mutation(db,USERNAME,route,obj)
                    db.commit()
                self._reply(200,{'ok':True});return
            if route=='/api/advanced/notify/test':
                opts=json.loads(OPTIONS_FILE.read_text()) if OPTIONS_FILE.exists() else {}
                svc=opts.get('notify_service','')
                advanced.send_notice(svc,'Notifica di prova dal gestionale Casa Consumi.')
                self._reply(200,{'ok':True});return
            if route=='/api/vault/trash/purge':
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Richiesto JSON')
                obj=json.loads(raw)
                if not isinstance(obj,dict) or obj.get('confirm')!='ELIMINA DEFINITIVAMENTE':
                    raise ValueError('Conferma esplicita richiesta')
                with db_connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    counts,disks=vault.purge_trash(db)
                    activity.event(db,USERNAME,'ZEUS Vault','svuotamento_cestino','cestino')
                    db.commit()
                vault.cleanup_disks(disks)
                self._reply(200,{'ok':True,'counts':counts});return
            if not (route.startswith('/api/qr-admin/') or route.startswith('/api/advanced/') or route.startswith('/api/debts/') or route.startswith('/api/readings/') or route.startswith('/api/finance/') or route.startswith('/api/vault/')):
                self._reply(404,{'error':'Risorsa inesistente'});return
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                raise ValueError('Richiesto JSON')
            obj=json.loads(raw)
            if not isinstance(obj,dict): raise ValueError('Formato errato')
            with db_connect() as db:
                db.execute('BEGIN IMMEDIATE')
                result=(qr_readings.admin_mutate(db,route,obj) if route.startswith('/api/qr-admin/') else
                        debts.mutate(db,route,obj) if route.startswith('/api/debts/') else
                        finance.mutate(db,route,obj) if route.startswith('/api/finance/') else
                        vault.mutate(db,route,obj) if route.startswith('/api/vault/') else
                        readings.mutate(db,route,obj) if route.startswith('/api/readings/') else advanced.mutate(db,route,obj))
                activity.mutation(db,USERNAME,route,obj,result)
                db.commit()
            self._reply(200,result)
        except PermissionError as exc:
            self._reply(423,{'error':str(exc)})
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
            self._reply(400,{'error':str(exc)})
        except (sqlite3.DatabaseError,OSError,RuntimeError,urllib.error.URLError) as exc:
            logging.exception('Gestione richiesta non riuscita')
            self._reply(500,{'error':'Operazione fallita: '+str(exc)[:200]})


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    full_backup.apply_at_startup(DATA,initialize)
    initialize()
    advanced.start_reminders(db_connect, OPTIONS_FILE)
    vault.start_reminders(db_connect, OPTIONS_FILE)
    qr_thread=threading.Thread(target=qr_readings.serve,args=(db_connect,),daemon=True,name='ZEUSReading8091')
    qr_thread.start()
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    logging.info('Casa Consumi server online on TCP %d (data path: %s)', PORT, DB_FILE)
    server.serve_forever(poll_interval=0.4)


if __name__ == '__main__':
    main()
