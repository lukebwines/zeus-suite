"""Removal of archived tenant profile from working directory, with historical pointers.

No invoices, payments, accounting or physical readings are deleted by this action.
An audit tombstone only retains name if needed for already-recorded financial movements.
"""
import datetime as dt
import json


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS tenant_archive_deletions (
       tenant_id TEXT PRIMARY KEY, apartment INTEGER NOT NULL,
       deleted_at TEXT NOT NULL, audit_label TEXT NOT NULL DEFAULT '',
       referenced INTEGER NOT NULL DEFAULT 0)""")


def sync(db, old, new):
    old_by={str(t.get('id')):t for t in old.get('tenants',[])}
    new_ids={str(t.get('id')) for t in new.get('tenants',[])}
    for tid,t in old_by.items():
        if tid in new_ids:continue
        if t.get('active',True):
            raise PermissionError('Archivia l’inquilino prima di eliminare la scheda.')
        if any(tid==b.get(key) for b in old.get('bills',[]) for key in ('tenant1Id','tenant2Id')):
            linked=True
        else:linked=False
        for table in ('adjustments','receipts'):
            if db.execute(f'SELECT 1 FROM {table} WHERE tenant_id=? LIMIT 1',(tid,)).fetchone():linked=True
        if db.execute('SELECT 1 FROM tenant_handover_baselines WHERE tenant_id=?',(tid,)).fetchone():linked=True
        # Some reports need the name; keep only a minimal historical label when linked.
        label=str(t.get('name',''))[:120] if linked else ''
        db.execute("""INSERT INTO tenant_archive_deletions
           (tenant_id,apartment,deleted_at,audit_label,referenced) VALUES(?,?,?,?,?)""",
           (tid,int(t['apartment']),dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),label,int(linked)))
        db.execute('INSERT INTO events(at,action,context) VALUES(?,?,?)',
           (dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
            'inquilino_archiviato_rimosso',f'{tid[:90]} · riferimenti storici: {linked}'))
    # Prevent a removed identifier from being re-used with an unrelated tenant.
    for t in new.get('tenants',[]):
        tid=str(t.get('id'))
        if tid not in old_by and db.execute('SELECT 1 FROM tenant_archive_deletions WHERE tenant_id=?',(tid,)).fetchone():
            raise PermissionError('Identificativo di una scheda rimossa: crea una nuova anagrafica.')
