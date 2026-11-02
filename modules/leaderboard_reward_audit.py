
"""排行榜獎勵稽核與查詢。"""

import json

from core.database import get_connection
from modules.leaderboard_storage import initialize


def award_summary(guild_id):
    """依狀態統計基礎獎勵筆數。"""
    initialize()

    with get_connection() as db:
        rows = db.execute("""
            SELECT state, COUNT(*) AS total
            FROM lb_v11_awards
            WHERE guild_id = ?
            GROUP BY state
        """, (str(guild_id),)).fetchall()

    return {
        row["state"]: int(row["total"])
        for row in rows
    }


def pending_awards(guild_id, limit=100):
    """查詢待派發獎勵，不執行發放。"""
    initialize()

    limit = max(1, min(int(limit), 1000))

    with get_connection() as db:
        rows = db.execute("""
            SELECT
                cycle,
                idx,
                board,
                entity_key,
                rank,
                reward_json,
                state
            FROM lb_v11_awards
            WHERE guild_id = ?
              AND state = 'pending_adapter'
            ORDER BY idx, board, rank
            LIMIT ?
        """, (
            str(guild_id),
            limit,
        )).fetchall()

    result = []

    for row in rows:
        item = dict(row)

        try:
            item["reward"] = json.loads(
                item.pop("reward_json")
            )
        except (ValueError, TypeError):
            item["reward"] = None
            item.pop("reward_json", None)

        result.append(item)

    return result


def unconfigured_awards(guild_id):
    """列出尚未配置獎勵的排行榜。"""
    initialize()

    with get_connection() as db:
        rows = db.execute("""
            SELECT
                board,
                COUNT(*) AS total
            FROM lb_v11_awards
            WHERE guild_id = ?
              AND state = 'unconfigured'
            GROUP BY board
            ORDER BY board
        """, (str(guild_id),)).fetchall()

    return [
        {
            "board": row["board"],
            "count": int(row["total"]),
        }
        for row in rows
    ]
