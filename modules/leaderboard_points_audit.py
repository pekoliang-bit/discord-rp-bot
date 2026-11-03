
"""排行榜點數資料稽核。"""

from core.database import get_connection
from modules.leaderboard_points_storage import initialize


def inspect_character(guild_id, character_id):
    with get_connection() as db:
        initialize(db)

        balances = db.execute("""
            SELECT pool, balance
            FROM lb_point_balances
            WHERE guild_id = ?
              AND character_id = ?
        """, (
            str(guild_id),
            int(character_id),
        )).fetchall()

        history = db.execute("""
            SELECT
                category,
                COUNT(*) AS operations,
                COALESCE(SUM(amount), 0) AS total
            FROM lb_point_history
            WHERE guild_id = ?
              AND character_id = ?
            GROUP BY category
        """, (
            str(guild_id),
            int(character_id),
        )).fetchall()

    result = {
        "character_id": int(character_id),
        "balances": {
            row["pool"]: int(row["balance"])
            for row in balances
        },
        "history": {
            row["category"]: {
                "operations": int(row["operations"]),
                "total": int(row["total"]),
            }
            for row in history
        },
    }

    result["valid"] = all(
        value >= 0
        for value in result["balances"].values()
    )

    return result
