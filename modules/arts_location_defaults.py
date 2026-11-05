
"""藝文特區預設頻道與行動資料查詢。

不註冊額外 Slash 指令。
不直接修改玩家金幣、屬性或行動值。
群主自訂頻道綁定可由 overrides 傳入。
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from modules.action_reward_rules import (
    validate_action,
    all_valid_actions,
)


DATA_FILE = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "action_locations"
    / "arts_default_pending.json"
)

TARGET_GUILD_ID = "1544051203493601380"

VALID_CATEGORIES = ("打工", "活動", "課程")


def load_defaults() -> dict[str, Any]:
    """讀取藝文特區預設資料。"""
    with DATA_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError("藝文特區設定格式錯誤")

    if str(data.get("target_guild_id")) != TARGET_GUILD_ID:
        raise ValueError("預設伺服器 ID 不正確")

    return deepcopy(data)


def get_all_locations() -> list[dict]:
    """取得所有藝文地點。"""
    return load_defaults().get("locations", [])


def lookup_channel(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> dict | None:
    """依照伺服器、頻道 ID 查詢地點。"""
    guild_id = str(guild_id)
    channel_id = str(channel_id)

    if overrides is not None:
        key = (guild_id, channel_id)

        if key in overrides:
            location = overrides[key]
            return deepcopy(location) if location else None

    data = load_defaults()

    if guild_id != str(data["target_guild_id"]):
        return None

    for location in data["locations"]:
        if str(location.get("channel_id")) == channel_id:
            return deepcopy(location)

    return None


def get_reviewable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> list[dict]:
    """取得指定頻道所有待審核及已核准行動。"""
    location = lookup_channel(
        guild_id,
        channel_id,
        overrides,
    )

    if location is None:
        return []

    return deepcopy(location.get("actions", []))


def get_executable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> list[dict]:
    """只回傳已核准且通過基本規則驗證的行動。"""
    actions = get_reviewable_actions(
        guild_id,
        channel_id,
        overrides,
    )

    return all_valid_actions(actions)


def get_actions_by_category(
    guild_id: int | str,
    channel_id: int | str,
    category: str,
    approved_only: bool = True,
    overrides: dict | None = None,
) -> list[dict]:
    """按照打工、活動、課程分類查詢。"""
    if category not in VALID_CATEGORIES:
        return []

    if approved_only:
        actions = get_executable_actions(
            guild_id,
            channel_id,
            overrides,
        )
    else:
        actions = get_reviewable_actions(
            guild_id,
            channel_id,
            overrides,
        )

    return [
        action
        for action in actions
        if action.get("category") == category
    ]


def get_location_summary(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> dict | None:
    """取得地點行動統計，供管理介面使用。"""
    location = lookup_channel(
        guild_id,
        channel_id,
        overrides,
    )

    if location is None:
        return None

    actions = location.get("actions", [])

    return {
        "key": location.get("key"),
        "name": location.get("name"),
        "channel_id": location.get("channel_id"),
        "total_actions": len(actions),
        "approved_actions": sum(
            1 for action in actions
            if action.get("approved") is True
        ),
        "pending_actions": sum(
            1 for action in actions
            if action.get("approved") is not True
        ),
        "categories": {
            category: sum(
                1 for action in actions
                if action.get("category") == category
            )
            for category in VALID_CATEGORIES
        },
    }


def check_default_configuration() -> dict:
    """檢查所有地點、頻道 ID 及行動設定。"""
    data = load_defaults()

    errors = []
    channel_ids = set()
    action_count = 0

    for location in data.get("locations", []):
        name = location.get("name", "未命名地點")
        channel_id = str(location.get("channel_id") or "")

        if not channel_id:
            errors.append(f"{name}：缺少頻道 ID")

        if channel_id in channel_ids:
            errors.append(f"{name}：頻道 ID 重複")

        channel_ids.add(channel_id)

        for action in location.get("actions", []):
            action_count += 1

            valid, reason = validate_action(action)

            if not valid:
                errors.append(
                    f"{name}／{action.get('name')}：{reason}"
                )

    return {
        "valid": not errors,
        "guild_id": TARGET_GUILD_ID,
        "location_count": len(data.get("locations", [])),
        "action_count": action_count,
        "errors": errors,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            check_default_configuration(),
            ensure_ascii=False,
            indent=2,
        )
    )
