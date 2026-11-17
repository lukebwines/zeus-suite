"""ZEUS Casa Reading: anonymous-but-secret meter intake, always pending.

A completely separate HTTP listener serves the public QR intake. The admin
server authenticates every /api/qr-admin route using the existing credentials.
No public request can enter the authenticated application handler.
"""
from __future__ import annotations

import base64
import datetime as dt
from decimal import Decimal
import hashlib
import io
import hmac
import json
import logging
import ipaddress
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

import readings
import advanced

APP = Path(__file__).resolve().parent
PUBLIC_PORT = int(os.environ.get('CASA_READING_PORT', '8091'))
def configured_public_url():
    """Use the add-on setting, preserving the currently printed QR domain by default."""
    from urllib.parse import urlsplit
    raw = os.environ.get('CASA_READING_PUBLIC_URL')
    if raw is None:
        try:
            options = json.loads(Path(os.environ.get('CASA_OPTIONS_FILE', '/data/options.json')).read_text('utf-8'))
            raw = options.get('reading_public_url')
        except (FileNotFoundError, OSError, ValueError, TypeError, AttributeError):
            raw = None
    url = (raw or 'https://zeusreading.archis.uk').strip().rstrip('/')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname
            or parsed.username or parsed.password or parsed.path
            or parsed.query or parsed.fragment):
        raise ValueError('Indirizzo pubblico ZEUS Reading non valido: usare un URL HTTPS senza percorso')
    return url


PUBLIC_URL = configured_public_url()
UTILITIES = readings.UTILITIES
METERS = readings.METERS
PHOTO_MAX = 3 * 1024 * 1024
BODY_MAX = 5 * 1024 * 1024
PIN_RE = re.compile(r'^\d{4}$')
TOKEN_RE = re.compile(r'^[a-zA-Z0-9_-]{35,60}$')
TIMEZONE = ZoneInfo('Europe/Rome')
Image.MAX_IMAGE_PIXELS = 12000000
REJECTION_REASONS = ('Fotografia illeggibile', 'Lettura digitata errata', 'Contatore non corrispondente', 'Lettura duplicata', 'Altro motivo')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def local_day():
    return dt.datetime.now(TIMEZONE).date().isoformat()


def make_pin_hash(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac('sha256', pin.encode('ascii'), salt, 260000).hex()


def new_secret():
    return secrets.token_urlsafe(32)


def initialize(db: sqlite3.Connection):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS qr_meters (
      utility TEXT NOT NULL, meter TEXT NOT NULL,
      token_hash TEXT NOT NULL UNIQUE,
      active INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT NOT NULL,
      PRIMARY KEY(utility,meter)
    );
    CREATE TABLE IF NOT EXISTS qr_tokens (
      utility TEXT NOT NULL, meter TEXT NOT NULL,
      token TEXT NOT NULL UNIQUE,
      PRIMARY KEY(utility,meter)
    );
    CREATE TABLE IF NOT EXISTS qr_submissions (
      id TEXT PRIMARY KEY,
      utility TEXT NOT NULL,
      meter TEXT NOT NULL,
      reading_date TEXT NOT NULL,
      value TEXT NOT NULL,
      photo_data BLOB NOT NULL,
      photo_mime TEXT NOT NULL,
      received_at TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
      reviewed_at TEXT NOT NULL DEFAULT '',
      review_note TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS qr_pending_idx ON qr_submissions(status,received_at);
    CREATE TABLE IF NOT EXISTS qr_settings (
      id INTEGER PRIMARY KEY CHECK(id=1), pin_salt TEXT NOT NULL, pin_hash TEXT NOT NULL,
      changed_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS qr_pin_attempts (
      token_hash TEXT PRIMARY KEY, failure_count INTEGER NOT NULL,
      first_failed REAL NOT NULL, locked_until REAL NOT NULL DEFAULT 0
    );
    ''')
    # Additive schema update: old QR submissions keep their photos and values.
    existing={r[1] for r in db.execute('PRAGMA table_info(qr_submissions)')}
    for column in ('register_f1','register_f2','register_f3'):
        if column not in existing:
            db.execute(f"ALTER TABLE qr_submissions ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")


def validate_pin(pin):
    if not isinstance(pin,str) or not PIN_RE.fullmatch(pin):
        raise ValueError('Il PIN deve contenere esattamente quattro cifre')
    if pin in {'0000','1111','1234','4321','9999','1212','2222','3333','4444','5555','6666','7777','8888','0123','9876'}:
        raise ValueError('Scegli un PIN meno prevedibile')
    return pin


def has_pin(db):
    return bool(db.execute('SELECT 1 FROM qr_settings WHERE id=1').fetchone())


def set_global_pin(db,pin):
    validate_pin(pin)
    salt=secrets.token_bytes(16)
    pin_hash=make_pin_hash(pin,salt)
    db.execute('''INSERT INTO qr_settings(id,pin_salt,pin_hash,changed_at) VALUES (1,?,?,?)
      ON CONFLICT(id) DO UPDATE SET pin_salt=excluded.pin_salt,
      pin_hash=excluded.pin_hash, changed_at=excluded.changed_at''',(salt.hex(),pin_hash,now()))
    db.execute('DELETE FROM qr_pin_attempts')
    advanced.log(db,'qr_pin_comune_aggiornato','Il PIN unico per tutti i contatori è stato modificato')
    return {'ok':True}


def new_meter(db, utility, meter, *, token_reset=True):
    utility=readings.utility(utility)
    meter=readings.meter(meter,utility)
    if not has_pin(db):raise ValueError('Configura prima il PIN comune di 4 cifre')
    old=db.execute('SELECT token FROM qr_tokens WHERE utility=? AND meter=?',(utility,meter)).fetchone()
    if old and not token_reset:token=old[0]
    else:token=new_secret()
    hashed=hashlib.sha256(token.encode('ascii')).hexdigest()
    db.execute('''INSERT INTO qr_meters(utility,meter,token_hash,active,updated_at)
       VALUES(?,?,?,1,?) ON CONFLICT(utility,meter) DO UPDATE SET
       token_hash=excluded.token_hash,active=1,updated_at=excluded.updated_at''',
       (utility,meter,hashed,now()))
    db.execute('''INSERT INTO qr_tokens(utility,meter,token) VALUES(?,?,?)
      ON CONFLICT(utility,meter) DO UPDATE SET token=excluded.token''',(utility,meter,token))
    db.execute('DELETE FROM qr_pin_attempts WHERE token_hash=?',(hashed,))
    advanced.log(db,'qr_configurato',f'{utility}/{meter} · QR {"rigenerato" if old else "creato"}')
    return {'utility':utility,'meter':meter,'url':PUBLIC_URL+'/r/'+token,'ok':True}


def admin_info(db):
    devices=[{'utility':u,'meter':m,'configured':False,'active':False,'url':''}
             for u in ('electricity','gas','water') for m in (('main','sub','sub2') if u=='electricity' else ('main','sub'))]
    rows={(x[0],x[1]):x for x in db.execute('''SELECT q.utility,q.meter,q.active,t.token
       FROM qr_meters q JOIN qr_tokens t ON q.utility=t.utility AND q.meter=t.meter''')}
    for d in devices:
        row=rows.get((d['utility'],d['meter']))
        if row:d.update(configured=True,active=bool(row[2]),url=PUBLIC_URL+'/r/'+row[3])
    sub=[dict(zip(('id','utility','meter','reading_date','value','received_at','status','reviewed_at','review_note','register_f1','register_f2','register_f3','has_photo'),r))
         for r in db.execute('''SELECT id,utility,meter,reading_date,value,received_at,status,reviewed_at,review_note,
                register_f1,register_f2,register_f3,(length(photo_data)>0) FROM qr_submissions ORDER BY received_at DESC LIMIT 300''')]
    return {'devices':devices,'submissions':sub,'public_url':PUBLIC_URL,'has_pin':has_pin(db)}


def admin_mutate(db, route, body):
    if route=='/api/qr-admin/pin':
        return set_global_pin(db,body.get('pin'))
    if route in ('/api/qr-admin/generate','/api/qr-admin/rotate'):
        u=readings.utility(body.get('utility'));m=readings.meter(body.get('meter'),u)
        configured=bool(db.execute('SELECT 1 FROM qr_meters WHERE utility=? AND meter=?',(u,m)).fetchone())
        if route.endswith('generate') and configured:raise ValueError('QR già presente: utilizza Rigenera QR')
        if route.endswith('rotate') and not configured:raise ValueError('QR non configurato')
        return new_meter(db,u,m)
    if route=='/api/qr-admin/toggle':
        u=readings.utility(body.get('utility'));m=readings.meter(body.get('meter'),u)
        active=body.get('active')
        if not isinstance(active,bool):raise ValueError('Stato non valido')
        if db.execute('UPDATE qr_meters SET active=?,updated_at=? WHERE utility=? AND meter=?',
             (int(active),now(),u,m)).rowcount!=1:raise ValueError('QR non configurato')
        advanced.log(db,'qr_attivazione',f'{u}/{m} → {active}')
        return {'ok':True}
    if route=='/api/qr-admin/restore':
        key=readings.text(body.get('id'),80,True)
        row=db.execute("SELECT utility,meter FROM qr_submissions WHERE id=? AND status='rejected'",(key,)).fetchone()
        if not row:raise ValueError('Si possono ripristinare soltanto letture rifiutate')
        db.execute('''UPDATE qr_submissions SET status='pending',reviewed_at='',review_note=''
            WHERE id=? AND status='rejected' ''',(key,))
        advanced.log(db,'qr_lettura_ripristinata',f'{row[0]}/{row[1]} {key}')
        return {'ok':True}
    if route=='/api/qr-admin/delete':
        key=readings.text(body.get('id'),80,True)
        # One record contains both image BLOB and metadata: DELETE removes both.
        row=db.execute("SELECT utility,meter FROM qr_submissions WHERE id=? AND status='rejected'",(key,)).fetchone()
        if not row:raise ValueError('È possibile eliminare solo una lettura rifiutata')
        db.execute('PRAGMA secure_delete=ON')
        db.execute("DELETE FROM qr_submissions WHERE id=? AND status='rejected'",(key,))
        advanced.log(db,'qr_lettura_eliminata',f'{row[0]}/{row[1]} {key} - lettura ed eventuale foto eliminate')
        return {'ok':True}
    if route=='/api/qr-admin/review':
        key=readings.text(body.get('id'),80,True)
        action=body.get('action')
        note=readings.text(body.get('note',''),300)
        if action not in ('approve','reject'):raise ValueError('Decisione non valida')
        row=db.execute('SELECT utility,meter,reading_date,value,status,register_f1,register_f2,register_f3 FROM qr_submissions WHERE id=?',(key,)).fetchone()
        if not row or row[4]!='pending':raise ValueError('Lettura non trovata o già esaminata')
        if action=='reject':
            if not note or note.split(' — ',1)[0] not in REJECTION_REASONS:
                raise ValueError('Indica un motivo di rifiuto valido')
        if action=='approve':
            detail=(f' · A1/F1={row[5]}; A2/F2={row[6]}; A3/F3={row[7]}'
                    if row[0]=='electricity' and row[1]=='main' and row[5]!='' else '')
            readings.mutate(db,'/api/readings/add',{'utility':row[0],'meter':row[1],
               'date':row[2],'value':row[3],
               'note':'ZEUS Reading · approvazione QR '+key[:12]+detail})
        db.execute('UPDATE qr_submissions SET status=?,reviewed_at=?,review_note=? WHERE id=?',
                   ('approved' if action=='approve' else 'rejected',now(),note,key))
        advanced.log(db,'qr_lettura_'+('approvata' if action=='approve' else 'rifiutata'),key)
        return {'ok':True}
    raise ValueError('Operazione QR non riconosciuta')

def admin_qr_svg(db, utility, meter):
    u=readings.utility(utility)
    m=readings.meter(meter,u)
    row=db.execute('''SELECT t.token FROM qr_tokens t JOIN qr_meters q
           ON q.utility=t.utility AND q.meter=t.meter WHERE t.utility=? AND t.meter=?''',(u,m)).fetchone()
    if not row:raise ValueError('QR non generato')
    sys.path.insert(0,str(APP/'qr_lib'))
    import qrcode
    qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,border=4,box_size=1)
    qr.add_data(PUBLIC_URL+'/r/'+row[0])
    qr.make(fit=True)
    matrix=qr.get_matrix();n=len(matrix)
    squares=['<rect x="%s" y="%s" width="1" height="1"/>'%(x,y)
             for y,line in enumerate(matrix) for x,on in enumerate(line) if on]
    # Print-only identification: the encoded URL, token and square matrix are unchanged.
    labels={
        ('electricity','main'):('Elettricità - Contatore principale','EL-GEN'),
        ('electricity','sub'):('Elettricità - Sottocontatore app. 1','EL-APP1'),
        ('electricity','sub2'):('Elettricità - Sottocontatore app. 2','EL-APP2'),
        ('gas','main'):('Gas - Contatore principale','GAS-GEN'),
        ('gas','sub'):('Gas - Sottocontatore app. 1','GAS-APP1'),
        ('water','main'):('Acqua - Contatore principale','ACQ-GEN'),
        ('water','sub'):('Acqua - Sottocontatore app. 1','ACQ-APP1'),
    }
    from html import escape
    title,code=labels[(u,m)]
    height=n+12
    svg=(f'<svg xmlns="http://www.w3.org/2000/svg" width="450" height="{round(450*height/n)}" '
         f'viewBox="0 0 {n} {height}" role="img" aria-label="{escape(title)}">'
         f'<title>{escape(title)} - {code}</title>'
         f'<rect width="{n}" height="{height}" fill="white"/>'
         f'<g fill="#143c33">'+''.join(squares)+'</g>'
         f'<text x="{n/2:.1f}" y="{n+4.4:.1f}" text-anchor="middle" '
         f'font-family="Arial,sans-serif" font-size="2.35" font-weight="bold" fill="#25483c">{escape(title)}</text>'
         f'<text x="{n/2:.1f}" y="{n+8.6:.1f}" text-anchor="middle" '
         f'font-family="Arial,sans-serif" font-size="2.3" fill="#547165">{code}</text></svg>')
    return svg.encode('utf-8')


def find_token(db, token):
    if not TOKEN_RE.fullmatch(token):return None
    hashed=hashlib.sha256(token.encode('ascii')).hexdigest()
    return db.execute('SELECT utility,meter FROM qr_meters WHERE token_hash=? AND active=1',(hashed,)).fetchone()


def allowed_attempt(db, token):
    # Persistent PIN throttle, including across application restarts.
    key=hashlib.sha256(token.encode('ascii')).hexdigest()
    row=db.execute('SELECT failure_count,first_failed,locked_until FROM qr_pin_attempts WHERE token_hash=?',(key,)).fetchone()
    t=dt.datetime.now(dt.timezone.utc).timestamp()
    return not row or (t>=row[2] and (t-row[1]>=900 or row[0]<5))


def failed_attempt(db, token):
    key=hashlib.sha256(token.encode('ascii')).hexdigest()
    t=dt.datetime.now(dt.timezone.utc).timestamp()
    row=db.execute('SELECT failure_count,first_failed FROM qr_pin_attempts WHERE token_hash=?',(key,)).fetchone()
    count=1 if not row or t-row[1]>=900 else row[0]+1
    first=t if count==1 else row[1]
    locked=t+900 if count>=5 else 0
    db.execute('''INSERT INTO qr_pin_attempts(token_hash,failure_count,first_failed,locked_until)
         VALUES(?,?,?,?) ON CONFLICT(token_hash) DO UPDATE SET failure_count=excluded.failure_count,
         first_failed=excluded.first_failed, locked_until=excluded.locked_until''',
         (key,count,first,locked))


def clear_attempts(db, token):
    db.execute('DELETE FROM qr_pin_attempts WHERE token_hash=?',
               (hashlib.sha256(token.encode('ascii')).hexdigest(),))

def valid_image(raw):
    # JPEG, mandatory; do not serve as HTML and never show on the public site.
    if not (1024<=len(raw)<=PHOTO_MAX and raw.startswith(b'\xff\xd8\xff') and raw[-2:]==b'\xff\xd9'):
        raise ValueError('Fotografia JPEG non valida o superiore a 3 MB')


def _stamp_font(px: int, bold=False):
    paths = (
        ('/usr/share/fonts/ttf-dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/ttf-dejavu/DejaVuSans.ttf'),
        ('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/dejavu/DejaVuSans.ttf'),
        ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    )
    for path in paths:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    return ImageFont.load_default()


def stamp_photo(raw: bytes, utility: str, meter: str, value: str, received_at: str, registers=None) -> bytes:
    """Produce a new JPEG with a footer, preserving the whole meter display.

    The timestamp is server receipt time, NOT a verified camera capture time.
    EXIF data is removed from the stored copy to protect location metadata.
    """
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format != 'JPEG' or source.width * source.height > 12000000:
                raise ValueError('Fotografia JPEG non valida o troppo grande')
            source = ImageOps.exif_transpose(source)
            source.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
            photo = source.convert('RGB')
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as ex:
        raise ValueError('Fotografia non decodificabile') from ex
    width, height = photo.size
    # A footer, not a translucent overlay: the meter digits remain unobscured.
    font_size=max(13, min(33, width//36))
    regular=_stamp_font(font_size)
    bold=_stamp_font(font_size, bold=True)
    label={'electricity':'ELETTRICITÀ','gas':'GAS','water':'ACQUA'}[utility]
    meter_label='CONTATORE PRINCIPALE' if meter=='main' else ('SOTTOCONTATORE APPARTAMENTO 2' if meter=='sub2' else 'SOTTOCONTATORE APPARTAMENTO 1')
    # received_at is a UTC timestamp produced by the server itself
    server_time=dt.datetime.fromisoformat(received_at).astimezone(TIMEZONE).strftime('%d/%m/%Y %H:%M:%S')
    lines=[('ZEUS READING · '+label+' · '+meter_label,bold),
           ('Ricezione server: '+server_time+' (Italia)',regular)]
    if registers:
        lines.append(('A1+/F1: '+registers[0]+' · A2+/F2: '+registers[1]+' · A3+/F3: '+registers[2]+' kWh',regular))
        lines.append(('Totale cumulativo A1+A2+A3: '+value+' kWh',regular))
    else:
        lines.append(('Lettura digitata: '+value+' '+('kWh' if utility=='electricity' else 'm³'),regular))
    gutter=max(10,width//60)
    line_h=max(19,int(font_size*1.4))
    footer_h=gutter*2+line_h*len(lines)
    output=Image.new('RGB',(width,height+footer_h),(22,48,40))
    output.paste(photo,(0,0))
    draw=ImageDraw.Draw(output)
    for i,(line,font) in enumerate(lines):
        # Fit the longest line without cutting it off on narrow phone images.
        adjusted=font
        if draw.textbbox((0,0),line,font=font)[2]>width-2*gutter:
            for sz in range(font_size-1,9,-1):
                candidate=_stamp_font(sz, bold=(i==0))
                if draw.textbbox((0,0),line,font=candidate)[2]<=width-2*gutter:
                    adjusted=candidate
                    break
        draw.text((gutter,height+gutter+i*line_h),line,font=adjusted,fill=(255,255,255))
    result=io.BytesIO()
    output.save(result,format='JPEG',quality=82,optimize=True)
    data=result.getvalue()
    if len(data)>4*1024*1024:
        raise ValueError('Fotografia elaborata troppo grande: riprova con una risoluzione inferiore')
    return data


def submit(db, token, obj):
    meter=find_token(db,token)
    if not meter:raise LookupError('Codice QR non valido o revocato')
    if not allowed_attempt(db,token):raise PermissionError('PIN temporaneamente bloccato: riprova più tardi')
    pin=obj.get('pin','')
    if not isinstance(pin,str) or not PIN_RE.fullmatch(pin):
        failed_attempt(db,token)
        raise PermissionError('PIN non corretto')
    current=db.execute('SELECT pin_salt,pin_hash FROM qr_settings WHERE id=1').fetchone()
    if not current:raise PermissionError('PIN non configurato')
    check=make_pin_hash(pin,bytes.fromhex(current[0]))
    if not hmac.compare_digest(check,current[1]):
        failed_attempt(db,token)
        raise PermissionError('PIN non corretto')
    clear_attempts(db,token)
    # The QR token decides the meter. Only the electricity MAIN meter accepts
    # the three active-energy registers; the server computes the authoritative sum.
    registers=None
    if meter[0]=='electricity' and meter[1]=='main':
        values=obj.get('registers')
        if not isinstance(values,dict) or set(values)!={'f1','f2','f3'}:
            raise ValueError('Per il generale elettrico inserisci A1/F1, A2/F2 e A3/F3.')
        vals=[readings.dec(values[k]) for k in ('f1','f2','f3')]
        registers=[str(x) for x in vals]
        value=str(sum(vals,Decimal('0')))
        # Value is always derived by the server, never from the smartphone.
    else:
        if obj.get('registers') is not None:
            raise ValueError('Le fasce F1/F2/F3 sono ammesse solo per il contatore generale elettrico.')
        value=str(readings.dec(obj.get('value')))
    is_electricity_main=(meter[0]=='electricity' and meter[1]=='main')
    if is_electricity_main:
        # The three readings appear on separate display pages; no photo is
        # collected or retained for this meter (old records stay unchanged).
        if obj.get('photo') is not None:
            raise ValueError('Il contatore elettrico generale non richiede fotografie.')
        photo=None
    else:
        raw_b64=obj.get('photo')
        if not isinstance(raw_b64,str) or len(raw_b64)>4*PHOTO_MAX//3+12:
            raise ValueError('Fotografia obbligatoria e massimo 3 MB')
        try:photo=base64.b64decode(raw_b64,validate=True)
        except (ValueError,base64.binascii.Error):raise ValueError('Fotografia non valida')
        valid_image(photo)
    existing=db.execute('''SELECT id FROM qr_submissions WHERE utility=? AND meter=? AND status='pending'
                  AND received_at>=?''', (meter[0],meter[1],
                  (dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=10)).isoformat(timespec='seconds'))).fetchone()
    if existing:raise ValueError('Esiste già una lettura inviata negli ultimi 10 minuti per questo contatore')
    recent=db.execute('SELECT count(*) FROM qr_submissions WHERE received_at>=?',((dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=1)).isoformat(timespec='seconds'),)).fetchone()[0]
    if recent >= 100: raise ValueError('Limite giornaliero delle letture raggiunto')
    received_at=now()
    # The legacy schema keeps photo_data NOT NULL; an empty blob is a sentinel
    # for readings intentionally collected without photo. New images never use it.
    stamped=(b'' if photo is None else stamp_photo(photo,meter[0],meter[1],value,received_at,registers))
    recid=secrets.token_hex(16)
    db.execute('''INSERT INTO qr_submissions
      (id,utility,meter,reading_date,value,photo_data,photo_mime,received_at,status,register_f1,register_f2,register_f3)
      VALUES(?,?,?,?,?,?,?,?,'pending',?,?,?)''',
      (recid,meter[0],meter[1],local_day(),value,stamped,('' if photo is None else 'image/jpeg'),received_at,
       *(registers if registers else ('','',''))))
    advanced.log(db,'qr_lettura_inviata',f'{meter[0]}/{meter[1]} {recid}')
    return {'ok':True,'message':'Lettura ricevuta e in attesa di approvazione','reference':recid[:10]}


class PublicHandler(BaseHTTPRequestHandler):
    server_version='ZEUSReading/1.0'
    sys_version=''
    def log_message(self,fmt,*args):
        # Never print QR secrets, PIN, querystrings or paths to add-on logs.
        logging.info('ZEUS Reading HTTP %s',getattr(self,'_status', '?'))
    def _answer(self,code,body,kind='application/json; charset=utf-8'):
        data=json.dumps(body,ensure_ascii=False).encode('utf-8') if kind.startswith('application/json') else body
        self._status=code
        self.send_response(code)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'")
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Permissions-Policy','camera=(self), geolocation=()')
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        route=urlsplit(self.path).path
        if route in ('/reader.js','/reader.css'):
            kind='application/javascript; charset=utf-8' if route.endswith('js') else 'text/css; charset=utf-8'
            self._answer(200,(APP/route[1:]).read_bytes(),kind);return
        if route.startswith('/r/'):
            token=route[3:]
            with self.server.db_connection() as db:
                meter=find_token(db,token)
                if not meter:self._answer(404,{'error':'QR inesistente o disattivato'});return
            unit={'electricity':'ELETTRICITÀ','gas':'GAS','water':'ACQUA'}[meter[0]]
            meter_name={'main':'CONTATORE PRINCIPALE','sub':'SOTTOCONTATORE · APPARTAMENTO 1','sub2':'SOTTOCONTATORE · APPARTAMENTO 2'}[meter[1]]
            multi=(meter[0]=='electricity' and meter[1]=='main')
            active_inputs=('''<fieldset class="registers" data-register-group><legend>Energia attiva · letture per fascia (kWh)</legend>
                <label>A1+ (F1)<input name="f1" type="number" step="0.0001" min="0" required inputmode="decimal" placeholder="Es. 2701"></label>
                <label>A2+ (F2)<input name="f2" type="number" step="0.0001" min="0" required inputmode="decimal" placeholder="Es. 2140"></label>
                <label>A3+ (F3)<input name="f3" type="number" step="0.0001" min="0" required inputmode="decimal" placeholder="Es. 2927"></label>
                <p class="register-total" aria-live="polite">Totale cumulativo: <strong id="register-total">—</strong> kWh</p>
                <small>Leggi solo A1+, A2+, A3+. Non inserire R1+, R2+, R3+ (energia reattiva).</small></fieldset>'''
                if multi else '''<label>Lettura visualizzata sul contatore<input name="value" type="number" step="0.0001" min="0" required placeholder="Es. 12345,67"></label>''')
            photo_fields=('' if multi else '''<label>Fotografia obbligatoria del display<input name="photo" type="file" accept="image/*" capture="environment" required><small>Scatta una foto nitida, senza persone né altri dati personali. Max 3 MB dopo la compressione.</small></label><div id="preview" hidden><img id="photo-preview" alt="Anteprima della fotografia del contatore"></div>''')
            instructions=("Inserisci le tre letture di energia attiva A1+, A2+ e A3+. Il totale sarà calcolato automaticamente. Non è richiesta alcuna foto."
                          if multi else "Scansiona l'etichetta, inserisci il valore e scatta una fotografia del display. La lettura sarà inviata per l'approvazione.")
            content=((APP/'reader.html').read_text('utf-8')
                     .replace('__TOKEN__',token).replace('__METER_TITLE__',unit+' · '+meter_name)
                     .replace('__READING_INPUTS__',active_inputs).replace('__PHOTO_FIELDS__',photo_fields)
                     .replace('__INSTRUCTIONS__',instructions).encode('utf-8'))
            self._answer(200,content,'text/html; charset=utf-8');return
        self._answer(404,{'error':'Risorsa non disponibile'})
    _post_slots = threading.BoundedSemaphore(2)

    def do_POST(self):
        # Cap expensive public uploads before opening a database transaction.
        if not self._post_slots.acquire(blocking=False):
            self._answer(503, {'error': 'ZEUS Reading occupato: riprova tra poco'})
            return
        try:
            # Prevent stalled public clients from holding worker slots indefinitely.
            self.connection.settimeout(20)
            self._process_POST()
        except TimeoutError:
            try:
                self._answer(408, {'error': 'Invio scaduto: riprova'})
            except OSError:
                pass
        finally:
            self._post_slots.release()

    def _process_POST(self):
        # A scheduled full restore freezes also the public QR submissions.
        from pathlib import Path
        if (Path(os.environ.get('CASA_DATA_DIR','/data'))/'zeus-restore'/'armed.json').exists():
            self._answer(503,{'error':'ZEUS in manutenzione per ripristino; riprova dopo il riavvio'});return
        route=urlsplit(self.path).path
        if not route.startswith('/submit/'):
            self._answer(404,{'error':'Risorsa non disponibile'});return
        origin=self.headers.get('Origin','')
        if origin and origin!=PUBLIC_URL:
            # Allow same-origin private-LAN testing, but not attacker-controlled hosts.
            host=urlsplit('http://'+self.headers.get('Host','')).hostname
            src=urlsplit(origin)
            try: private=ipaddress.ip_address(host).is_private
            except (ValueError,TypeError): private=False
            allowed_local=private and src.scheme=='http' and src.netloc==self.headers.get('Host','')
            if not allowed_local:
                self._answer(403,{'error':'Origine non consentita'});return
        if self.headers.get('Content-Type','').split(';')[0].strip().lower()!='application/json':
            self._answer(415,{'error':'Richiesto JSON'});return
        try:l=int(self.headers.get('Content-Length','-1'))
        except ValueError:l=-1
        if l<2 or l>BODY_MAX:
            self._answer(413,{'error':'Dimensioni richiesta non consentite'});return
        try:
            obj=json.loads(self.rfile.read(l))
            if not isinstance(obj,dict):raise ValueError('Richiesta non valida')
            with self.server.db_connection() as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    result=submit(db,route[8:],obj)
                except PermissionError:
                    # Keep failed PIN attempts even when a request is rejected.
                    db.commit()
                    raise
                db.commit()
            self._answer(200,result)
        except LookupError:self._answer(404,{'error':'QR inesistente o disattivato'})
        except PermissionError as e:self._answer(429 if 'bloccato' in str(e) else 403,{'error':str(e)})
        except (ValueError,TypeError,json.JSONDecodeError) as e:self._answer(400,{'error':str(e)})
        except Exception:
            logging.exception('Errore invio lettura QR')
            self._answer(500,{'error':'Impossibile salvare la lettura'})


def serve(db_connection):
    server=ThreadingHTTPServer(('0.0.0.0',PUBLIC_PORT),PublicHandler)
    server.db_connection=db_connection
    logging.info('ZEUS Reading public intake on TCP %s; no admin endpoints',PUBLIC_PORT)
    server.serve_forever(poll_interval=.4)
