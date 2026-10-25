
"""角色初始屬性完整性驗證。

本檔只讀取、檢查，不會修改任何角色屬性。

適用於目前已建立的 attributes.py 資料格式。
正式主副賽道與對應屬性映射需要後續擴充。
"""

import json

from core.permissions import InvalidState


POOL_LIMIT = 500
CREATE_CAP = 150
TOTAL_ATTRIBUTES = 13

BASE_NORMAL = 20
BASE_MAIN = 35
BASE_SECONDARY = 25

BONUS_MAIN = 30
BONUS_SECONDARY = 15


def _read_json(raw, label):
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise InvalidState(
            f"{label}資料格式損壞。"
        ) from exc

    if not isinstance(value, dict):
        raise InvalidState(
            f"{label}必須是物件格式。"
        )

    return value


def _get_schema(conn, guild_id):
    row = conn.execute(
        """
        SELECT data_json
        FROM module_records
        WHERE guild_id = ?
          AND module = 'attributes'
          AND record_type = 'official_schema'
        ORDER BY id DESC
        LIMIT 1
        """,
        (str(guild_id),),
    ).fetchone()

    if row is None:
        raise InvalidState(
            "伺服器尚未建立官方屬性清單。"
        )

    schema = _read_json(
        row["data_json"],
        "官方屬性清單",
    )

    general = schema.get("general")
    talent = schema.get("talent")

    if not isinstance(general, list):
        raise InvalidState(
            "一般屬性池設定錯誤。"
        )

    if not isinstance(talent, list):
        raise InvalidState(
            "藝能屬性池設定錯誤。"
        )

    names = general + talent

    if len(names) != TOTAL_ATTRIBUTES:
        raise InvalidState(
            "官方屬性必須恰好有十三項。"
        )

    if not all(
        isinstance(name, str) and name.strip()
        for name in names
    ):
        raise InvalidState(
            "官方屬性名稱不可空白。"
        )

    if len(set(names)) != TOTAL_ATTRIBUTES:
        raise InvalidState(
            "官方屬性名稱不得重複。"
        )

    if not general or not talent:
        raise InvalidState(
            "兩個配點池都必須有屬性。"
        )

    return schema


def _validate_pool(
    allocation,
    expected_names,
    pool_label,
):
    if not isinstance(allocation, dict):
        raise InvalidState(
            f"{pool_label}配點紀錄不完整。"
        )

    if set(allocation) != set(expected_names):
        raise InvalidState(
            f"{pool_label}屬性名稱與官方清單不同。"
        )

    for name, points in allocation.items():
        if type(points) is not int:
            raise InvalidState(
                f"{name}的配點必須是整數。"
            )

        if points < 0:
            raise InvalidState(
                f"{name}的配點不可小於零。"
            )

    total = sum(allocation.values())

    if total > POOL_LIMIT:
        raise InvalidState(
            f"{pool_label}超過500點。"
        )

    return total


def validate_initial_attributes(
    conn,
    guild_id,
    character_id,
):
    """驗證一名角色的初始配點是否合法。

    呼叫端應在同一資料庫交易內進行驗證及啟用，
    避免檢查通過後又被其他操作改動。

    成功：回傳包含屬性及配點資訊的字典。
    失敗：拋出 InvalidState。
    """

    guild_id = str(guild_id)

    character = conn.execute(
        """
        SELECT id, settings_json
        FROM characters
        WHERE guild_id = ?
          AND id = ?
        """,
        (guild_id, character_id),
    ).fetchone()

    if character is None:
        raise InvalidState(
            "找不到指定角色。"
        )

    schema = _get_schema(
        conn,
        guild_id,
    )

    settings = _read_json(
        character["settings_json"],
        "角色設定",
    )

    if (
        settings.get("attribute_schema_version")
        != schema.get("version")
    ):
        raise InvalidState(
            "角色配點使用的官方屬性版本不符。"
        )

    main = settings.get("main_track")
    secondary = settings.get("secondary_track")

    names = (
        schema["general"]
        + schema["talent"]
    )

    # 目前沿用上一份 attributes.py 的格式：
    # main_track / secondary_track 暫存為對應屬性。
    # 未來正式賽道映射須另行加入。
    if main not in names:
        raise InvalidState(
            "主賽道對應屬性不合法。"
        )

    if secondary not in names:
        raise InvalidState(
            "副賽道對應屬性不合法。"
        )

    if main == secondary:
        raise InvalidState(
            "主副賽道不能對應相同屬性。"
        )

    allocation = settings.get(
        "initial_allocation"
    )

    if not isinstance(allocation, dict):
        raise InvalidState(
            "尚未儲存完整初始配點。"
        )

    general = allocation.get("general")
    talent = allocation.get("talent")

    spent_general = _validate_pool(
        general,
        schema["general"],
        "一般池",
    )

    spent_talent = _validate_pool(
        talent,
        schema["talent"],
        "藝能池",
    )

    all_points = {
        **general,
        **talent,
    }

    expected_values = {}

    for name in names:
        if name == main:
            base = BASE_MAIN
            bonus = BONUS_MAIN
        elif name == secondary:
            base = BASE_SECONDARY
            bonus = BONUS_SECONDARY
        else:
            base = BASE_NORMAL
            bonus = 0

        points = all_points[name]

        if base + points > CREATE_CAP:
            raise InvalidState(
                f"{name}超過創角配點上限。"
            )

        expected_values[name] = (
            base + points + bonus
        )

    rows = conn.execute(
        """
        SELECT name, value
        FROM attributes
        WHERE guild_id = ?
          AND character_id = ?
        """,
        (guild_id, character_id),
    ).fetchall()

    actual_values = {
        row["name"]: row["value"]
        for row in rows
    }

    if len(actual_values) != TOTAL_ATTRIBUTES:
        raise InvalidState(
            "角色尚未建立完整十三項屬性。"
        )

    if actual_values != expected_values:
        raise InvalidState(
            "角色目前數值與初始配點紀錄不一致。"
            "請由管理員檢查。"
        )

    return {
        "schema_version": schema["version"],
        "main_track": main,
        "secondary_track": secondary,
        "general_spent": spent_general,
        "talent_spent": spent_talent,
        "attributes": expected_values,
    }
