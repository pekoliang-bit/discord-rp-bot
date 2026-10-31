
"""娛樂城風險效果安全執行器。

放置位置：modules/casino_risk_executor.py

用途：
- 預覽待處理風險效果
- 確認角色及伺服器一致
- 透過正式角色數值適配器執行
- 同一資料庫交易內完成扣點與事件結算
- 避免重複扣點

此檔不提供玩家指令。
正式數值適配器完成前，不能執行扣點。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from core.database import get_connection, transaction
from modules.casino_effects import init_effects_schema


ALLOWED_EFFECTS = {
    "路人緣",
    "公關",
    "商業價值",
}

ALLOWED_LEVELS = {
    "moderate",
    "high",
    "severe",
}


class RiskExecutionError(Exception):
    pass


def get_risk_event(
    guild_id: int | str,
    event_id: int,
) -> dict | None:
    init_effects_schema()

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM casino_effect_events
            WHERE guild_id = ?
              AND id = ?
              AND event_kind = 'risk_review'
            """,
            (
                str(guild_id),
                int(event_id),
            ),
        ).fetchone()

    return dict(row) if row else None


def preview_risk_effect(
    guild_id: int | str,
    event_id: int,
) -> dict[str, Any]:
    """只讀取效果，不寫入角色數值。"""

    event = get_risk_event(
        guild_id,
        event_id,
    )

    if event is None:
        raise RiskExecutionError(
            "找不到娛樂城風險事件"
        )

    try:
        payload = json.loads(
            event["payload_json"]
        )
    except (ValueError, TypeError) as exc:
        raise RiskExecutionError(
            "風險事件內容損壞"
        ) from exc

    level = payload.get("risk_level")

    if level not in ALLOWED_LEVELS:
        raise RiskExecutionError(
            "此風險級別不需要扣點"
        )

    raw = payload.get(
        "proposed_penalties",
        {},
    )

    if not isinstance(raw, dict):
        raise RiskExecutionError(
            "懲罰資料格式不正確"
        )

    penalties = {}

    for name, value in raw.items():
        if name not in ALLOWED_EFFECTS:
            raise RiskExecutionError(
                "發現未授權的數值類型"
            )

        if type(value) is not int or value > 0:
            raise RiskExecutionError(
                "風險懲罰數值不合法"
            )

        if value < -100:
            raise RiskExecutionError(
                "單次懲罰超過安全限制"
            )

        penalties[name] = value

    if not penalties:
        raise RiskExecutionError(
            "風險事件沒有可執行的數值效果"
        )

    return {
        "event_id": int(event["id"]),
        "guild_id": str(event["guild_id"]),
        "character_id": int(event["character_id"]),
        "status": event["status"],
        "risk_level": level,
        "penalties": penalties,
        "rumor_candidate": bool(
            payload.get("rumor_candidate", False)
        ),
    }


def execute_risk_effect(
    guild_id: int | str,
    event_id: int,
    *,
    apply_stats: Callable[..., Any],
) -> dict[str, Any]:
    """使用可信任的數值系統適配器套用效果。

    apply_stats 呼叫格式：

        apply_stats(
            conn=conn,
            guild_id=guild_id,
            character_id=character_id,
            changes=penalties,
            source_key=event_key,
        )

    適配器必須：
    - 使用傳入的 conn
    - 不另開交易
    - 檢查角色屬性上下限
    - 遵守數值啟用及自訂名稱規則
    - 若失敗必須拋出例外

    本函式不應直接暴露給一般玩家。
    """

    if not callable(apply_stats):
        raise RiskExecutionError(
            "尚未連接正式角色數值系統"
        )

    preview = preview_risk_effect(
        guild_id,
        event_id,
    )

    if preview["status"] == "completed":
        return {
            "event_id": event_id,
            "status": "already_completed",
            "applied": False,
        }

    if preview["status"] != "pending":
        raise RiskExecutionError(
            "此風險事件不是待處理狀態"
        )

    with transaction() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM casino_effect_events
            WHERE guild_id = ?
              AND id = ?
              AND event_kind = 'risk_review'
            """,
            (
                str(guild_id),
                int(event_id),
            ),
        ).fetchone()

        if row is None:
            raise RiskExecutionError(
                "風險事件不存在"
            )

        if row["status"] == "completed":
            return {
                "event_id": event_id,
                "status": "already_completed",
                "applied": False,
            }

        if row["status"] != "pending":
            raise RiskExecutionError(
                "事件目前無法執行"
            )

        payload = json.loads(
            row["payload_json"]
        )

        # 使用資料庫中已鎖定的事件資料。
        if (
            str(row["guild_id"]) != preview["guild_id"]
            or int(row["character_id"])
            != preview["character_id"]
            or payload.get("risk_level")
            != preview["risk_level"]
            or payload.get("proposed_penalties")
            != preview["penalties"]
        ):
            raise RiskExecutionError(
                "風險事件內容已變更，請重新確認"
            )

        # 效果與事件狀態必須在同一交易內完成。
        # 不允許適配器自行提交資料庫交易。
        apply_stats(
            conn=conn,
            guild_id=str(guild_id),
            character_id=int(row["character_id"]),
            changes=dict(preview["penalties"]),
            source_key=row["event_key"],
        )

        payload["applied_to_stats"] = True

        cursor = conn.execute(
            """
            UPDATE casino_effect_events
            SET status = 'completed',
                payload_json = ?
            WHERE guild_id = ?
              AND id = ?
              AND status = 'pending'
              AND event_kind = 'risk_review'
            """,
            (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                str(guild_id),
                int(event_id),
            ),
        )

        if cursor.rowcount != 1:
            raise RiskExecutionError(
                "事件狀態更新失敗，交易已取消"
            )

    return {
        "event_id": event_id,
        "status": "completed",
        "applied": True,
        "character_id": preview["character_id"],
        "penalties": preview["penalties"],
    }


def list_pending_risk_effects(
    guild_id: int | str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """取得待處理風險事件的安全預覽。"""

    init_effects_schema()

    limit = min(max(int(limit), 1), 100)

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM casino_effect_events
            WHERE guild_id = ?
              AND event_kind = 'risk_review'
              AND status = 'pending'
            ORDER BY id
            LIMIT ?
            """,
            (str(guild_id), limit),
        ).fetchall()

    results = []

    for row in rows:
        try:
            results.append(
                preview_risk_effect(
                    guild_id,
                    int(row["id"]),
                )
            )
        except RiskExecutionError:
            continue

    return results
