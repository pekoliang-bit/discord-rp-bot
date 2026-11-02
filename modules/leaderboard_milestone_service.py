
"""排行榜里程碑統計服務。"""

from core.database import get_connection
from modules.leaderboard_milestone_rules import reached
from modules.leaderboard_milestone_storage import store


def _count(db, guild, entity, board, idx, rank, kind):
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
        str(guild),
        int(idx),
        str(entity),
        str(board),
    ]

    if kind == "board_top3":
        sql += " AND rank BETWEEN 1 AND 3"

    elif kind == "same_rank":
        sql += " AND rank = ?"
        params.append(int(rank))

    else:
        raise ValueError("不支援的累積類型")

    return int(db.execute(sql, params).fetchone()[0])


def _streak(db, guild, entity, board, idx, rank):
    """從本期往前檢查相同名次的連續期數。"""
    count = 0

    while idx > 0:
        row = db.execute("""
            SELECT 1
            FROM lb_v11_snapshots
            WHERE guild_id = ?
              AND cycle = '30d'
              AND idx = ?
              AND entity_key = ?
              AND board = ?
              AND rank = ?
            LIMIT 1
        """, (
            str(guild),
            int(idx),
            str(entity),
            str(board),
            int(rank),
        )).fetchone()

        if row is None:
            break

        count += 1
        idx -= 1

    return count


def stage_milestones(guild_id, idx, board, ranked_rows):
    """建立里程碑待派發紀錄。"""
    if board == "awards":
        return 0

    idx = int(idx)

    if idx < 1:
        raise ValueError("結算期數必須大於零")

    total = 0

    with get_connection() as db:
        for row in ranked_rows:
            entity = str(row["entity_key"])
            rank = int(row["rank"])

            if rank < 1:
                continue

            # 必須存在已封存的本期快照。
            exists = db.execute("""
                SELECT 1
                FROM lb_v11_snapshots
                WHERE guild_id = ?
                  AND cycle = '30d'
                  AND idx = ?
                  AND board = ?
                  AND entity_key = ?
                  AND rank = ?
            """, (
                str(guild_id),
                idx,
                board,
                entity,
                rank,
            )).fetchone()

            if exists is None:
                continue

            kinds = ["same_rank", "streak"]

            if rank <= 3:
                kinds.append("board_top3")

            for kind in kinds:
                if kind == "streak":
                    current = _streak(
                        db, guild_id, entity,
                        board, idx, rank
                    )
                else:
                    current = _count(
                        db, guild_id, entity,
                        board, idx, rank, kind
                    )

                previous = max(0, current - 1)

                dimension = (
                    str(rank)
                    if kind in ("same_rank", "streak")
                    else "top3"
                )

                rewards = reached(
                    kind, previous, current
                )

                if rewards:
                    total += store(
                        guild_id,
                        entity,
                        board,
                        kind,
                        dimension,
                        idx,
                        rewards,
                    )

    return total
