"""公司合約正式解約服務。

違約金由角色錢包支付至原公司的金庫。
公司同意協議解約時免收違約金。
"""

from __future__ import annotations

from core.database import (
    get_connection,
    get_or_create_account,
    transaction,
)
from core.economy import _payment
from core.company_contract_exit_rules import (
    is_self_company_grace,
    calculate_exit_penalty,
)
from core.company_contract_exit_store import (
    initialize_exit_store,
    get_exit,
    get_open_exit,
)


def initialize():
    with transaction() as db:
        initialize_exit_store(db)


def _owned_company_during_grace(
    db, guild_id, user_id, hired_at
):
    rows = db.execute("""
        SELECT founded_at
        FROM rp_business_entities
        WHERE guild_id=?
          AND owner_id=?
          AND kind='company'
          AND status='active'
          AND founded_at IS NOT NULL
    """, (
        str(guild_id),
        str(user_id),
    )).fetchall()

    return any(
        is_self_company_grace(
            row["founded_at"], hired_at
        )
        for row in rows
    )


def request_exit(
    guild_id,
    contract_id,
    actor_id,
    reason,
):
    """員工本人提出解約。"""
    initialize()

    gid = str(guild_id)
    uid = str(actor_id)

    if reason not in (
        "ordinary", "mutual", "self_company"
    ):
        raise ValueError("未知的解約類型")

    with transaction() as db:
        from core.company_public_ids import resolve_contract
        contract_id = resolve_contract(db, gid, contract_id)
        contract = db.execute("""
            SELECT *
            FROM rp_company_employee_contracts
            WHERE guild_id=? AND id=?
              AND status='active'
        """, (
            gid, int(contract_id)
        )).fetchone()

        if contract is None:
            raise LookupError("找不到生效中的合約")

        if str(contract["employee_user_id"]) != uid:
            raise PermissionError(
                "只有合約員工本人可以申請解約"
            )

        employee = db.execute("""
            SELECT e.hired_at, c.owner_id
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
            employee is None
            or str(employee["owner_id"]) != uid
        ):
            raise ValueError(
                "員工已離職或角色擁有者已變更"
            )

        if reason == "self_company":
            if not _owned_company_during_grace(
                db, gid, uid, employee["hired_at"]
            ):
                raise PermissionError(
                    "未符合成立自己公司後5天內"
                    "合理解約的條件"
                )

        old = get_open_exit(
            db, gid, contract_id
        )

        if old:
            if (
                str(old["requested_by"]) == uid
                and old["reason"] == reason
            ):
                return int(old["id"])

            raise ValueError(
                "這份合約已有待處理的解約案件"
            )

        cursor = db.execute("""
            INSERT INTO rp_company_contract_exits
            (
                guild_id,contract_id,company_id,
                character_id,requested_by,reason,
                payment_key
            )
            VALUES (?,?,?,?,?,?,?)
        """, (
            gid,
            int(contract_id),
            str(contract["company_id"]),
            int(contract["character_id"]),
            uid,
            reason,
            f"contract-exit:{gid}:{contract_id}",
        ))

        return int(cursor.lastrowid)


def approve_mutual_exit(
    guild_id,
    exit_id,
    employer_id,
):
    """現任公司持有人批准雙方協議解約。"""
    from modules.company_treasury import _is_owner

    initialize()
    gid = str(guild_id)

    with transaction() as db:
        case = get_exit(db, gid, exit_id)

        if case is None:
            raise LookupError("找不到解約申請")

        if case["status"] != "pending":
            raise ValueError("案件已處理")

        if case["reason"] != "mutual":
            raise ValueError(
                "只有雙方協議解約需要此項核准"
            )

        if not _is_owner(
            db, gid,
            case["company_id"],
            employer_id,
        ):
            raise PermissionError(
                "只有現任公司持有人可以同意解約"
            )

        db.execute("""
            UPDATE rp_company_contract_exits
            SET company_approved=1
            WHERE guild_id=? AND id=?
              AND status='pending'
        """, (gid, int(exit_id)))

    return "✅ 公司已同意協議解約，員工可完成解約。"


def finalize_exit(
    guild_id,
    exit_id,
    actor_id,
):
    """完成解約；付款與員工離職共用同一交易。"""
    initialize()

    gid = str(guild_id)
    uid = str(actor_id)

    with transaction() as db:
        case = get_exit(db, gid, exit_id)

        if case is None:
            raise LookupError("找不到解約案件")

        if case["status"] == "completed":
            if str(case["requested_by"]) != uid:
                raise PermissionError("無權查看此案件")
            return "ℹ️ 本案件已完成，未再次扣費。"

        if case["status"] != "pending":
            raise ValueError("案件已取消或不可結案")

        if str(case["requested_by"]) != uid:
            raise PermissionError(
                "只有原申請員工能完成解約"
            )

        contract = db.execute("""
            SELECT *
            FROM rp_company_employee_contracts
            WHERE guild_id=? AND id=?
              AND status='active'
        """, (gid, case["contract_id"])).fetchone()

        if contract is None:
            raise ValueError("正式合約已失效")

        if (
            str(contract["employee_user_id"]) != uid
            or str(contract["company_id"])
                != str(case["company_id"])
            or int(contract["character_id"])
                != int(case["character_id"])
        ):
            raise RuntimeError("解約案件與合約歸屬不符")

        employee = db.execute("""
            SELECT e.hired_at, c.owner_id
            FROM company_employees e
            JOIN characters c
              ON c.guild_id=e.guild_id
             AND c.id=e.character_id
            WHERE e.guild_id=? AND e.company_id=?
              AND e.character_id=?
              AND e.status='active'
        """, (
            gid,
            case["company_id"],
            case["character_id"],
        )).fetchone()

        if (
            employee is None
            or str(employee["owner_id"]) != uid
        ):
            raise ValueError("員工身分已失效")

        verified = False

        if case["reason"] == "self_company":
            verified = _owned_company_during_grace(
                db, gid, uid, employee["hired_at"]
            )

        penalty = calculate_exit_penalty(
            int(contract["penalty"]),
            case["reason"],
            mutual_approved=bool(
                case["company_approved"]
            ),
            self_company_verified=verified,
        )

        if penalty:
            sender = get_or_create_account(
                db, gid,
                "character",
                str(case["character_id"]),
                "wallet",
            )

            receiver = get_or_create_account(
                db, gid,
                "organization",
                str(case["company_id"]),
                "treasury",
            )

            result = _payment(
                db,
                gid,
                case["payment_key"],
                sender,
                receiver,
                penalty,
                "員工正式解約違約金",
            )

            if result.duplicated:
                raise RuntimeError(
                    "帳本已有付款但案件未完成，"
                    "請管理員核對，不得再次扣款"
                )

        updated = db.execute("""
            UPDATE company_employees
            SET status='resigned'
            WHERE guild_id=? AND company_id=?
              AND character_id=? AND status='active'
        """, (
            gid,
            case["company_id"],
            case["character_id"],
        ))

        if updated.rowcount != 1:
            raise RuntimeError(
                "員工狀態已變更，解約交易已取消"
            )

        for table in (
            "company_promotions",
            "company_work_records",
        ):
            db.execute(
                f"""
                UPDATE {table}
                SET status='cancelled'
                WHERE guild_id=? AND company_id=?
                  AND character_id=? AND status='pending'
                """,
                (
                    gid,
                    case["company_id"],
                    case["character_id"],
                ),
            )

        db.execute("""
            UPDATE rp_company_employee_contracts
            SET status='terminated',
                terminated_at=CURRENT_TIMESTAMP,
                termination_reason=?
            WHERE guild_id=? AND id=?
              AND status='active'
        """, (
            case["reason"],
            gid,
            case["contract_id"],
        ))

        db.execute("""
            UPDATE rp_company_contract_exits
            SET status='completed',
                penalty_amount=?,
                completed_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND id=?
              AND status='pending'
        """, (
            penalty, gid, int(exit_id)
        ))

    return (
        "✅ 正式解約完成，已保留原職位經驗。\n"
        f"違約金：{penalty:,} 金幣。"
    )


def list_exit_cases(guild_id, actor_id, limit=15):
    """查閱本人提出或本人持有公司之解約案件；不外洩其他公司資料。"""
    from modules.company_treasury import _is_owner

    initialize()
    gid, uid = str(guild_id), str(actor_id)
    with get_connection() as db:
        rows = db.execute("""
            SELECT id,contract_id,company_id,character_id,requested_by,
                   reason,company_approved,penalty_amount,status,created_at
            FROM rp_company_contract_exits
            WHERE guild_id=?
            ORDER BY id DESC LIMIT 100
        """, (gid,)).fetchall()
        result = []
        for row in rows:
            if str(row['requested_by']) == uid or _is_owner(db, gid, row['company_id'], uid):
                result.append(dict(row))
            if len(result) >= min(max(int(limit), 1), 30):
                break
        return result
