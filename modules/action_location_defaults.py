"""夜生活場所預設綁定查詢（資料層；不自動執行未核准行動）。

僅目標伺服器有固定預設頻道。其他伺服器不會繼承別人的 ID；
未來群主自訂綁定可透過 override 參數或管理系統接入。
這不是 Discord extension，也不註冊額外 Slash 指令。
"""
from __future__ import annotations

import json
from pathlib import Path

_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "action_locations"
    / "nightlife_default_pending.json"
)


def load_defaults() -> dict:
    return json.loads(_SOURCE.read_text(encoding="utf-8"))


def lookup_channel(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None
) -> dict | None:
    """查詢綁定；管理員自訂項目優先。"""
    guild_id, channel_id = str(guild_id), str(channel_id)
    if overrides is not None and (guild_id, channel_id) in overrides:
        return overrides[(guild_id, channel_id)]
    data = load_defaults()
    if guild_id != str(data["target_guild_id"]):
        return None
    for location in data["locations"]:
        if str(location["channel_id"]) == channel_id:
            return location
    return None


def get_reviewable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None
) -> list[dict]:
    location = lookup_channel(guild_id, channel_id, overrides)
    return list(location.get("actions", [])) if location else []


def get_executable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None
) -> list[dict]:
    """只回傳已核准且通過最低價格與獎勵驗證的行動。"""
    from modules.action_reward_rules import all_valid_actions
    return all_valid_actions(
        get_reviewable_actions(guild_id, channel_id, overrides)
    )
