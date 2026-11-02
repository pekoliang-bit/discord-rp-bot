"""讀取公司開戶資料：供銀行、公司面板共用。"""
from __future__ import annotations
from core.database import get_connection, account_balance
from modules.company_bank_schema import ensure_company_bank_schema


def get_company_bank_record(guild_id, company_id):
    with get_connection() as db:
        ensure_company_bank_schema(db)
        row = db.execute(
            "SELECT account_id,initial_deposit,opened_at FROM rp_company_bank_accounts "
            "WHERE guild_id=? AND company_id=?", (str(guild_id),str(company_id)),
        ).fetchone()
        if row is None:
            return None
        return {"account_id": int(row["account_id"]),
                "initial_deposit": int(row["initial_deposit"]),
                "opened_at": row["opened_at"],
                "balance": int(account_balance(db,guild_id,row["account_id"]))}


def get_company_bank_overview(guild_id, company_id, user_id):
    """Authorized bank plus treasury summary, sharing the existing company ledger."""
    from modules.company_treasury import company_bank_summary
    return company_bank_summary(guild_id, company_id, user_id)
