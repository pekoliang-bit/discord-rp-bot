
"""公司合約查詢與拒絕服務。"""

from __future__ import annotations

from core.database import get_connection, transaction
from core.company_contract_store import initialize_contracts


def list_employee_contracts(
    guild_id,
    actor_id,
    *,
    limit=20,
):
    gid = str(guild_id)
    uid = str(actor_id)

    if not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("查詢筆數須介於1至20")

    with get_connection() as db:
        initialize_contracts(db)

        rows = db.execute(
            """
            SELECT *
            FROM rp_company_employee_contracts
            WHERE guild_id=?
              AND employee_user_id=?
              AND status IN ('pending','active')
            ORDER BY id DESC
            LIMIT ?
            """,
            (gid, uid, limit),
        ).fetchall()

        return [dict(row) for row in rows]


def reject_contract(
    guild_id,
    contract_id,
    actor_id,
):
    gid = str(guild_id)
    uid = str(actor_id)
    with transaction() as db:
        initialize_contracts(db)
        from core.company_public_ids import resolve_contract
        cid = resolve_contract(db, gid, contract_id)
        if cid <= 0:
            raise ValueError("合約編號不正確")

        row = db.execute(
            """
            SELECT *
            FROM rp_company_employee_contracts
            WHERE guild_id=? AND id=?
            """,
            (gid, cid),
        ).fetchone()

        if row is None:
            raise LookupError("找不到這份合約")

        if str(row["employee_user_id"]) != uid:
            raise PermissionError(
                "只有合約指定的員工能拒絕"
            )

        if row["status"] != "pending":
            raise ValueError(
                "只有尚未生效的合約能拒絕"
            )

        result = db.execute(
            """
            UPDATE rp_company_employee_contracts
            SET status='cancelled',
                termination_reason='employee_rejected',
                terminated_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND id=?
              AND status='pending'
            """,
            (gid, cid),
        )

        if result.rowcount != 1:
            raise RuntimeError("合約狀態已變更")

    return "✅ 已拒絕合約，沒有產生違約金。"
