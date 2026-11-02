
"""公司薪資週期與近期發薪紀錄查詢。

不扣款、不修改員工資料。
"""

from __future__ import annotations

from core.database import get_connection


def payroll_cycle_summary(
    guild_id,
    business_id,
    actor_id,
) -> str:
    from core.company_payroll_service import (
        initialize_payroll,
    )
    from modules.company_treasury import (
        _finance_permission,
    )

    initialize_payroll()

    gid = str(guild_id).strip()
    bid = str(business_id).strip()
    uid = str(actor_id).strip()

    if not gid or not bid or not uid:
        raise ValueError("缺少必要的公司或玩家編號。")

    with get_connection() as db:
        if not _finance_permission(db, gid, bid, uid):
            raise PermissionError(
                "你沒有此公司的財務查看權限。"
            )

        policy = db.execute(
            """
            SELECT cycle_days, updated_at
            FROM rp_company_payroll_settings
            WHERE guild_id=?
              AND business_id=?
            """,
            (gid, bid),
        ).fetchone()

        rows = db.execute(
            """
            SELECT
                character_id,
                pay_period,
                amount,
                paid_at
            FROM rp_company_payroll
            WHERE guild_id=?
              AND business_id=?
            ORDER BY paid_at DESC, character_id DESC
            LIMIT 10
            """,
            (gid, bid),
        ).fetchall()

    if policy is None:
        cycle_text = "尚未設定"
    else:
        cycle_text = (
            f"每 {int(policy['cycle_days'])} 天"
        )

    lines = [
        "📅 **公司薪資管理**",
        f"公司編號：{bid}",
        f"發薪週期：{cycle_text}",
        "",
        "**最近10筆發薪紀錄**",
    ]

    if not rows:
        lines.append("目前沒有發薪紀錄。")
    else:
        for record in rows:
            period = str(record["pay_period"])

            if period.startswith(("7d:", "14d:")):
                kind = "週期薪資"
            else:
                kind = "舊制薪資"

            lines.append(
                f"角色 #{record['character_id']}｜"
                f"{int(record['amount']):,} 金幣｜"
                f"{kind}｜{period}"
            )

    lines.append("")
    lines.append(
        "※ 發薪必須由具備權限者操作，"
        "實際扣款與防重複以正式帳本為準。"
    )

    return "\n".join(lines)
