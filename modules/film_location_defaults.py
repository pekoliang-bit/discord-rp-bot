
"""影視娛樂區：場所綁定與待審核行動查詢。

功能：
- 讀取五個影視娛樂地點。
- 按伺服器及頻道 ID 查詢場所。
- 支援群主自訂覆蓋資料。
- 分離待審核與已核准項目。
- 維持與正式影視通告系統的資料隔離。

不註冊 Slash 指令。
不直接修改金幣、行動值、角色數值或作品紀錄。
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
    / "film_default_pending.json"
)

TARGET_GUILD_ID = "1544051203493601380"

VALID_CATEGORIES = (
    "打工",
    "活動",
    "課程",
)


def load_defaults() -> dict[str, Any]:
    """讀取影視娛樂區預設資料。"""
    with DATA_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError("影視娛樂區 JSON 格式錯誤")

    if str(data.get("target_guild_id")) != TARGET_GUILD_ID:
        raise ValueError("影視娛樂區伺服器 ID 不正確")

    if not isinstance(data.get("locations"), list):
        raise ValueError("影視娛樂區缺少 locations 清單")

    return deepcopy(data)


def get_all_locations() -> list[dict[str, Any]]:
    """取得全部影視娛樂地點。"""
    return load_defaults()["locations"]


def lookup_channel(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> dict[str, Any] | None:
    """使用伺服器及頻道 ID 查詢場所。

    overrides 的鍵為：
        (str(guild_id), str(channel_id))

    群主自訂資料優先於預設資料。
    """
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
        configured_channel = location.get("channel_id")

        if configured_channel is None:
            continue

        if str(configured_channel) == channel_id:
            return deepcopy(location)

    return None


def get_reviewable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> list[dict[str, Any]]:
    """取得指定地點的所有行動，包括未核准項目。"""
    location = lookup_channel(
        guild_id,
        channel_id,
        overrides=overrides,
    )

    if location is None:
        return []

    actions = location.get("actions", [])

    return deepcopy(actions) if isinstance(actions, list) else []


def get_executable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> list[dict[str, Any]]:
    """只回傳已核准且通過價格、點數基本驗證的項目。

    注意：
    此函式僅代表資料層可供正式系統讀取。
    不能直接視為角色已符合執行資格。
    """
    actions = get_reviewable_actions(
        guild_id,
        channel_id,
        overrides=overrides,
    )

    return all_valid_actions(actions)


def get_actions_by_category(
    guild_id: int | str,
    channel_id: int | str,
    category: str,
    *,
    approved_only: bool = True,
    overrides: dict | None = None,
) -> list[dict[str, Any]]:
    """按打工、活動、課程分類取得行動。"""
    if category not in VALID_CATEGORIES:
        return []

    if approved_only:
        actions = get_executable_actions(
            guild_id,
            channel_id,
            overrides=overrides,
        )
    else:
        actions = get_reviewable_actions(
            guild_id,
            channel_id,
            overrides=overrides,
        )

    return [
        action
        for action in actions
        if action.get("category") == category
    ]


def get_formal_notice_policy() -> dict[str, Any]:
    """取得影視普通行動與正式通告的隔離規則。"""
    data = load_defaults()
    policy = data.get("policy", {})

    return deepcopy(
        policy.get("formal_notice_separation", {})
    )


def is_formal_notice_action(action: dict) -> bool:
    """一般場所行動不得被當作正式影視通告。"""
    return action.get("formal_notice") is True


def get_location_summary(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> dict[str, Any] | None:
    """供群主預覽場所行動與審核進度。"""
    location = lookup_channel(
        guild_id,
        channel_id,
        overrides=overrides,
    )

    if location is None:
        return None

    actions = location.get("actions", [])

    return {
        "key": location.get("key"),
        "name": location.get("name"),
        "channel_id": str(location.get("channel_id")),
        "total_actions": len(actions),
        "approved_actions": sum(
            1
            for action in actions
            if action.get("approved") is True
        ),
        "pending_actions": sum(
            1
            for action in actions
            if action.get("approved") is not True
        ),
        "categories": {
            category: sum(
                1
                for action in actions
                if action.get("category") == category
            )
            for category in VALID_CATEGORIES
        },
        "formal_notice_separated": True,
    }


def check_default_configuration() -> dict[str, Any]:
    """檢查頻道綁定、行動數量及基本獎勵規則。"""
    data = load_defaults()

    errors = []
    channel_ids = set()
    action_count = 0

    for location in data["locations"]:
        name = location.get("name", "未命名地點")
        channel_id = str(location.get("channel_id") or "")

        if not channel_id:
            errors.append(f"{name}：缺少頻道 ID")

        elif channel_id in channel_ids:
            errors.append(f"{name}：頻道 ID 重複")

        channel_ids.add(channel_id)

        actions = location.get("actions", [])

        if not isinstance(actions, list):
            errors.append(f"{name}：actions 不是清單")
            continue

        for action in actions:
            action_count += 1

            valid, reason = validate_action(action)

            if not valid:
                errors.append(
                    f"{name}／{action.get('name')}：{reason}"
                )

            if is_formal_notice_action(action):
                errors.append(
                    f"{name}／{action.get('name')}："
                    "普通行動不可標記為正式通告"
                )

    return {
        "valid": not errors,
        "guild_id": TARGET_GUILD_ID,
        "location_count": len(data["locations"]),
        "action_count": action_count,
        "formal_notice_separation": True,
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
