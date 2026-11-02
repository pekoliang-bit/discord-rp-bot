
"""排行榜結算完整性檢查。"""

from core.database import get_connection
from modules.leaderboard_storage import initialize


def inspect_period(guild_id, cycle, index):
    if cycle not in ("14d", "30d"):
        raise ValueError("未知結算週期")

    index = int(index)

    if index < 1:
        raise ValueError("結算期數錯誤")

    initialize()

    with get_connection() as db:
        period = db.execute("""
            SELECT state, end_utc
            FROM lb_v11_periods
            WHERE guild_id = ?
              AND cycle = ?
              AND idx = ?
        """, (
            str(guild_id),
            cycle,
            index,
        )).fetchone()

        snapshots = db.execute("""
            SELECT
                board,
                COUNT(*) AS total
            FROM lb_v11_snapshots
            WHERE guild_id = ?
              AND cycle = ?
              AND idx = ?
            GROUP BY board
        """, (
            str(guild_id),
            cycle,
            index,
        )).fetchall()

        awards = db.execute("""
            SELECT
                state,
                COUNT(*) AS total
            FROM lb_v11_awards
            WHERE guild_id = ?
              AND cycle = ?
              AND idx = ?
            GROUP BY state
        """, (
            str(guild_id),
            cycle,
            index,
        )).fetchall()

    snapshot_counts = {
        row["board"]: int(row["total"])
        for row in snapshots
    }

    award_counts = {
        row["state"]: int(row["total"])
        for row in awards
    }

    return {
        "cycle": cycle,
        "index": index,
        "exists": period is not None,
        "snapshotted": (
            period is not None
            and period["state"] == "snapshotted"
        ),
        "end_utc": (
            period["end_utc"]
            if period is not None else None
        ),
        "snapshot_counts": snapshot_counts,
        "snapshot_total": sum(snapshot_counts.values()),
        "award_counts": award_counts,
        "award_total": sum(award_counts.values()),
        "has_unconfigured": (
            award_counts.get("unconfigured", 0) > 0
        ),
    }


def can_stage_rewards(guild_id, cycle, index):
    """只判斷能否建立待派發紀錄。"""
    if cycle != "30d":
        return False

    result = inspect_period(guild_id, cycle, index)

    return (
        result["snapshotted"]
        and result["snapshot_total"] > 0
    )


def can_pay_rewards(guild_id, cycle, index):
    """正式入帳仍未開放。"""
    return False
