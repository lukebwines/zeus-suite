"""ZEUS Casa -> Home Assistant v1 read-only, compact, privacy-preserving API.

No tenant names, lenders, invoices, QR tokens, passwords or documents are exposed.
All financial amounts in API are expressed as euros rounded to cents.
"""
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime
import json
import advanced
import debts
import finance_balance

VERSION = '1.7.17-rc4-nav1'
METERS = {('electricity', 'main'): 'meter_electricity_main',
          ('electricity', 'sub'): 'meter_electricity_sub',
          ('electricity', 'sub2'): 'meter_electricity_sub2',
          ('gas', 'main'): 'meter_gas_main', ('gas', 'sub'): 'meter_gas_sub',
          ('water', 'main'): 'meter_water_main', ('water', 'sub'): 'meter_water_sub'}


def euros(cents):
    return round(int(cents) / 100, 2)


def summary(db, today=None):
    day = today or datetime.now(ZoneInfo('Europe/Rome')).date()
    if isinstance(day, str):
        day = date.fromisoformat(day)
    archive = advanced.get_archive(db)
    bills = archive.get('bills', [])
    adjustments = [dict(apartment=r[0], tenant_id=r[1], signed_cents=r[2], due=r[3])
                   for r in db.execute('SELECT apartment,tenant_id,signed_cents,due FROM adjustments')]
    receipts = [dict(apartment=r[0], tenant_id=r[1], received_cents=r[2])
                for r in db.execute('SELECT apartment,tenant_id,received_cents FROM receipts')]
    # Advanced.balance is the existing ZEUS calculation; balances are NOT duplicated into Finance.
    balances = advanced.balance(archive, adjustments, receipts)
    near_count = 0
    invalid_bills = 0
    for bill in bills:
        try:
            q = advanced.calc(bill)
            for i in (1, 2):
                due = bill.get(f'due{i}') or ''
                received = bill.get(f'received{i}')
                if received is None:
                    received = q[f'q{i}']/100 if bill.get(f'paid{i}') else 0
                pending = max(0, q[f'q{i}'] - advanced.cents(received))
                if pending and due and day.isoformat() <= due <= (day+timedelta(days=7)).isoformat():
                    near_count += 1
        except (ValueError, KeyError, TypeError, OverflowError):
            invalid_bills += 1
    pending_qr = db.execute("SELECT count(*) FROM qr_submissions WHERE status='pending'").fetchone()[0]
    pending_proposals = db.execute("SELECT count(*) FROM reading_pending WHERE linked_bill_id=''").fetchone()[0]
    last_reading = db.execute('SELECT MAX(date) FROM meter_readings').fetchone()[0]
    latest = {}
    for u, m, v in db.execute('SELECT utility,meter,value FROM meter_readings ORDER BY date DESC,created_at DESC,id DESC'):
        key = METERS.get((u, m))
        if key and key not in latest:
            try:
                latest[key] = float(v)
            except (TypeError, ValueError):
                continue
    loan = debts.get_all(db, at=day.isoformat())
    totals = loan['total']
    fb = finance_balance.balance(db, str(day.year))
    cash = fb['cash']; economic = fb['economic']
    vault_docs = db.execute('SELECT count(*) FROM vault_documents WHERE deleted_at IS NULL').fetchone()[0]
    vault_files = db.execute('SELECT count(*) FROM vault_files WHERE deleted_at IS NULL AND document_id IN (SELECT id FROM vault_documents WHERE deleted_at IS NULL)').fetchone()[0]
    deadlines = [r[0] for r in db.execute("SELECT due_date FROM vault_deadlines WHERE deleted_at IS NULL AND status='pending'")]
    overdue_vault = sum(d < day.isoformat() for d in deadlines)
    upcoming_vault = sum(day.isoformat() <= d <= (day + timedelta(days=30)).isoformat() for d in deadlines)
    upcoming_dates = sorted(d for d in deadlines if d >= day.isoformat())
    return {
        'api_version': 1, 'version': VERSION, 'as_of': day.isoformat(),
        'casa': {
            'bills': len(bills), 'invalid_bills': invalid_bills,
            'receivables_eur': euros(sum(x['debit'] for x in balances)),
            'overdue_eur': euros(sum(x['overdue'] for x in balances)),
            'receivables_apartment_1_eur': euros(balances[0]['debit']),
            'receivables_apartment_2_eur': euros(balances[1]['debit']),
            'bills_due_next_7_days': near_count,
            'pending_bill_documents': pending_proposals,
        },
        'reading': {'pending_qr': pending_qr, 'last_reading_date': last_reading, **latest},
        'lending': {
            'loans': len(loan['loans']),
            'principal_eur': euros(totals['principal_cents']),
            'interest_due_eur': euros(totals['interest_due_cents']),
            'total_debt_eur': euros(totals['total_debt_cents']),
        },
        'finance': {
            'cash_eur': euros(cash['current_cents']),
            'cash_forecast_eur': euros(cash['end_forecast_cents']),
            'year_income_eur': euros(economic['income_cents']),
            'year_expense_eur': euros(economic['costs_cents']),
            'deposits_eur': euros(cash['deposit_outstanding_cents']),
        },
        'vault': {
            'documents': vault_docs, 'files': vault_files,
            'upcoming_30_days': upcoming_vault, 'overdue': overdue_vault,
            'next_due_date': upcoming_dates[0] if upcoming_dates else None,
        },
    }
