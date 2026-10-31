
"""娛樂城跨遊戲七天風險事件橋接。

一般服務檔，不是 Discord Extension。

不直接寫入角色屬性或傳聞。
正式數值規則與外部介面完成接線前，
先以冪等的待處理事件保存建議處分，
避免亂改玩家資料。
"""

from __future__ import annotations

import json
from typing import Any

from core.database import transaction
from modules.casino_effects import (
    assess_risk,
    init_effects_schema,
)


# 以下為已討論的預設數值。
# 這些是待審核效果，並非已生效的處分。

RISK_PRESETS: dict[str, dict[str, Any]] = {
    "normal": {
        "penalties": {},
        "rumor_candidate": False,
    },
    "moderate": {
        "penalties": {
            "路人緣": -5,
        },
        "rumor_candidate": True,
    },
    "high": {
        "penalties": {
            "路人緣": -10,
            "公關": -5,
        },
        "rumor_candidate": True,
    },
    "severe": {
        "penalties": {
            "路人緣": -20,
            "公關": -10,
            "商業價值": -10,
        },
        "rumor_candidate": True,
    },
}


def evaluate_and_queue(
    guild_id: int | str,
    character_id: int,
    *,
    moderate_stake: int = 200_000,
    high_stake: int = 500_000,
    severe_stake: int = 1_000_000,
) -> dict[str, Any]:
    """儲存七天風險評估及每日唯一待處理事件。

    事件以 guild / character / day 作為唯一識別。

    當日重試不重複建立扣分事件。

    不會自行扣除屬性或建立傳聞，
    避免外部系統尚未整合時造成資料損失。
    """

    if type(character_id) is not int or character_id <= 0:
        raise ValueError("角色編號不合法")

    init_effects_schema()

    result = assess_risk(
        guild_id,
        character_id,
        moderate_stake=moderate_stake,
        high_stake=high_stake,
        severe_stake=severe_stake,
    )

    level = str(result["risk_level"])

    if level not in RISK_PRESETS:
        raise ValueError("風險級別不合法")

    payload = {
        "risk_level": level,
        "plays_7d": result["plays_7d"],
        "stake_7d": result["stake_7d"],
        "loss_7d": result["loss_7d"],
        "proposed_penalties": (
            RISK_PRESETS[level]["penalties"]
        ),
        "rumor_candidate": (
            RISK_PRESETS[level]["rumor_candidate"]
        ),
        "applied_to_stats": False,
        "private_notification_required": (
            level != "normal"
        ),
    }

    with transaction() as conn:
        day = conn.execute(
            "SELECT date('now')"
        ).fetchone()[0]

        event_key = (
            f"risk:{character_id}:{day}"
        )

        # 風險達 moderate 以上才產生事件。
        #
        # 不覆寫當日舊事件，避免同一天因為
        # 重複評估而再次執行相同處分。

        if level != "normal":
            conn.execute(
                """
                INSERT OR IGNORE INTO casino_effect_events
                    (
                        guild_id,
                        event_key,
                        character_id,
                        event_kind,
                        payload_json
                    )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(guild_id),
                    event_key,
                    character_id,
                    "risk_review",
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )

            row = conn.execute(
                """
                SELECT
                    id,
                    status,
                    payload_json
                FROM casino_effect_events
                WHERE guild_id = ?
                  AND event_key = ?
                """,
                (
                    str(guild_id),
                    event_key,
                ),
            ).fetchone()

            result["event_id"] = row["id"]
            result["event_status"] = row["status"]

            result["proposed_effects"] = json.loads(
                row["payload_json"]
            )

        else:
            result["event_id"] = None
            result["proposed_effects"] = payload

    return result


def pending_risk_events(
    guild_id: int | str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """取得等待處理的風險事件。

    供後續具備授權的效果執行器使用。
    回傳資料不包含玩家 Discord 身分。
    """

    limit = max(
        1,
        min(int(limit), 100),
    )

    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                guild_id,
                character_id,
                event_key,
                payload_json,
                created_at
            FROM casino_effect_events
            WHERE guild_id = ?
              AND event_kind = 'risk_review'
              AND status = 'pending'
            ORDER BY id
            LIMIT ?
            """,
            (
                str(guild_id),
                limit,
            ),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]
