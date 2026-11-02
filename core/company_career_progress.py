
"""公司員工累加制經驗與升遷查詢。"""

from __future__ import annotations

from core.database import get_connection


def career_progress(guild_id, company_id, character_id):
    with get_connection() as db:
        employee = db.execute(
            """
            SELECT rank, exp, status, hired_at
            FROM company_employees
            WHERE guild_id=? AND company_id=?
              AND character_id=?
            """,
            (
                str(guild_id),
                str(company_id),
                int(character_id),
            ),
        ).fetchone()

        if employee is None:
            raise LookupError("找不到員工紀錄")

        positions = db.execute(
            """
            SELECT rank, title, exp_required,
                   exp_per_work, promotion_mode, salary
            FROM company_job_positions
            WHERE guild_id=? AND company_id=?
            ORDER BY rank
            """,
            (str(guild_id), str(company_id)),
        ).fetchall()

    current_rank = int(employee["rank"])
    total_exp = int(employee["exp"])

    current = next(
        (
            dict(row) for row in positions
            if int(row["rank"]) == current_rank
        ),
        None,
    )

    next_position = next(
        (
            dict(row) for row in positions
            if int(row["rank"]) == current_rank + 1
        ),
        None,
    )

    required = (
        int(next_position["exp_required"])
        if next_position else None
    )

    return {
        "character_id": int(character_id),
        "company_id": str(company_id),
        "status": employee["status"],
        "hired_at": employee["hired_at"],
        "rank": current_rank,
        "total_exp": total_exp,
        "current": current,
        "next": next_position,
        "missing_exp": (
            max(0, required - total_exp)
            if required is not None else None
        ),
        "eligible": (
            required is not None
            and total_exp >= required
        ),
    }


def format_career_progress(data):
    current = data["current"]
    next_position = data["next"]

    lines = [
        "📈 **職位經驗與升遷**",
        f"角色：#{data['character_id']}",
        f"公司：{data['company_id']}",
        f"目前職級：{data['rank']}",
        f"職稱：{current['title'] if current else '未設定'}",
        f"累計經驗：{data['total_exp']:,} EXP",
    ]

    if not next_position:
        lines.append("目前沒有更高一級的職位。")
        return "\n".join(lines)

    lines.extend([
        f"下一職級：{next_position['rank']}｜"
        f"{next_position['title']}",
        f"升遷門檻："
        f"{int(next_position['exp_required']):,} EXP",
        f"尚差：{data['missing_exp']:,} EXP",
        "升遷方式："
        + (
            "達標自動升遷"
            if next_position["promotion_mode"] == "auto"
            else "達標後審核"
        ),
    ])

    return "\n".join(lines)
