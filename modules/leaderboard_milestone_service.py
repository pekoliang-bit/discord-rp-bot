
"""排行榜四種累積里程碑統計。

A. 同榜同名次累積
B. 同榜前三名累積
C. 同一獲獎身分跨榜前三名累積
D. 同榜同名次連續蟬聯

安全規則：
- 只讀取已封存的 30 天快照。
- 第 28 榜暫緩。
- 跨榜必須使用有類型的 entity_key。
- 舊式裸 ID 不參與跨榜統計。
- 達成的獎勵只建立待派發紀錄。
- 不直接修改角色或組織資產。
"""

from __future__ import annotations

from core.database import get_connection

from modules.leaderboard_entity_identity import (
    parse_identity,
)
from modules.leaderboard_milestone_rules import reached
from modules.leaderboard_milestone_storage import store


def _count(
    db,
    guild_id,
    entity_key,
    board,
    index,
    rank,
    kind,
):
    """計算同榜歷史累積期數。"""

    sql = """
        SELECT COUNT(DISTINCT idx)
        FROM lb_v11_snapshots
        WHERE guild_id = ?
          AND cycle = '30d'
          AND idx <= ?
          AND entity_key = ?
          AND board = ?
    """

    params = [
        str(guild_id),
        int(index),
        str(entity_key),
        str(board),
    ]

    if kind == "same_rank":
        sql += " AND rank = ?"
        params.append(int(rank))

    elif kind == "board_top3":
        sql += " AND rank BETWEEN 1 AND 3"

    else:
        raise ValueError("不支援的同榜累積類型")

    row = db.execute(sql, params).fetchone()
    return int(row[0])


def _streak(
    db,
    guild_id,
    entity_key,
    board,
    index,
    rank,
):
    """計算同榜同名次連續結算期數。"""

    count = 0
    current_index = int(index)

    while current_index >= 1:
        row = db.execute(
            """
            SELECT 1
            FROM lb_v11_snapshots
            WHERE guild_id = ?
              AND cycle = '30d'
              AND idx = ?
              AND board = ?
              AND entity_key = ?
              AND rank = ?
            LIMIT 1
            """,
            (
                str(guild_id),
                current_index,
                str(board),
                str(entity_key),
                int(rank),
            ),
        ).fetchone()

        if row is None:
            break

        count += 1
        current_index -= 1

    return count


def _cross_count(
    db,
    guild_id,
    entity_key,
    index,
    inclusive=True,
):
    """跨榜前三名獲獎次數。

    同一期在兩個不同排行榜取得前三名，
    算作兩次獲獎。

    不同結算期的同一排行榜可以重複累積。
    """

    operator = "<=" if inclusive else "<"

    sql = f"""
        SELECT COUNT(*)
        FROM (
            SELECT DISTINCT idx, board
            FROM lb_v11_snapshots
            WHERE guild_id = ?
              AND cycle = '30d'
              AND idx {operator} ?
              AND entity_key = ?
              AND rank BETWEEN 1 AND 3
              AND board != 'awards'
        )
    """

    row = db.execute(
        sql,
        (
            str(guild_id),
            int(index),
            str(entity_key),
        ),
    ).fetchone()

    return int(row[0])


def _snapshot_exists(
    db,
    guild_id,
    index,
    board,
    entity_key,
    rank,
):
    """確認指定名次確實存在於正式快照。"""

    row = db.execute(
        """
        SELECT 1
        FROM lb_v11_snapshots
        WHERE guild_id = ?
          AND cycle = '30d'
          AND idx = ?
          AND board = ?
          AND entity_key = ?
          AND rank = ?
        LIMIT 1
        """,
        (
            str(guild_id),
            int(index),
            str(board),
            str(entity_key),
            int(rank),
        ),
    ).fetchone()

    return row is not None


def _queue_reward(
    pending,
    guild_id,
    entity_key,
    board,
    kind,
    dimension,
    index,
    previous,
    current,
):
    """收集本次達成的獎勵，稍後統一寫入。"""

    rewards = reached(kind, previous, current)

    if not rewards:
        return

    pending.append(
        (
            str(guild_id),
            str(entity_key),
            str(board),
            str(kind),
            str(dimension),
            int(index),
            rewards,
        )
    )


def stage_milestones(
    guild_id,
    idx,
    board,
    ranked_rows,
):
    """根據正式快照建立里程碑待派發紀錄。

    回傳本次新增的里程碑紀錄數量。
    重複執行不會重複建立同一門檻。
    """

    if str(board) == "awards":
        return 0

    index = int(idx)

    if index < 1:
        raise ValueError("結算期數必須大於零")

    guild_id = str(guild_id)
    board = str(board)

    pending = []
    cross_entities = set()

    # 先完成所有讀取，關閉讀取連線後再寫入。
    # 避免讀取交易與寫入交易互相阻塞。
    with get_connection() as db:

        for item in ranked_rows:
            entity_key = str(item["entity_key"])
            rank = int(item["rank"])

            if rank < 1:
                continue

            if not _snapshot_exists(
                db,
                guild_id,
                index,
                board,
                entity_key,
                rank,
            ):
                continue

            # A：同榜同名次累積
            same_rank = _count(
                db,
                guild_id,
                entity_key,
                board,
                index,
                rank,
                "same_rank",
            )

            _queue_reward(
                pending,
                guild_id,
                entity_key,
                board,
                "same_rank",
                str(rank),
                index,
                max(0, same_rank - 1),
                same_rank,
            )

            # D：同榜同名次連續蟬聯
            streak = _streak(
                db,
                guild_id,
                entity_key,
                board,
                index,
                rank,
            )

            _queue_reward(
                pending,
                guild_id,
                entity_key,
                board,
                "streak",
                str(rank),
                index,
                max(0, streak - 1),
                streak,
            )

            if rank > 3:
                continue

            # B：同榜任意前三名累積
            top3 = _count(
                db,
                guild_id,
                entity_key,
                board,
                index,
                rank,
                "board_top3",
            )

            _queue_reward(
                pending,
                guild_id,
                entity_key,
                board,
                "board_top3",
                "top3",
                index,
                max(0, top3 - 1),
                top3,
            )

            # C：跨榜只接受明確類型的身分。
            # 裸 ID 不會猜測其為角色或公司。
            if parse_identity(entity_key) is not None:
                cross_entities.add(entity_key)

        # 跨榜累積使用整期已封存的快照，
        # 而非只看本次傳入的單一榜單。
        for entity_key in cross_entities:
            previous = _cross_count(
                db,
                guild_id,
                entity_key,
                index,
                inclusive=False,
            )

            current = _cross_count(
                db,
                guild_id,
                entity_key,
                index,
                inclusive=True,
            )

            _queue_reward(
                pending,
                guild_id,
                entity_key,
                "*",
                "cross_top3",
                "top3",
                index,
                previous,
                current,
            )

    # 使用既有 storage 的唯一鍵防止重複。
    total = 0

    for (
        gid,
        entity_key,
        reward_board,
        kind,
        dimension,
        achieved_index,
        rewards,
    ) in pending:

        total += store(
            gid,
            entity_key,
            reward_board,
            kind,
            dimension,
            achieved_index,
            rewards,
        )

    return total
