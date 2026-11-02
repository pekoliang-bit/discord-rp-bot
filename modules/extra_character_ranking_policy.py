
"""路人角色排行榜資格政策。

只有角色類別為 extra 的角色受到此政策限制。
其他玩家角色與 NPC 沿用原排行榜規則。
"""

from __future__ import annotations

import json

from core.database import get_connection


def _settings(value):
    if isinstance(value, dict):
        return value

    if not value:
        return {}

    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def allow_extra(gid):
    """讀取群主的路人排行榜總開關。"""
    gid = str(gid)

    with get_connection() as db:
        exists = db.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'extra_settings'
            """
        ).fetchone()

        if exists is None:
            return True

        row = db.execute(
            """
            SELECT allow_ranking
            FROM extra_settings
            WHERE guild_id = ?
            """,
            (gid,),
        ).fetchone()

    return bool(row["allow_ranking"]) if row else True


def can_rank(gid, character_id):
    """檢查指定角色是否符合路人排行榜資格。"""
    gid = str(gid)
    character_id = int(character_id)

    with get_connection() as db:
        row = db.execute(
            """
            SELECT status, settings_json
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (gid, character_id),
        ).fetchone()

    if not row or row["status"] != "active":
        return False

    settings = _settings(row["settings_json"])

    if settings.get("character_category") != "extra":
        return True

    if not allow_extra(gid):
        return False

    return settings.get("ranking_eligible", True) is not False


def set_character_eligibility(gid, character_id, enabled):
    """設定個別路人能否參與排行榜。

    不會改動玩家角色或其他類型 NPC。
    """
    gid = str(gid)
    character_id = int(character_id)

    with __import__(
        "core.database",
        fromlist=["transaction"],
    ).transaction() as db:
        row = db.execute(
            """
            SELECT settings_json
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (gid, character_id),
        ).fetchone()

        if row is None:
            raise ValueError("找不到指定角色")

        settings = _settings(row["settings_json"])

        if settings.get("character_category") != "extra":
            raise ValueError("只能修改路人角色的排行資格")

        settings["ranking_eligible"] = bool(enabled)

        db.execute(
            """
            UPDATE characters
            SET settings_json = ?
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                json.dumps(settings, ensure_ascii=False),
                gid,
                character_id,
            ),
        )

    return bool(enabled)


def filter_rows(gid, rows):
    """過濾含 character_id 的排行榜資料列。"""
    result = []

    for row in rows:
        if isinstance(row, dict):
            character_id = row.get("character_id")
        else:
            try:
                character_id = row["character_id"]
            except (TypeError, KeyError, IndexError):
                character_id = None

        if character_id is None:
            # 沒有角色 ID 的非角色榜單不在這裡過濾。
            result.append(row)
            continue

        if can_rank(gid, character_id):
            result.append(row)

    return result
