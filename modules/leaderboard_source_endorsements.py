
"""代言數量排行榜：只計算已接受且目前仍生效的正式代言。唯讀。"""
from __future__ import annotations

from core.database import get_connection
from modules.leaderboard_source_common import ranked, table_columns


def query(guild_id, limit=100):
    required = {
        "rp_endorsement_contracts": {"guild_id", "character_id", "id", "status"},
        "characters": {"guild_id", "id", "name", "status"},
    }
    with get_connection() as db:
        if any(not cols <= table_columns(db, table) for table, cols in required.items()):
            return [], "unavailable"
        rows = db.execute("""
            SELECT c.id AS character_id, c.name AS character_name,
                   COUNT(DISTINCT e.id) AS score
            FROM characters AS c
            JOIN rp_endorsement_contracts AS e
              ON e.guild_id = c.guild_id AND e.character_id = c.id
            WHERE c.guild_id = ? AND c.status = 'active' AND e.status = 'active'
            GROUP BY c.id, c.name
        """, (str(guild_id),)).fetchall()
    data = [dict(row) for row in rows]
    return (ranked(data, limit), "ready") if data else ([], "empty")
