
"""排行榜稱號資料檢查。"""

from core.database import get_connection
from modules.leaderboard_title_storage import initialize


def inspect_titles(guild_id):
    issues = []

    with get_connection() as db:
        initialize(db)

        rows = db.execute("""
            SELECT *
            FROM lb_v11_titles
            WHERE guild_id = ?
        """, (str(guild_id),)).fetchall()

    for row in rows:
        item = dict(row)

        if item["best_rank"] not in (1, 2, 3):
            issues.append({
                "entity_key": item["entity_key"],
                "title": item["title"],
                "issue": "非前三名稱號",
            })

        if item["latest_idx"] < item["first_idx"]:
            issues.append({
                "entity_key": item["entity_key"],
                "title": item["title"],
                "issue": "取得期數不合理",
            })

        active_idx = item["active_until_idx"]

        if (
            active_idx is not None
            and active_idx < item["latest_idx"]
        ):
            issues.append({
                "entity_key": item["entity_key"],
                "title": item["title"],
                "issue": "有效期早於最近取得期數",
            })

    return {
        "total_titles": len(rows),
        "issues": issues,
        "valid": not issues,
    }
