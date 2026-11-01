"""公司銀行帳戶資料表；僅增建，不刪除或覆蓋舊資料。"""
from __future__ import annotations


def ensure_company_bank_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_company_bank_accounts (
        guild_id TEXT NOT NULL,
        company_id TEXT NOT NULL,
        account_id INTEGER NOT NULL,
        initial_deposit INTEGER NOT NULL DEFAULT 0,
        opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, company_id),
        UNIQUE (guild_id, account_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rp_company_bank_opening_log (
        guild_id TEXT NOT NULL,
        application_id INTEGER NOT NULL,
        company_id TEXT NOT NULL,
        account_id INTEGER NOT NULL,
        reviewer_id TEXT,
        opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, application_id)
    )""")
