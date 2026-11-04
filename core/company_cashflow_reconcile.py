
"""Auditable organization cash movements. Ledger remains the sole source of balances.

No bookkeeping classification is inferred from text descriptions. Internal transfers
between organization-owned bank, treasury, and dividend-reserve are excluded.
"""
from __future__ import annotations

from core.database import get_connection


def _company_account_ids(db, guild_id, business_id):
    gid, bid = str(guild_id), str(business_id)
    account_ids = set()
    rows = db.execute("SELECT id FROM accounts WHERE guild_id=? AND owner_type='organization' AND owner_id=?", (gid, bid)).fetchall()
    account_ids.update(int(r['id']) for r in rows)
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_company_bank_accounts'").fetchone():
        rows = db.execute("SELECT account_id FROM rp_company_bank_accounts WHERE guild_id=? AND company_id=?", (gid, bid)).fetchall()
        account_ids.update(int(r['account_id']) for r in rows)
    return account_ids


def ledger_movements(db, guild_id, business_id):
    gid = str(guild_id)
    ids = _company_account_ids(db, gid, business_id)
    if not ids:
        return []
    marks = ','.join('?' for _ in ids)
    rows = db.execute(f"""
        SELECT t.id AS transaction_id,t.unique_key AS key,t.created_at,
               SUM(e.amount) AS delta
        FROM ledger_transactions t JOIN ledger_entries e
          ON e.guild_id=t.guild_id AND e.transaction_id=t.id
        WHERE t.guild_id=? AND e.account_id IN ({marks})
        GROUP BY t.id,t.unique_key,t.created_at
        ORDER BY t.id DESC
    """, (gid, *sorted(ids))).fetchall()
    return [dict(r) for r in rows]


def cashflow_summary(guild_id, business_id, actor_id):
    from modules.company_treasury import initialize_treasury, _finance_permission
    initialize_treasury()
    gid, bid, uid = map(str, (guild_id, business_id, actor_id))
    with get_connection() as db:
        if not _finance_permission(db, gid, bid, uid):
            raise PermissionError('沒有公司財務查閱權限')
        rows = ledger_movements(db, gid, bid)
    nonzero = [r for r in rows if int(r['delta'])]
    return {
        'inflow': sum(int(r['delta']) for r in nonzero if int(r['delta'])>0),
        'outflow': -sum(int(r['delta']) for r in nonzero if int(r['delta'])<0),
        'net': sum(int(r['delta']) for r in nonzero),
        'transfers': len(rows)-len(nonzero),
        'unclassified': nonzero,
    }
