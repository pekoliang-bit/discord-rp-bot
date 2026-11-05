"""商業經貿區預設場所、專用指令與行動資料查詢。

- 4 個 /行動 場所，48 項待審核行動
- 銀行只能使用 /銀行
- 商城只能使用 /商城
- 不註冊 Slash 指令
- 不直接執行交易
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
    / "business_default_pending.json"
)

TARGET_GUILD_ID = "1544051203493601380"
CATEGORY_ID = "1548941879314489424"

FIELDS = (
    "name",
    "category",
    "cost_coins",
    "reward_coins",
    "attribute",
    "attribute_increase",
    "point_pool",
    "point_amount",
    "action_points_cost",
)

VALID_CATEGORIES = ("打工", "活動", "課程")


def expand_action(record: list | dict) -> dict:
    """將精簡紀錄轉成共用行動格式。"""
    if isinstance(record, dict):
        return deepcopy(record)

    if not isinstance(record, list):
        raise ValueError("行動紀錄必須是清單或物件")

    if len(record) != len(FIELDS):
        raise ValueError(
            f"行動欄位數量不正確：{len(record)}"
        )

    raw = dict(zip(FIELDS, record))

    pool = raw.pop("point_pool")
    amount = raw.pop("point_amount")

    if type(amount) is not int or amount < 0:
        raise ValueError("點數獎勵數量不正確")

    raw["point_rewards"] = (
        {pool: amount}
        if pool is not None and amount > 0
        else {}
    )

    raw["approved"] = False

    return raw


def load_defaults() -> dict[str, Any]:
    """讀取並展開商業經貿區設定。"""
    with DATA_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("商業經貿區 JSON 格式錯誤")

    if str(data.get("target_guild_id")) != TARGET_GUILD_ID:
        raise ValueError("伺服器 ID 不符合預設設定")

    if str(data.get("category_id")) != CATEGORY_ID:
        raise ValueError("商業經貿分類 ID 不正確")

    locations = data.get("locations")

    if not isinstance(locations, list):
        raise ValueError("locations 必須是清單")

    for location in locations:
        actions = location.get("actions", [])

        if not isinstance(actions, list):
            raise ValueError("actions 必須是清單")

        location["actions"] = [
            expand_action(action)
            for action in actions
        ]

    return data


def get_all_locations() -> list[dict]:
    return deepcopy(load_defaults()["locations"])


def get_category_id(
    guild_id: int | str,
    custom_category_id: int | str | None = None,
) -> int | None:
    """取得伺服器商業經貿分類 ID。

    其他伺服器必須提供自己的分類 ID。
    """
    if custom_category_id is not None:
        return int(custom_category_id)

    # Share one authoritative per-guild setting with personal shop channels.
    from modules.player_shop_channels import category_for
    configured = category_for(guild_id)
    if configured is not None:
        return configured

    if str(guild_id) == TARGET_GUILD_ID:
        return int(CATEGORY_ID)

    return None


def lookup_channel(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> dict | None:
    """依伺服器與頻道查詢場所。"""
    guild_id = str(guild_id)
    channel_id = str(channel_id)

    if overrides is not None:
        key = (guild_id, channel_id)

        if key in overrides:
            location = overrides[key]
            return deepcopy(location) if location else None

    if guild_id != TARGET_GUILD_ID:
        return None

    for location in get_all_locations():
        if str(location.get("channel_id")) == channel_id:
            return location

    return None


def is_action_enabled(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> bool:
    location = lookup_channel(
        guild_id,
        channel_id,
        overrides,
    )

    return bool(
        location
        and location.get("action_enabled") is True
        and "行動" in location.get("allowed_commands", [])
    )


def get_required_command(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> str | None:
    """取得該場所指定的專用指令。"""
    location = lookup_channel(
        guild_id,
        channel_id,
        overrides,
    )

    if location is None:
        return None

    if location.get("action_enabled") is True:
        return "行動"

    commands = location.get("allowed_commands", [])
    return commands[0] if commands else None


def get_reviewable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> list[dict]:
    location = lookup_channel(
        guild_id,
        channel_id,
        overrides,
    )

    if location is None:
        return []

    if location.get("action_enabled") is not True:
        return []

    return deepcopy(location.get("actions", []))


def get_executable_actions(
    guild_id: int | str,
    channel_id: int | str,
    overrides: dict | None = None,
) -> list[dict]:
    """僅回傳通過審核與驗證的行動。"""
    return all_valid_actions(
        get_reviewable_actions(
            guild_id,
            channel_id,
            overrides,
        )
    )


def get_actions_by_category(
    guild_id: int | str,
    channel_id: int | str,
    category: str,
    *,
    approved_only: bool = True,
    overrides: dict | None = None,
) -> list[dict]:
    if category not in VALID_CATEGORIES:
        return []

    actions = (
        get_executable_actions(
            guild_id, channel_id, overrides
        )
        if approved_only
        else get_reviewable_actions(
            guild_id, channel_id, overrides
        )
    )

    return [
        action
        for action in actions
        if action.get("category") == category
    ]


def get_bank_robbery_config(
    guild_id: int | str,
) -> dict | None:
    """提供未來銀行事件系統讀取。

    機率與事件結果未核准前，保持停用。
    """
    if str(guild_id) != TARGET_GUILD_ID:
        return None

    return deepcopy(
        load_defaults().get("bank_robbery", {})
    )


def get_shop_channel_config(
    guild_id: int | str,
    custom_category_id: int | str | None = None,
) -> dict | None:
    category_id = get_category_id(
        guild_id,
        custom_category_id,
    )

    if category_id is None:
        return None

    config = deepcopy(
        load_defaults()["shop_channel_management"]
    )

    config["category_id"] = str(category_id)
    return config


def check_default_configuration() -> dict:
    """檢查全部 6 個場所及 48 項行動。"""
    data = load_defaults()

    errors = []
    seen_channels = set()
    action_count = 0
    enabled_count = 0

    for location in data["locations"]:
        name = location.get("name", "未命名")
        channel_id = str(location.get("channel_id") or "")

        if not channel_id:
            errors.append(f"{name}：缺少頻道 ID")
        elif channel_id in seen_channels:
            errors.append(f"{name}：頻道重複")

        seen_channels.add(channel_id)

        enabled = location.get("action_enabled") is True
        actions = location.get("actions", [])

        if enabled:
            enabled_count += 1
        elif actions:
            errors.append(
                f"{name}：專用頻道不得包含行動"
            )

        if name == "銀行":
            if location.get("allowed_commands") != ["銀行"]:
                errors.append("銀行指令設定錯誤")

        if name == "商城":
            if location.get("allowed_commands") != ["商城"]:
                errors.append("商城指令設定錯誤")

        for action in actions:
            action_count += 1

            valid, reason = validate_action(action)

            if not valid:
                errors.append(
                    f"{name}／{action.get('name')}：{reason}"
                )

            if action.get("approved") is True:
                errors.append(
                    f"{name}／{action.get('name')}："
                    "預設待審核資料不可提前核准"
                )

    return {
        "valid": not errors,
        "guild_id": TARGET_GUILD_ID,
        "category_id": CATEGORY_ID,
        "location_count": len(data["locations"]),
        "action_enabled_locations": enabled_count,
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
