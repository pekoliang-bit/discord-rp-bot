
"""角色類排行榜資料來源。

支援：
- impact：影響指數
- fans：忠實粉絲
- antis：黑粉
- main_skill：正式主攻數值

路人依照群主總開關及個別資格參賽。
"""

from __future__ import annotations

import json

from core.database import get_connection
from modules.leaderboard_source_common import (
    ranked,
    table_columns,
)


def _parse_settings(raw):
    try:
        result = json.loads(raw or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _ranking_enabled(db, gid):
    if "allow_ranking" not in table_columns(
        db, "extra_settings"
    ):
        return True

    row = db.execute(
        """
        SELECT allow_ranking
        FROM extra_settings
        WHERE guild_id = ?
        """,
        (str(gid),),
    ).fetchone()

    return bool(row["allow_ranking"]) if row else True


def _characters(db, gid):
    rows = db.execute(
        """
        SELECT id, name, settings_json
        FROM characters
        WHERE guild_id = ?
          AND status = 'active'
        """,
        (str(gid),),
    ).fetchall()

    allow = _ranking_enabled(db, gid)
    result = []

    for row in rows:
        settings = _parse_settings(
            row["settings_json"]
        )

        if settings.get("character_category") == "extra":
            if not allow:
                continue
            if settings.get(
                "ranking_eligible", True
            ) is False:
                continue

        result.append(
            {
                "character_id": int(row["id"]),
                "character_name": str(row["name"]),
                "settings": settings,
            }
        )

    return result


def _attribute_score(db, gid, cid, names):
    if not names:
        return None

    placeholders = ",".join("?" for _ in names)

    row = db.execute(
        f"""
        SELECT MAX(value) AS score
        FROM attributes
        WHERE guild_id = ?
          AND character_id = ?
          AND name IN ({placeholders})
        """,
        (str(gid), int(cid), *names),
    ).fetchone()

    return (
        int(row["score"])
        if row and row["score"] is not None
        else None
    )


def _tracking_score(db, gid, cid, column):
    if column not in (
        "loyal_fans",
        "active_antis",
    ):
        return None

    row = db.execute(
        f"""
        SELECT {column} AS score
        FROM character_system_tracking
        WHERE guild_id = ?
          AND character_id = ?
        """,
        (str(gid), int(cid)),
    ).fetchone()

    return int(row["score"]) if row else 0


def query(guild_id, board, limit=100):
    gid = str(guild_id)

    if board not in (
        "impact",
        "fans",
        "antis",
        "main_skill",
    ):
        return [], "unavailable"

    with get_connection() as db:
        if not {
            "guild_id",
            "id",
            "name",
            "status",
            "settings_json",
        }.issubset(table_columns(db, "characters")):
            return [], "unavailable"

        if board in ("fans", "antis"):
            required = {
                "guild_id",
                "character_id",
                "loyal_fans",
                "active_antis",
            }

            if not required.issubset(
                table_columns(
                    db, "character_system_tracking"
                )
            ):
                return [], "unavailable"

        else:
            required = {
                "guild_id",
                "character_id",
                "name",
                "value",
            }

            if not required.issubset(
                table_columns(db, "attributes")
            ):
                return [], "unavailable"

        rows = []

        for character in _characters(db, gid):
            cid = character["character_id"]

            if board == "impact":
                score = _attribute_score(
                    db, gid, cid, ("影響指數",)
                )

            elif board == "fans":
                score = _tracking_score(
                    db, gid, cid, "loyal_fans"
                )

            elif board == "antis":
                score = _tracking_score(
                    db, gid, cid, "active_antis"
                )

            else:
                main = character["settings"].get(
                    "main_track"
                )

                if not isinstance(main, str) or not main:
                    continue

                score = _attribute_score(
                    db, gid, cid, (main,)
                )

            if score is None:
                continue

            rows.append(
                {
                    "character_id": cid,
                    "character_name": (
                        character["character_name"]
                    ),
                    "score": int(score),
                }
            )

    if not rows:
        return [], "empty"

    return ranked(rows, limit), "ready"
