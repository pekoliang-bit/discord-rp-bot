
"""公司銀行帳戶正式開戶服務。

僅供已授權的 /申請 審核交易內呼叫。
本模組不自行通過申請、不修改玩家餘額。
"""

from __future__ import annotations

from core.database import get_or_create_account


def initialize_company_bank(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS rp_company_bank_accounts (
            guild_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            account_id INTEGER NOT NULL,
            opened_by TEXT NOT NULL,
            application_id INTEGER NOT NULL,
            opened_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'frozen')),
            PRIMARY KEY (guild_id, company_id),
            UNIQUE (guild_id, account_id),
            UNIQUE (guild_id, application_id)
        )
    """)


def approve_company_bank(
    db,
    guild_id,
    company_id,
    applicant_id,
    application_id,
):
    """與申請審核共用同一個 SQLite 交易。"""

    guild_id = str(guild_id)
    company_id = str(company_id).strip()
    applicant_id = str(applicant_id)

    if not guild_id or not company_id or not applicant_id:
        raise ValueError("公司開戶資料不完整。")

    application_id = int(application_id)
    if application_id <= 0:
        raise ValueError("申請編號必須是正整數。")

    initialize_company_bank(db)

    from modules.application_extras import (
        validate_company_bank_applicant,
    )

    validate_company_bank_applicant(
        db,
        guild_id,
        company_id,
        applicant_id,
    )

    company = db.execute("""
        SELECT business_id, owner_id, status
        FROM rp_business_entities
        WHERE guild_id=?
          AND business_id=?
          AND kind='company'
    """, (guild_id, company_id)).fetchone()

    if company is None or company["status"] != "active":
        raise ValueError("公司不存在或尚未正式啟用。")

    old = db.execute("""
        SELECT account_id, application_id
        FROM rp_company_bank_accounts
        WHERE guild_id=? AND company_id=?
    """, (guild_id, company_id)).fetchone()

    if old is not None:
        if int(old["application_id"]) == application_id:
            return int(old["account_id"])

        raise ValueError("該公司已經開立銀行帳戶。")

    account_id = get_or_create_account(
        db,
        guild_id,
        "organization",
        company_id,
        "bank",
    )

    db.execute("""
        INSERT INTO rp_company_bank_accounts (
            guild_id,
            company_id,
            account_id,
            opened_by,
            application_id
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        guild_id,
        company_id,
        account_id,
        applicant_id,
        application_id,
    ))

    return int(account_id)
