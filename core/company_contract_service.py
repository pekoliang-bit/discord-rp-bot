"""公司員工合約服務。"""

from __future__ import annotations

from core.database import transaction
from core.company_contract_rules import (
    validate_contract,
)
from core.company_contract_store import (
    initialize_contracts,
    active_contract,
)


def offer_contract(
    guild_id,
    company_id,
    character_id,
    employer_id,
    salary,
    cycle_days,
    penalty,
):
    from modules.company_treasury import _is_owner

    validate_contract(
        salary, cycle_days, penalty
    )

    gid = str(guild_id)
    bid = str(company_id)
    uid = str(employer_id)
    cid = int(character_id)

    with transaction() as db:
        initialize_contracts(db)

        if not _is_owner(db, gid, bid, uid):
            raise PermissionError(
                "只有公司持有人可以提出正式合約"
            )

        employee = db.execute("""
            SELECT c.owner_id
            FROM company_employees e
            JOIN characters c
              ON c.guild_id=e.guild_id
             AND c.id=e.character_id
            WHERE e.guild_id=?
              AND e.company_id=?
              AND e.character_id=?
              AND e.status='active'
        """, (gid, bid, cid)).fetchone()

        if employee is None:
            raise ValueError(
                "角色目前不是這家公司的正式員工"
            )

        old = db.execute("""
            SELECT id
            FROM rp_company_employee_contracts
            WHERE guild_id=?
              AND company_id=?
              AND character_id=?
              AND status IN ('pending','active')
        """, (gid, bid, cid)).fetchone()

        if old:
            raise ValueError(
                "此員工已有待確認或生效中的合約"
            )

        cursor = db.execute("""
            INSERT INTO rp_company_employee_contracts
            (
                guild_id,company_id,character_id,
                employee_user_id,employer_user_id,
                salary,cycle_days,penalty,
                employer_accepted
            )
            VALUES (?,?,?,?,?,?,?,?,1)
        """, (
            gid, bid, cid,
            str(employee["owner_id"]), uid,
            salary, cycle_days, penalty,
        ))

        return int(cursor.lastrowid)


def accept_contract(
    guild_id,
    contract_id,
    actor_id,
):
    gid = str(guild_id)
    uid = str(actor_id)

    with transaction() as db:
        initialize_contracts(db)

        contract = db.execute("""
            SELECT *
            FROM rp_company_employee_contracts
            WHERE guild_id=? AND id=?
        """, (gid, int(contract_id))).fetchone()

        if contract is None:
            raise LookupError("找不到合約")

        if str(contract["employee_user_id"]) != uid:
            raise PermissionError(
                "只有合約指定員工能確認"
            )

        if contract["status"] != "pending":
            raise ValueError(
                "合約目前不能再次確認"
            )

        current = db.execute("""
            SELECT c.owner_id
            FROM company_employees e
            JOIN characters c
              ON c.guild_id=e.guild_id
             AND c.id=e.character_id
            WHERE e.guild_id=?
              AND e.company_id=?
              AND e.character_id=?
              AND e.status='active'
        """, (
            gid,
            contract["company_id"],
            contract["character_id"],
        )).fetchone()

        if (
            current is None
            or str(current["owner_id"]) != uid
        ):
            raise ValueError(
                "員工已離職或角色擁有者變更"
            )

        from core.company_contract_payroll_guard import validate_contract_activation
        validate_contract_activation(db, gid, contract["company_id"], contract["cycle_days"])

        result = db.execute("""
            UPDATE rp_company_employee_contracts
            SET employee_accepted=1,
                status='active',
                activated_at=CURRENT_TIMESTAMP
            WHERE id=?
              AND guild_id=?
              AND status='pending'
              AND employee_user_id=?
        """, (int(contract_id), gid, uid))
        if result.rowcount != 1:
            raise RuntimeError("合約狀態已變更，請重新查詢")

    return "✅ 雙方已確認，正式合約生效。"


def guard_resignation(
    db,
    guild_id,
    company_id,
    character_id,
):
    """離職交易內的安全檢查。

    目前違約金尚未接入帳本，
    所以有違約金的合約必須先走正式解約。
    """

    contract = active_contract(
        db,
        guild_id,
        company_id,
        character_id,
    )

    if contract is None:
        return

    if int(contract["penalty"]) > 0:
        raise PermissionError(
            "此員工仍有正式合約與違約金條款，"
            "請先完成雙方協議或正式解約審核。"
        )


def finish_resignation(
    db,
    guild_id,
    company_id,
    character_id,
):
    """員工離職成功後，在同一交易內結束合約。"""

    db.execute("""
        UPDATE rp_company_employee_contracts
        SET status='terminated',
            terminated_at=CURRENT_TIMESTAMP,
            termination_reason='ordinary_resignation'
        WHERE guild_id=?
          AND company_id=?
          AND character_id=?
          AND status='active'
          AND penalty=0
    """, (
        str(guild_id),
        str(company_id),
        int(character_id),
    ))
