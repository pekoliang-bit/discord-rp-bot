
"""排行榜稱號發放服務。"""

from core.database import get_connection
from modules.leaderboard_title_catalog import title_for
from modules.leaderboard_title_storage import (
    grant_title,
    list_titles,
)


def stage_titles(guild_id, index, board=None):
    """依30天正式快照建立稱號收藏。"""
    index = int(index)

    if index < 1:
        raise ValueError("結算期數錯誤")

    with get_connection() as db:
        sql = """
            SELECT board, entity_key, rank
            FROM lb_v11_snapshots
            WHERE guild_id = ?
              AND cycle = '30d'
              AND idx = ?
              AND rank BETWEEN 1 AND 3
        """

        params = [str(guild_id), index]

        if board is not None:
            sql += " AND board = ?"
            params.append(str(board))

        rows = db.execute(sql, params).fetchall()

    count = 0

    for row in rows:
        title = title_for(row["board"], row["rank"])

        if title is None:
            continue

        grant_title(
            guild_id,
            row["entity_key"],
            row["board"],
            title,
            index,
            row["rank"],
        )

        count += 1

    return count


def get_collection(guild_id, entity_key, current_index):
    """查詢已收藏稱號及當期狀態。"""
    return list_titles(
        guild_id,
        entity_key,
        current_index,
    )


def active_titles(guild_id, entity_key, current_index):
    return [
        title
        for title in get_collection(
            guild_id, entity_key, current_index
        )
        if title["active"]
    ]
