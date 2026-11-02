
"""公司合約解約案件儲存。"""

from __future__ import annotations


def initialize_exit_store(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS
        rp_company_contract_exits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            contract_id INTEGER NOT NULL,
            company_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            requested_by TEXT NOT NULL,
            reason TEXT NOT NULL
                CHECK(reason IN (
                    'ordinary',
                    'mutual',
                    'self_company'
                )),
            company_approved INTEGER NOT NULL DEFAULT 0,
            penalty_amount INTEGER NOT NULL DEFAULT 0
                CHECK(penalty_amount >= 0),
            payment_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN (
                    'pending',
                    'completed',
                    'cancelled'
                )),
            created_at TEXT NOT NULL DEFAULT
                CURRENT_TIMESTAMP,
            completed_at TEXT
        )
    """)

    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        ix_company_contract_pending_exit
        ON rp_company_contract_exits(
            guild_id, contract_id
        )
        WHERE status='pending'
    """)

    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        ix_company_contract_completed_exit
        ON rp_company_contract_exits(
            guild_id, contract_id
        )
        WHERE status='completed'
    """)


def get_exit(db, guild_id, exit_id):
    return db.execute("""
        SELECT *
        FROM rp_company_contract_exits
        WHERE guild_id=? AND id=?
    """, (
        str(guild_id),
        int(exit_id),
    )).fetchone()


def get_open_exit(db, guild_id, contract_id):
    return db.execute("""
        SELECT *
        FROM rp_company_contract_exits
        WHERE guild_id=?
          AND contract_id=?
          AND status='pending'
    """, (
        str(guild_id),
        int(contract_id),
    )).fetchone()
