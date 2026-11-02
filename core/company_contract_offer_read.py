
"""公司合約查詢與提出前資格檢查。"""

from __future__ import annotations

from core.database import get_connection
from core.company_contract_store import initialize_contracts


def contract_list(guild_id, actor_id, limit=20):
    gid, uid = str(guild_id), str(actor_id)

    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("查詢筆數必須介於1至20")

    with get_connection() as db:
        initialize_contracts(db)

        rows = db.execute("""
            SELECT *
            FROM rp_company_employee_contracts
            WHERE guild_id=?
              AND (
                employee_user_id=?
                OR employer_user_id=?
              )
              AND status IN ('pending','active')
            ORDER BY id DESC
            LIMIT ?
        """, (gid, uid, uid, limit)).fetchall()

        return [dict(row) for row in rows]


def validate_contract_offer_target(
    guild_id,
    company_id,
    character_id,
    actor_id,
):
    from modules.company_treasury import _is_owner

    gid = str(guild_id)
    bid = str(company_id)
    uid = str(actor_id)
    cid = int(character_id)

    if cid <= 0:
        raise ValueError("角色編號不正確")

    with get_connection() as db:
        if not _is_owner(db, gid, bid, uid):
            raise PermissionError(
                "只有公司持有人可以提出合約"
            )

        company = db.execute("""
            SELECT kind, status
            FROM rp_business_entities
            WHERE guild_id=? AND business_id=?
        """, (gid, bid)).fetchone()

        if (
            company is None
            or company["kind"] != "company"
            or company["status"] != "active"
        ):
            raise ValueError(
                "必須選擇已啟用的正式公司"
            )

        employee = db.execute("""
            SELECT c.owner_id
            FROM company_employees AS e
            JOIN characters AS c
              ON c.guild_id=e.guild_id
             AND c.id=e.character_id
            WHERE e.guild_id=?
              AND e.company_id=?
              AND e.character_id=?
              AND e.status='active'
              AND c.status='active'
        """, (gid, bid, cid)).fetchone()

        if employee is None:
            raise ValueError(
                "指定角色目前不是正式在職員工"
            )

        return str(employee["owner_id"])
