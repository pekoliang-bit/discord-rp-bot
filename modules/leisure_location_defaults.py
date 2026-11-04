
"""生活休閒區：預設地點、頻道綁定與待審核行動。

功能：
1. 讀取生活休閒區 JSON 預設資料。
2. 依照伺服器 ID 和頻道 ID 查找地點。
3. 允許外部傳入群主自訂綁定。
4. 讀取待審核行動。
5. 排除尚未核准的行動。
6. 驗證基本的金幣、行動值與成長規則。

注意：
- 此模組不註冊新的 Discord Slash 指令。
- 此模組不直接扣款或修改角色數值。
- 正式執行仍須整合 /行動 系統。
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DATA_FILE = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "action_locations"
    / "leisure_default_pending.json"
)

TARGET_GUILD_ID = "1544051203493601380"

VALID_CATEGORIES = {
    "打工",
    "活動",
    "課程",
}

REGULATED_ATTRIBUTES = {
    "影響指數",
    "流量熱度",
    "商業價值",
    "忠粉",
    "黑粉",
    "活躍黑粉",
    "大眾認知度",
}


def load_defaults() -> dict[str, Any]:
    """讀取預設資料，並回傳獨立副本。"""
    with DATA_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError("生活休閒區設定必須是 JSON 物件")

    if str(data.get("target_guild_id")) != TARGET_GUILD_ID:
        raise ValueError("生活休閒區的預設伺服器 ID 不正確")

    return deepcopy(data)


def get_all_locations() -> list[dict[str, Any]]:
    """取得全部預設地點。"""
    return load_defaults().get("locations", [])


def lookup_channel(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> dict[str, Any] | None:
    """依伺服器及頻道查詢場所。

    群主自訂綁定優先於官方預設。
    overrides 使用：
        {
            (guild_id, channel_id): location_data
        }
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
        if str(location.get("channel_id")) == channel_id:
            return deepcopy(location)

    return None


def get_reviewable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> list[dict[str, Any]]:
    """取得指定頻道的全部待審核／已核准項目。"""
    location = lookup_channel(
        guild_id,
        channel_id,
        overrides=overrides,
    )

    if location is None:
        return []

    return deepcopy(location.get("actions", []))


def validate_action(action: dict[str, Any]) -> tuple[bool, str]:
    """驗證行動基本規則。

    此處只檢查設定資料。
    實際屬性是否存在，仍須由角色系統確認。
    """
    category = action.get("category")

    if category not in VALID_CATEGORIES:
        return False, "未知的行動分類"

    try:
        cost = int(action.get("cost_coins", 0))
        reward = int(action.get("reward_coins", 0))
        increase = int(action.get("attribute_increase", 0))
        points = int(action.get("action_points_cost", 0))
    except (TypeError, ValueError):
        return False, "行動數值格式不正確"

    if min(cost, reward, increase, points) < 0:
        return False, "金錢、成長與行動值不可為負數"

    attribute = action.get("attribute")

    if increase > 0 and not attribute:
        return False, "增加屬性時必須指定屬性"

    if attribute in REGULATED_ATTRIBUTES and increase > 0:
        return False, "特殊管制數值不可透過一般場所購買"

    if category == "打工":
        if cost != 0:
            return False, "打工不應收取課程費用"

        if reward < 2000:
            return False, "打工金幣低於最低限制"

        if points != 1:
            return False, "打工必須消耗 1 行動值"

    elif category == "活動":
        if points != 1:
            return False, "一般消費活動必須消耗 1 行動值"

        if increase > 0 and cost != increase * 300:
            return False, "一般活動每點應為 300 金幣"

    elif category == "課程":
        if points != 0:
            return False, "課程不應消耗行動值"

        if increase > 0 and cost != increase * 500:
            return False, "課程每點應為 500 金幣"

    return True, "OK"


def get_executable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> list[dict[str, Any]]:
    """只回傳已核准且通過基本驗證的行動。"""
    actions = get_reviewable_actions(
        guild_id,
        channel_id,
        overrides=overrides,
    )

    result = []

    for action in actions:
        if action.get("approved") is not True:
            continue

        valid, _ = validate_action(action)

        if valid:
            result.append(action)

    return result


def get_actions_by_category(
    guild_id: int | str,
    channel_id: int | str,
    category: str,
    *,
    approved_only: bool = True,
    overrides: dict | None = None,
) -> list[dict[str, Any]]:
    """按照打工、活動、課程分類取得行動。"""
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


def get_location_summary(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> dict[str, Any] | None:
    """提供管理介面使用的場所摘要。"""
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
            for category in ("打工", "活動", "課程")
        },
    }


def check_default_configuration() -> dict[str, Any]:
    """檢查預設資料完整性。"""
    data = load_defaults()

    errors = []
    channel_ids = set()
    action_count = 0

    for location in data.get("locations", []):
        channel_id = str(location.get("channel_id", ""))

        if not channel_id:
            errors.append(
                f"{location.get('name')}: 缺少頻道 ID"
            )

        if channel_id in channel_ids:
            errors.append(
                f"{location.get('name')}: 頻道 ID 重複"
            )

        channel_ids.add(channel_id)

        for action in location.get("actions", []):
            action_count += 1

            valid, reason = validate_action(action)

            if not valid:
                errors.append(
                    f"{location.get('name')} / "
                    f"{action.get('name')}: {reason}"
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
