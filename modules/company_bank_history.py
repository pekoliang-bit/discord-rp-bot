"""Company bank entries are read from the shared ledger, not a parallel balance."""
from __future__ import annotations
from core.database import get_connection
from modules.company_treasury import _finance_permission, initialize_treasury


def company_bank_history(guild_id,business_id,user_id,limit=10):
    initialize_treasury()
    with get_connection() as db:
        if not _finance_permission(db,str(guild_id),str(business_id),str(user_id)):
            raise PermissionError('沒有公司銀行帳務查看權限。')
        record=db.execute('SELECT account_id FROM rp_company_bank_accounts WHERE guild_id=? AND company_id=?',
                          (str(guild_id),str(business_id))).fetchone()
        if record is None:
            raise ValueError('此公司尚未開通銀行帳戶，請至 /申請 辦理。')
        rows=db.execute("""SELECT t.id,t.description,t.created_at,e.amount
            FROM ledger_entries e JOIN ledger_transactions t ON t.id=e.transaction_id AND t.guild_id=e.guild_id
            WHERE e.guild_id=? AND e.account_id=? ORDER BY t.id DESC LIMIT ?""",
            (str(guild_id),int(record['account_id']), max(1,min(int(limit),20)))).fetchall()
    return [dict(r) for r in rows]


def format_company_bank_history(guild_id,business_id,user_id):
    rows=company_bank_history(guild_id,business_id,user_id)
    lines=[f'📜 **公司銀行交易紀錄｜{business_id}**']
    if not rows:
        lines.append('尚無交易紀錄。')
    for r in rows:
        value=int(r['amount'])
        lines.append(f"#{r['id']}｜{value:+,} 金幣｜{str(r['description'])[:65]}")
    return '\n'.join(lines)[:1850]
