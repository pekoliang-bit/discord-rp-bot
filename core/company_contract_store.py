
"""正式員工合約儲存：與既有薪資、解約流程共用資料表。"""
from __future__ import annotations


def initialize_contracts(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS rp_company_employee_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            employee_user_id TEXT NOT NULL,
            employer_user_id TEXT NOT NULL,
            salary INTEGER NOT NULL CHECK(salary >= 0),
            cycle_days INTEGER NOT NULL CHECK(cycle_days IN (7,14)),
            penalty INTEGER NOT NULL DEFAULT 0 CHECK(penalty >= 0),
            employee_accepted INTEGER NOT NULL DEFAULT 0,
            employer_accepted INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','active','cancelled','terminated')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            activated_at TEXT,
            terminated_at TEXT,
            termination_reason TEXT
        )
    """)
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_company_employee_open_contract
        ON rp_company_employee_contracts(guild_id, company_id, character_id)
        WHERE status IN ('pending','active')
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS ix_company_employee_contract_actor
        ON rp_company_employee_contracts(guild_id, employee_user_id, status)
    """)


def active_contract(db, guild_id, company_id, character_id):
    initialize_contracts(db)
    return db.execute("""
        SELECT * FROM rp_company_employee_contracts
        WHERE guild_id=? AND company_id=? AND character_id=?
          AND status='active'
        ORDER BY id DESC LIMIT 1
    """, (str(guild_id), str(company_id), int(character_id))).fetchone()
