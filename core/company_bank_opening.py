
"""公司銀行帳戶正式開戶服務。

使用既有 accounts / ledger_entries。
開戶不產生金幣。
必須在申請審核的 SQLite 交易內執行。
"""

from __future__ import annotations

from core.database import get_or_create_account


def initialize_company_bank(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS
        rp_company_bank_accounts (
            guild_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            account_id INTEGER NOT NULL,
            opened_by TEXT NOT NULL,
            application_id INTEGER NOT NULL,
            initial_deposit INTEGER NOT NULL DEFAULT 0
                CHECK(initial_deposit >= 0),
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
    gid = str(guild_id).strip()
    bid = str(company_id).strip()
    uid = str(applicant_id).strip()

    if not all((gid, bid, uid)):
        raise ValueError("公司開戶資料不完整。")

    if isinstance(application_id, bool):
        raise ValueError("申請編號錯誤。")

    app_id = int(application_id)

    if app_id <= 0:
        raise ValueError("申請編號必須大於零。")

    initialize_company_bank(db)

    from modules.application_extras import (
        validate_company_bank_applicant,
    )

    validate_company_bank_applicant(
        db, gid, bid, uid
    )

    # 真正的公司資料亦必須存在並已啟用。
    company = db.execute("""
        SELECT business_id
        FROM rp_business_entities
        WHERE guild_id=?
          AND business_id=?
          AND kind='company'
          AND status='active'
    """, (gid, bid)).fetchone()

    if company is None:
        raise ValueError(
            "找不到正式啟用的公司。"
        )

    existing = db.execute("""
        SELECT account_id, application_id
        FROM rp_company_bank_accounts
        WHERE guild_id=? AND company_id=?
    """, (gid, bid)).fetchone()

    if existing is not None:
        if int(existing["application_id"]) == app_id:
            return int(existing["account_id"])

        raise ValueError(
            "此公司已開立銀行帳戶，"
            "不可重複申請。"
        )

    account_id = get_or_create_account(
        db,
        gid,
        "organization",
        bid,
        "bank",
    )

    # 既有帳戶可能已被其他流程建立，
    # 不得因此創造初始金幣。
    db.execute("""
        INSERT INTO rp_company_bank_accounts (
            guild_id,
            company_id,
            account_id,
            opened_by,
            application_id,
            initial_deposit
        )
        VALUES (?, ?, ?, ?, ?, 0)
    """, (gid, bid, account_id, uid, app_id))

    return int(account_id)
