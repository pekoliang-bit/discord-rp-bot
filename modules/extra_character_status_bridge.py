
"""路人角色與正式 LV1～LV20 地位系統的橋接。

原則：
1. 初始最高 LV9。
2. 不擅自增加影響指數。
3. 後續評估仍由 character_status.review_character 執行。
4. 正式 rp_character_status 為咖位的主要資料來源。
"""

from __future__ import annotations

import json

from core.database import get_connection, transaction


def _parse_json(raw):
    if isinstance(raw, dict):
        return dict(raw)

    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def initialize_extra_status(
    db,
    guild_id,
    character_id,
    initial_level,
):
    """建立路人時，於同一交易內寫入正式地位。"""
    from modules.character_status import LEVELS

    guild_id = str(guild_id)
    character_id = int(character_id)
    initial_level = int(initial_level)

    if not 1 <= initial_level <= 9:
        raise ValueError(
            "路人角色初始咖位必須介於 LV1～LV9"
        )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS rp_character_status (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            level INTEGER NOT NULL DEFAULT 1,
            maintenance REAL NOT NULL DEFAULT 100,
            last_review TEXT,
            last_promotion TEXT,
            warning_active INTEGER NOT NULL DEFAULT 0,
            main_skill_value INTEGER NOT NULL DEFAULT 0,
            main_skill_changed_at TEXT,
            stagnation_notified_at TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, character_id)
        )
        """
    )

    db.execute(
        """
        INSERT OR IGNORE INTO rp_character_status (
            guild_id,
            character_id,
            level,
            maintenance
        )
        VALUES (?, ?, ?, 100)
        """,
        (
            guild_id,
            character_id,
            initial_level,
        ),
    )

    # 已存在的角色不擅自重設咖位。
    row = db.execute(
        """
        SELECT settings_json
        FROM characters
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            guild_id,
            character_id,
        ),
    ).fetchone()

    if row is None:
        return

    settings = _parse_json(row["settings_json"])

    if settings.get("character_category") != "extra":
        return

    name = LEVELS[initial_level - 1][1]

    settings["initial_status_level"] = initial_level
    settings["initial_status_title"] = name
    settings["status_source"] = "rp_character_status"

    db.execute(
        """
        UPDATE characters
        SET settings_json = ?
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            json.dumps(
                settings,
                ensure_ascii=False,
            ),
            guild_id,
            character_id,
        ),
    )


def get_extra_status(guild_id, character_id):
    """讀取正式地位，不以舊的角色 JSON 覆蓋。"""
    from modules.character_status import (
        get_status,
        effective_levels,
    )

    guild_id = str(guild_id)
    state = get_status(
        guild_id,
        int(character_id),
    )

    level = max(
        1,
        min(20, int(state.get("level", 1))),
    )

    levels = effective_levels(guild_id)

    return {
        "level": level,
        "title": levels[level - 1][1],
        "display": (
            f"LV{level} {levels[level - 1][1]}"
        ),
        "maintenance": float(
            state.get("maintenance", 100)
        ),
        "warning_active": bool(
            state.get("warning_active", 0)
        ),
    }


def sync_extra_status(guild_id, character_id):
    """同步正式地位至角色顯示快取。

    只同步顯示資料，不重新計算或自行晉升。
    """
    guild_id = str(guild_id)
    character_id = int(character_id)

    current = get_extra_status(
        guild_id,
        character_id,
    )

    with transaction() as db:
        row = db.execute(
            """
            SELECT settings_json
            FROM characters
            WHERE guild_id = ?
              AND id = ?
              AND status = 'active'
            """,
            (
                guild_id,
                character_id,
            ),
        ).fetchone()

        if row is None:
            return None

        settings = _parse_json(
            row["settings_json"]
        )

        if settings.get("character_category") != "extra":
            return None

        settings["status_level"] = current["level"]
        settings["level"] = current["level"]
        settings["status_title"] = current["title"]
        settings["rank_display"] = current["display"]

        db.execute(
            """
            UPDATE characters
            SET settings_json = ?
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                json.dumps(
                    settings,
                    ensure_ascii=False,
                ),
                guild_id,
                character_id,
            ),
        )

    return current


def review_and_sync(guild_id, character_id):
    """執行既有正式地位評估並同步路人顯示。

    不取代原本 character_status 的排程。
    """
    from modules.character_status import (
        review_character,
    )

    messages = review_character(
        guild_id,
        character_id,
    )

    current = sync_extra_status(
        guild_id,
        character_id,
    )

    return {
        "status": current,
        "messages": messages,
    }


def sync_all_extras(guild_id):
    """批次同步目前所有啟用中的路人地位。"""
    guild_id = str(guild_id)

    with get_connection() as db:
        rows = db.execute(
            """
            SELECT id
            FROM characters
            WHERE guild_id = ?
              AND status = 'active'
              AND json_extract(
                  settings_json,
                  '$.character_category'
              ) = 'extra'
            """,
            (guild_id,),
        ).fetchall()

    completed = 0

    for row in rows:
        if sync_extra_status(
            guild_id,
            row["id"],
        ):
            completed += 1

    return completed
