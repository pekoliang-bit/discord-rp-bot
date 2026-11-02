
"""歷史欠薪管理：人工核定、債務紀錄、公司銀行補發及私人通知。"""

from __future__ import annotations

import logging
import time

from core.database import (
    transaction,
    get_connection,
    get_or_create_account,
)
from core.economy import _payment


LOG = logging.getLogger(__name__)


def _setup(db):
    """建立歷史欠薪資料表，不覆蓋現有資料。"""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS rp_company_wage_debt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            business_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            period_label TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount > 0),
            salary_snapshot INTEGER NOT NULL CHECK(salary_snapshot >= 0),
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'paid')),
            approved_by TEXT NOT NULL,
            approved_at INTEGER NOT NULL,
            paid_by TEXT,
            paid_at INTEGER,
            reference_key TEXT,
            UNIQUE(
                guild_id,
                business_id,
                character_id,
                period_label
            )
        )
        """
    )


def _notify_wage_event(
    guild_id,
    character_id,
    debt_id,
    event_type,
    amount,
):
    """通知失敗不影響已完成的財務操作。"""
    try:
        from core.company_event_notifications import private_wage_event

        private_wage_event(
            guild_id,
            character_id,
            debt_id,
            event_type,
            amount,
        )
    except Exception:
        LOG.exception(
            "歷史欠薪私人通知失敗：guild=%s character=%s debt=%s event=%s",
            guild_id,
            character_id,
            debt_id,
            event_type,
        )


def record_debt(
    guild_id,
    business_id,
    actor_id,
    character_id,
    period_label,
    amount,
    reason,
):
    """由公司持有人核定並登記歷史欠薪。"""

    from modules.company_treasury import (
        _is_owner,
        initialize_treasury,
    )

    initialize_treasury()

    gid = str(guild_id)
    bid = str(business_id)
    uid = str(actor_id)

    cid = int(character_id)
    amount = int(amount)
    label = str(period_label).strip()
    reason = str(reason).strip()

    if (
        cid <= 0
        or amount <= 0
        or amount > 10**12
        or not 1 <= len(label) <= 80
        or not 5 <= len(reason) <= 300
    ):
        raise ValueError(
            "角色、金額、薪資期或核定依據不正確"
        )

    debt_id = None

    with transaction() as db:
        _setup(db)

        if not _is_owner(db, gid, bid, uid):
            raise PermissionError(
                "僅公司持有人可以核定歷史欠薪"
            )

        character = db.execute(
            """
            SELECT 1
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (gid, cid),
        ).fetchone()

        if not character:
            raise ValueError("角色不存在")

        prior = db.execute(
            """
            SELECT id
            FROM rp_company_wage_debt
            WHERE guild_id = ?
              AND business_id = ?
              AND character_id = ?
              AND period_label = ?
            """,
            (gid, bid, cid, label),
        ).fetchone()

        if prior:
            raise ValueError(
                "這個角色的該薪資期已核定，禁止重複建立"
            )

        cursor = db.execute(
            """
            INSERT INTO rp_company_wage_debt (
                guild_id,
                business_id,
                character_id,
                period_label,
                amount,
                salary_snapshot,
                reason,
                approved_by,
                approved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gid,
                bid,
                cid,
                label,
                amount,
                amount,
                reason,
                uid,
                int(time.time()),
            ),
        )

        debt_id = cursor.lastrowid

    if debt_id is not None:
        _notify_wage_event(
            gid,
            cid,
            debt_id,
            "registered",
            amount,
        )

    return (
        "✅ 已登記歷史欠薪（尚未付款）。"
        "離職角色亦保留此紀錄。"
    )


def list_debts(
    guild_id,
    business_id,
    actor_id,
):
    """查詢公司的歷史欠薪紀錄。"""

    from modules.company_treasury import (
        _finance_permission,
        initialize_treasury,
    )

    initialize_treasury()

    gid = str(guild_id)
    bid = str(business_id)
    uid = str(actor_id)

    with get_connection() as db:
        _setup(db)

        if not _finance_permission(db, gid, bid, uid):
            raise PermissionError(
                "沒有查看公司財務權限"
            )

        rows = db.execute(
            """
            SELECT
                id,
                character_id,
                period_label,
                amount,
                status,
                reason,
                paid_at
            FROM rp_company_wage_debt
            WHERE guild_id = ?
              AND business_id = ?
            ORDER BY id DESC
            LIMIT 30
            """,
            (gid, bid),
        ).fetchall()

        return [dict(row) for row in rows]


def settle_debt(
    guild_id,
    business_id,
    actor_id,
    debt_id,
):
    """從公司銀行帳戶補發指定歷史欠薪。"""

    from modules.company_treasury import (
        _is_owner,
        initialize_treasury,
    )

    initialize_treasury()

    gid = str(guild_id)
    bid = str(business_id)
    uid = str(actor_id)

    debt_id = int(debt_id)

    with transaction() as db:
        _setup(db)

        if not _is_owner(db, gid, bid, uid):
            raise PermissionError(
                "只允許公司持有人清償歷史欠薪"
            )

        row = db.execute(
            """
            SELECT *
            FROM rp_company_wage_debt
            WHERE id = ?
              AND guild_id = ?
              AND business_id = ?
            """,
            (debt_id, gid, bid),
        ).fetchone()

        if not row:
            raise ValueError("欠薪紀錄不存在")

        if row["status"] == "paid":
            return "ℹ️ 本筆欠薪已清償"

        bank = db.execute(
            """
            SELECT
                account_id,
                status
            FROM rp_company_bank_accounts
            WHERE guild_id = ?
              AND company_id = ?
            """,
            (gid, bid),
        ).fetchone()

        if not bank or bank["status"] != "active":
            raise ValueError(
                "公司銀行帳戶未開通或已凍結"
            )

        ref = (
            f"company-wage-debt:"
            f"{gid}:{bid}:{row['id']}"
        )

        wallet = get_or_create_account(
            db,
            gid,
            "character",
            str(row["character_id"]),
            "wallet",
        )

        result = _payment(
            db,
            gid,
            ref,
            int(bank["account_id"]),
            wallet,
            int(row["amount"]),
            (
                "公司歷史欠薪補發："
                f"{row['period_label']}"
            ),
        )

        if result.duplicated:
            raise RuntimeError(
                "帳本存在付款，但欠薪仍未標記，"
                "請人工核對"
            )

        db.execute(
            """
            UPDATE rp_company_wage_debt
            SET
                status = ?,
                paid_by = ?,
                paid_at = ?,
                reference_key = ?
            WHERE id = ?
            """,
            (
                "paid",
                uid,
                int(time.time()),
                ref,
                row["id"],
            ),
        )

        paid_character_id = row["character_id"]
        paid_amount = int(row["amount"])
        paid_debt_id = row["id"]

    _notify_wage_event(
        gid,
        paid_character_id,
        paid_debt_id,
        "paid",
        paid_amount,
    )

    return (
        f"✅ 已補發 {paid_amount:,} 金幣，"
        f"角色 #{paid_character_id}；"
        "交易已記帳。"
    )
