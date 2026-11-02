
"""排行榜里程碑資料庫。

僅記錄待派發獎勵，不直接修改角色或組織資產。
"""

import json

from core.database import transaction, get_connection


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS lb_v11_milestones (
            guild_id TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            board TEXT NOT NULL,
            kind TEXT NOT NULL,
            dimension TEXT NOT NULL,
            threshold INTEGER NOT NULL,
            achieved_idx INTEGER NOT NULL,
            reward_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending_adapter',
            created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (
                guild_id, entity_key, board,
                kind, dimension, threshold
            )
        )
    """)


def store(
    guild_id,
    entity_key,
    board,
    kind,
    dimension,
    idx,
    rewards,
):
    """只新增尚未存在的里程碑。"""
    inserted = 0

    with transaction() as db:
        initialize(db)

        for reward in rewards:
            cursor = db.execute("""
                INSERT OR IGNORE INTO lb_v11_milestones (
                    guild_id,
                    entity_key,
                    board,
                    kind,
                    dimension,
                    threshold,
                    achieved_idx,
                    reward_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(guild_id),
                str(entity_key),
                str(board),
                str(kind),
                str(dimension),
                int(reward["threshold"]),
                int(idx),
                json.dumps(reward, ensure_ascii=False),
            ))

            inserted += cursor.rowcount

    return inserted


def list_pending(guild_id, limit=100):
    """供未來正式入帳適配器讀取。"""
    limit = max(1, min(int(limit), 1000))

    with get_connection() as db:
        initialize(db)

        rows = db.execute("""
            SELECT *
            FROM lb_v11_milestones
            WHERE guild_id = ?
              AND state = 'pending_adapter'
            ORDER BY achieved_idx, board, entity_key
            LIMIT ?
        """, (str(guild_id), limit)).fetchall()

        return [dict(row) for row in rows]
