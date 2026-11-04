
"""排行榜點數正式分配服務。"""

from core.database import transaction
from modules.leaderboard_points_catalog import (
    validate_target,
)
from modules.leaderboard_points_storage import initialize


def allocate(
    guild_id,
    character_id,
    user_id,
    pool,
    category,
    target,
    amount,
    request_key,
):
    """提交已由使用者確認的分配請求。"""
    validate_target(pool, category, target)

    amount = int(amount)

    if amount <= 0:
        raise ValueError("分配數量必須大於零")

    if not str(request_key).strip():
        raise ValueError("缺少操作識別碼")

    guild_id = str(guild_id)
    character_id = int(character_id)

    with transaction() as db:
        initialize(db)

        character = db.execute("""
            SELECT owner_id, status
            FROM characters
            WHERE guild_id = ? AND id = ?
        """, (
            guild_id,
            character_id,
        )).fetchone()

        if character is None:
            raise ValueError("角色不存在")

        if character["status"] != "active":
            raise ValueError("角色尚未啟用")

        if str(character["owner_id"]) != str(user_id):
            raise PermissionError("只能分配自己的角色點數")

        old = db.execute("""
            SELECT character_id, pool, amount, category, target FROM lb_point_history
            WHERE guild_id = ? AND source_key = ?
        """, (
            guild_id,
            str(request_key),
        )).fetchone()

        if old is not None:
            if (int(old["character_id"]) != character_id
                or old["pool"] != pool
                or int(old["amount"]) != amount
                or old["category"] != category
                or old["target"] != target):
                raise ValueError("相同操作識別碼對應不同分配內容")
            return {"status": "already_processed"}

        cursor = db.execute("""
            UPDATE lb_point_balances
            SET balance = balance - ?
            WHERE guild_id = ?
              AND character_id = ?
              AND pool = ?
              AND balance >= ?
        """, (
            amount,
            guild_id,
            character_id,
            pool,
            amount,
        ))

        if cursor.rowcount != 1:
            raise ValueError("可用點數不足")

        if category == "attribute":
            db.execute("""
                INSERT INTO attributes (
                    guild_id, character_id, name, value
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, character_id, name)
                DO UPDATE SET
                    value = value + excluded.value,
                    version = version + 1
            """, (
                guild_id,
                character_id,
                target,
                amount,
            ))

        else:
            db.execute("""
                INSERT INTO character_skills (
                    guild_id, character_id,
                    skill_name, skill_value
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(
                    guild_id, character_id, skill_name
                )
                DO UPDATE SET
                    skill_value =
                        skill_value + excluded.skill_value
            """, (
                guild_id,
                character_id,
                target,
                amount,
            ))

        db.execute("""
            INSERT INTO lb_point_history (
                guild_id, character_id, pool,
                amount, category, target, source_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            guild_id,
            character_id,
            pool,
            amount,
            category,
            target,
            str(request_key),
        ))

    return {
        "status": "success",
        "pool": pool,
        "category": category,
        "target": target,
        "amount": amount,
    }
