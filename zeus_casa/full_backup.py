"""ZEUS complete data backup, verified staging and offline recovery.

Never replace a live SQLite database. Upload/validation is online; switching data
occurs only during the next add-on start, before any servers/threads are created.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
from contextlib import closing
import tempfile
import threading
import zipfile

MAX_UPLOAD = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_FILES = 10000
VERSION = 3
DB_NAME = 'casa_consumi.sqlite3'
STAGE = 'zeus-restore'
ALLOWED_ROOT = {'casa_consumi.sqlite3','archivio.json','manifest.json','LEGGIMI.txt'}
FILE_NAME = re.compile(r'[0-9a-f]{32}\.bin\Z')


def digest(stream):
    h=hashlib.sha256()
    while True:
        part=stream.read(1024*1024)
        if not part: break
        h.update(part)
    return h.hexdigest()


def _atomic_json(path,obj):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(obj,f,ensure_ascii=False)
        f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def _json(path):
    try:return json.loads(path.read_text('utf-8'))
    except (FileNotFoundError,ValueError):return None


def validate(zip_path,working_dir,unpack=False):
    """Reject corrupted, foreign, incomplete, malformed or oversized archives."""
    destination=Path(working_dir)
    try:
        with zipfile.ZipFile(zip_path,'r') as z:
            entries=z.infolist()
            if not 3<=len(entries)<=MAX_FILES:raise ValueError('Numero di file non valido nel backup')
            names=[item.filename for item in entries]
            if len(set(names))!=len(names):raise ValueError('Archivio con nomi duplicati')
            if not {'casa_consumi.sqlite3','manifest.json'}.issubset(names):raise ValueError('Database o manifesto mancanti')
            expanded=0
            for info in entries:
                name=info.filename
                if (name not in ALLOWED_ROOT and not (name.startswith('vault/files/') and FILE_NAME.fullmatch(name[12:]))) or info.is_dir():
                    raise ValueError('Archivio con un percorso o formato non consentito')
                if info.flag_bits & 1 or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Archivio cifrato o con link non consentiti')
                expanded += info.file_size
                if info.file_size > MAX_EXPANDED or expanded > MAX_EXPANDED:
                    raise ValueError('Archivio espanso troppo grande')
            manifest=json.loads(z.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('app')!='ZEUS Casa' or manifest.get('backup_version') not in (2,3):
                raise ValueError('Non è un backup completo ZEUS compatibile')
            if set(manifest.get('files',[])) != set(names):
                raise ValueError('Elenco file del backup non coerente')
            hashes=manifest.get('sha256',{})
            if manifest.get('backup_version')==3 and (not isinstance(hashes,dict) or set(hashes)!=(set(names)-{'manifest.json'})):
                raise ValueError('Impronte del backup mancanti o incomplete')
            # Refuse the archive before expansion if free space is insufficient.
            # A malicious archive must not fill the host filesystem.
            if shutil.disk_usage(destination.parent).free < expanded + 32*1024*1024:
                raise ValueError('Spazio insufficiente per controllare il backup ZIP')
            destination.mkdir(mode=0o700,parents=True,exist_ok=True)
            for info in entries:
                target=destination/info.filename
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                h=hashlib.sha256()
                with z.open(info,'r') as src,target.open('wb') as dst:
                    while True:
                        chunk=src.read(1024*1024)
                        if not chunk:break
                        h.update(chunk);dst.write(chunk)
                if hashes and info.filename in hashes and h.hexdigest()!=hashes[info.filename]:
                    raise ValueError('Impronta non corrispondente per '+info.filename)
            db_file=destination/DB_NAME
            # Query-only and untrusted schema prevent executing triggers while inspecting.
            conn=sqlite3.connect(f'file:{db_file}?mode=ro',uri=True)
            try:
                conn.execute('PRAGMA trusted_schema=OFF')
                conn.execute('PRAGMA query_only=ON')
                if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('Il database SQLite non supera il controllo di integrità')
                tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'state','vault_files','vault_documents','vault_folders'}.issubset(tables):
                    raise ValueError('Il database non contiene tutte le tabelle ZEUS essenziali')
                row=conn.execute('SELECT archive FROM state WHERE id=1').fetchone()
                if not row or not isinstance(json.loads(row[0]),dict):
                    raise ValueError('Dati principali ZEUS mancanti')
                all_vault=conn.execute('SELECT disk_name,size,sha256 FROM vault_files').fetchall()
                listed=set()
                for filename,size,sha in all_vault:
                    if not isinstance(filename,str) or not FILE_NAME.fullmatch(filename):
                        raise ValueError('Nome allegato non valido nel database')
                    listed.add('vault/files/'+filename)
                    path=destination/'vault'/'files'/filename
                    if not path.is_file() or path.stat().st_size!=size:
                        raise ValueError('Allegato mancante o dimensione non coerente: '+filename)
                    with path.open('rb') as f:
                        if digest(f)!=sha:raise ValueError('Allegato danneggiato: '+filename)
                if listed != {n for n in names if n.startswith('vault/files/')}:
                    raise ValueError('Allegati dello ZIP diversi da quelli del database')
                summary={
                    'version':manifest['backup_version'],
                    'created_at_utc':manifest.get('created_at_utc',''),
                    'attachments':len(all_vault),
                    'size_bytes':Path(zip_path).stat().st_size,
                    'sqlite_bytes':db_file.stat().st_size,
                    'files':len(names),
                }
            finally:conn.close()
        return summary
    finally:
        if not unpack:shutil.rmtree(destination,ignore_errors=True)


# Only one restore upload may manipulate upload.part, incoming.zip and checked.json.
_STAGE_LOCK=threading.Lock()

def stage_stream(data_dir,source,length):
    if not _STAGE_LOCK.acquire(blocking=False):
        raise ValueError('Un altro caricamento di backup è in corso')
    try:
        return _stage_stream_locked(data_dir,source,length)
    finally:
        _STAGE_LOCK.release()


def _stage_stream_locked(data_dir,source,length):
    data_dir=Path(data_dir)
    if not 0<length<=MAX_UPLOAD:raise ValueError('Il backup supera il limite di 512 MB')
    folder=data_dir/STAGE
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    if (folder/'armed.json').exists() or (folder/'in_progress.json').exists():
        raise ValueError('Un ripristino è già programmato; riavvia ZEUS prima di caricarne un altro')
    tmp=folder/'upload.part'
    (folder/'checked.json').unlink(missing_ok=True)
    (folder/'incoming.zip').unlink(missing_ok=True)
    try:
        with tmp.open('wb') as target:
            remain=length
            while remain:
                part=source.read(min(remain,1024*1024))
                if not part:raise ValueError('Caricamento interrotto prima della fine')
                target.write(part);remain-=len(part)
            target.flush();os.fsync(target.fileno())
        with tempfile.TemporaryDirectory(dir=folder,prefix='check-') as scratch:
            summary=validate(tmp,scratch)
        os.replace(tmp,folder/'incoming.zip')
        sha=digest((folder/'incoming.zip').open('rb'))
        _atomic_json(folder/'checked.json',{**summary,'sha256':sha})
        return summary
    finally:tmp.unlink(missing_ok=True)


def arm_restore(data_dir,confirm):
    if confirm!='RIPRISTINA ZEUS':raise ValueError('Inserisci la conferma RIPRISTINA ZEUS')
    folder=Path(data_dir)/STAGE
    info=_json(folder/'checked.json')
    archive=folder/'incoming.zip'
    if not info or not archive.is_file():raise ValueError('Nessun backup ZIP verificato e pronto')
    with archive.open('rb') as f:
        if digest(f) != info['sha256']:
            raise ValueError('Il file caricato è stato modificato')
    _atomic_json(folder/'armed.json',{'sha256':info['sha256'],'created_at_utc':datetime.now(timezone.utc).isoformat()})
    return {'scheduled':True,'message':'Ripristino programmato. Riavvia manualmente l’add-on ZEUS Casa da Home Assistant. Non usare l’app fino al termine del riavvio.'}


def status(data_dir):
    folder=Path(data_dir)/STAGE
    return {'checked':_json(folder/'checked.json'), 'armed':(folder/'armed.json').exists(), 'last_result':_json(folder/'last_result.json')}


def _rollback(data_dir,backup):
    db=Path(data_dir)/DB_NAME
    shutil.copy2(backup/DB_NAME,db)
    for suffix in ('-wal','-shm'):(Path(str(db)+suffix)).unlink(missing_ok=True)
    root=Path(data_dir)/'vault'/'files'
    if root.exists():shutil.rmtree(root)
    original=backup/'vault'/'files'
    if original.exists():shutil.copytree(original,root)
    else:root.mkdir(mode=0o700,parents=True,exist_ok=True)


def apply_at_startup(data_dir,initialize):
    """Called BEFORE threads, web service and readers. Safely recover crashes."""
    data_dir=Path(data_dir)
    folder=data_dir/STAGE
    marker=folder/'in_progress.json'
    if marker.exists():
        previous=_json(marker) or {}
        recovery=folder/'recovery'/previous.get('recovery','')
        if recovery.is_dir() and (recovery/DB_NAME).is_file():
            logging.error('Ripristino interrotto: recupero dei dati precedenti da %s',recovery)
            _rollback(data_dir,recovery)
            _atomic_json(folder/'last_result.json',{'ok':False,'message':'Ripristino interrotto: ripristinati i dati precedenti'})
            marker.unlink(missing_ok=True);(folder/'armed.json').unlink(missing_ok=True)
        else:raise RuntimeError('Ripristino interrotto e copia di emergenza assente: avvio bloccato')
    armed=_json(folder/'armed.json')
    if not armed:return
    archive=folder/'incoming.zip'
    if archive.is_file():
        with archive.open('rb') as f: verified_sha = digest(f) == armed.get('sha256')
    else: verified_sha = False
    if not verified_sha:
        (folder/'armed.json').unlink(missing_ok=True)
        _atomic_json(folder/'last_result.json',{'ok':False,'message':'Backup assente o modificato; dati correnti invariati'})
        return
    try:
        with tempfile.TemporaryDirectory(dir=folder,prefix='restore-') as scratch:
            summary=validate(archive,scratch,unpack=True)
            old=data_dir/DB_NAME
            # An incomplete safety copy must never consume all available disk space.
            old_vault=data_dir/'vault'/'files'
            original_bytes=sum(f.stat().st_size for f in old_vault.iterdir() if f.is_file()) if old_vault.is_dir() else 0
            proposed_bytes=summary['sqlite_bytes']+sum(f.stat().st_size for f in (Path(scratch)/'vault'/'files').iterdir() if f.is_file()) if (Path(scratch)/'vault'/'files').is_dir() else summary['sqlite_bytes']
            required=old.stat().st_size+original_bytes+proposed_bytes+(32*1024*1024)
            if shutil.disk_usage(data_dir).free < required:
                raise OSError('Spazio insufficiente per copia di sicurezza e ripristino; operazione annullata')
            stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_UTC')
            recovery=folder/'recovery'/stamp
            recovery.mkdir(mode=0o700,parents=True)
            with closing(sqlite3.connect(old)) as src, closing(sqlite3.connect(recovery/DB_NAME)) as dest:
                src.backup(dest)
            original=data_dir/'vault'/'files'
            if original.exists():shutil.copytree(original,recovery/'vault'/'files')
            _atomic_json(marker,{'recovery':stamp})
            try:
                # Only replace database at startup, never while connections are open.
                shutil.copy2(Path(scratch)/DB_NAME,folder/'next.sqlite3')
                os.replace(folder/'next.sqlite3',old)
                for suffix in ('-wal','-shm'):(Path(str(old)+suffix)).unlink(missing_ok=True)
                new_root=Path(scratch)/'vault'/'files'
                target=data_dir/'vault'/'files'
                if target.exists():shutil.rmtree(target)
                target.mkdir(mode=0o700,parents=True,exist_ok=True)
                for file in new_root.glob('*') if new_root.exists() else []:
                    shutil.copy2(file,target/file.name)
                initialize()
                _atomic_json(folder/'last_result.json',{'ok':True,'message':'Ripristino completo riuscito','summary':summary,'recovery':str(recovery)})
                marker.unlink(missing_ok=True)
                logging.warning('ZEUS ripristinato da backup completo; copia precedente in %s',recovery)
            except Exception:
                logging.exception('Ripristino fallito, recupero dati correnti precedenti')
                _rollback(data_dir,recovery)
                initialize()
                _atomic_json(folder/'last_result.json',{'ok':False,'message':'Ripristino non riuscito; ripristinati i dati precedenti'})
                marker.unlink(missing_ok=True)
    except Exception as exc:
        logging.exception('Ripristino non completato')
        # If the in-progress marker remains, NEVER start services over uncertain data.
        # Next start can use its safety snapshot to roll back.
        if marker.exists():
            raise RuntimeError('Ripristino interrotto: necessario riavvio per recupero automatico') from exc
        _atomic_json(folder/'last_result.json',{'ok':False,'message':'Ripristino annullato: '+str(exc)[:130]})
    finally:
        if not marker.exists():
            (folder/'armed.json').unlink(missing_ok=True)
            (folder/'checked.json').unlink(missing_ok=True)
            archive.unlink(missing_ok=True)
