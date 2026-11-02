
"""點數分配預覽。"""

from core.database import get_connection
from modules.leaderboard_points_catalog import (
    validate_target,
)
from modules.leaderboard_points_storage import get_balance


def preview(
    guild_id,
    character_id,
    user_id,
    pool,
    category,
    target,
    amount,
):
    validate_target(pool, category, target)

    amount = int(amount)

    if amount <= 0:
        raise ValueError("分配點數必須大於零")

    with get_connection() as db:
        row = db.execute("""
            SELECT owner_id, status
            FROM characters
            WHERE guild_id = ? AND id = ?
        """, (
            str(guild_id),
            int(character_id),
        )).fetchone()

        if row is None or row["status"] != "active":
            raise ValueError("角色不存在或尚未啟用")

        if str(row["owner_id"]) != str(user_id):
            raise PermissionError("無權分配此角色點數")

        if category == "attribute":
            result = db.execute("""
                SELECT value FROM attributes
                WHERE guild_id = ?
                  AND character_id = ?
                  AND name = ?
            """, (
                str(guild_id),
                int(character_id),
                target,
            )).fetchone()

            before = int(result["value"]) if result else 0

        else:
            result = db.execute("""
                SELECT skill_value
                FROM character_skills
                WHERE guild_id = ?
                  AND character_id = ?
                  AND skill_name = ?
            """, (
                str(guild_id),
                int(character_id),
                target,
            )).fetchone()

            before = (
                int(result["skill_value"])
                if result else 0
            )

    balances = get_balance(guild_id, character_id)
    available = balances[pool]

    if amount > available:
        raise ValueError("可用點數不足")

    return {
        "character_id": int(character_id),
        "pool": pool,
        "category": category,
        "target": target,
        "before": before,
        "after": before + amount,
        "amount": amount,
        "balance_before": available,
        "balance_after": available - amount,
        "requires_confirmation": True,
    }
