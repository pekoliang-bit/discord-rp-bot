"""培訓學習區待審核預設資料。未提供頻道 ID 前不會啟用任何綁定。"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from modules.action_reward_rules import all_valid_actions, validate_action

_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "action_locations"
    / "training_default_pending.json"
)


def load_defaults() -> dict:
    return json.loads(_SOURCE.read_text(encoding="utf-8"))


def lookup_channel(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None
) -> dict | None:
    guild_id, channel_id = str(guild_id), str(channel_id)
    if overrides is not None and (guild_id, channel_id) in overrides:
        return deepcopy(overrides[(guild_id, channel_id)])
    data = load_defaults()
    if guild_id != str(data["target_guild_id"]):
        return None
    for place in data["locations"]:
        if (
            place.get("channel_id") is not None
            and str(place["channel_id"]) == channel_id
        ):
            return deepcopy(place)
    return None


def get_reviewable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None
) -> list[dict]:
    place = lookup_channel(guild_id, channel_id, overrides)
    return deepcopy(place.get("actions", [])) if place else []


def get_executable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None
) -> list[dict]:
    return all_valid_actions(
        get_reviewable_actions(guild_id, channel_id, overrides)
    )


def get_pending_locations() -> list[dict]:
    """供群主確認地點與行動；沒有頻道連結也可以預覽。"""
    return deepcopy(load_defaults()["locations"])
