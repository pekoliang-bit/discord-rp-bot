
"""排行榜快照名次檢查。"""

from core.database import get_connection
from modules.leaderboard_storage import initialize


def inspect_board(guild_id, cycle, index, board):
    initialize()

    with get_connection() as db:
        rows = db.execute("""
            SELECT rank, entity_key, score
            FROM lb_v11_snapshots
            WHERE guild_id = ?
              AND cycle = ?
              AND idx = ?
              AND board = ?
            ORDER BY score DESC, entity_key
        """, (
            str(guild_id),
            cycle,
            int(index),
            board,
        )).fetchall()

    issues = []
    previous_score = None
    previous_rank = None

    for position, row in enumerate(rows, 1):
        score = float(row["score"])
        rank = int(row["rank"])

        if previous_score is not None and score == previous_score:
            expected = previous_rank
        else:
            expected = position

        if rank != expected:
            issues.append({
                "entity_key": row["entity_key"],
                "actual_rank": rank,
                "expected_rank": expected,
            })

        previous_score = score
        previous_rank = rank

    return {
        "board": board,
        "cycle": cycle,
        "index": int(index),
        "participants": len(rows),
        "valid": not issues,
        "issues": issues,
    }
