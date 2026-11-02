
"""排行榜稱號收藏與當期有效狀態。

不註冊新 Discord 指令。
不直接修改角色屬性。
"""

from core.database import transaction, get_connection


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS lb_v11_titles (
            guild_id TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            board TEXT NOT NULL,
            title TEXT NOT NULL,
            first_idx INTEGER NOT NULL,
            latest_idx INTEGER NOT NULL,
            best_rank INTEGER NOT NULL,
            active_until_idx INTEGER,
            created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (
                guild_id,
                entity_key,
                board,
                title
            )
        )
    """)


def grant_title(
    guild_id,
    entity_key,
    board,
    title,
    index,
    rank,
):
    """記錄當期前三名稱號。"""
    rank = int(rank)
    index = int(index)

    if rank not in (1, 2, 3):
        raise ValueError("只有前三名有排名稱號")

    if index < 1:
        raise ValueError("結算期數錯誤")

    if not str(title).strip():
        raise ValueError("稱號不可為空")

    with transaction() as db:
        initialize(db)

        db.execute("""
            INSERT INTO lb_v11_titles (
                guild_id,
                entity_key,
                board,
                title,
                first_idx,
                latest_idx,
                best_rank,
                active_until_idx
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                guild_id,
                entity_key,
                board,
                title
            )
            DO UPDATE SET
                latest_idx = excluded.latest_idx,
                best_rank = MIN(
                    lb_v11_titles.best_rank,
                    excluded.best_rank
                ),
                active_until_idx = excluded.active_until_idx
        """, (
            str(guild_id),
            str(entity_key),
            str(board),
            str(title),
            index,
            index,
            rank,
            index,
        ))


def archive_expired(guild_id, current_index):
    """封存過期稱號，不刪除收藏。"""
    with transaction() as db:
        initialize(db)

        cursor = db.execute("""
            UPDATE lb_v11_titles
            SET active_until_idx = NULL
            WHERE guild_id = ?
              AND active_until_idx IS NOT NULL
              AND active_until_idx < ?
        """, (
            str(guild_id),
            int(current_index),
        ))

        return cursor.rowcount


def list_titles(guild_id, entity_key, current_index):
    """取得永久收藏與當期是否有效。"""
    with get_connection() as db:
        initialize(db)

        rows = db.execute("""
            SELECT *
            FROM lb_v11_titles
            WHERE guild_id = ?
              AND entity_key = ?
            ORDER BY latest_idx DESC, best_rank ASC
        """, (
            str(guild_id),
            str(entity_key),
        )).fetchall()

    result = []

    for row in rows:
        item = dict(row)

        item["active"] = (
            item["active_until_idx"] is not None
            and item["active_until_idx"] == int(current_index)
        )

        result.append(item)

    return result
