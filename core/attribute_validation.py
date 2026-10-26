
"""Discord RP：唯一主攻技能的初始屬性驗證。

十三項屬性，雙500點配點池。
主攻五選一：演繹、舞蹈、歌唱、綜藝、主持。
沒有副賽道。

此檔僅驗證，不修改資料庫。
"""

from __future__ import annotations

import json

from core.permissions import InvalidState

POOL_LIMIT = 500
CREATE_CAP = 150
BASE_NORMAL = 20
BASE_MAIN = 35
BONUS_MAIN = 30

MAIN_SKILLS = (
    "演繹",
    "舞蹈",
    "歌唱",
    "綜藝",
    "主持",
)


def read_object(raw, label):
    try:
        obj = json.loads(raw or "{}")
    except (ValueError, TypeError) as exc:
        raise InvalidState(
            f"{label}的 JSON 格式不正確。"
        ) from exc

    if not isinstance(obj, dict):
        raise InvalidState(
            f"{label}必須是物件格式。"
        )

    return obj


def read_schema(conn, guild_id):
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

    if not row:
        raise InvalidState(
            "尚未建立官方十三項屬性清單。"
        )

    schema = read_object(
        row["data_json"],
        "官方屬性",
    )

    general = schema.get("general")
    talent = schema.get("talent")

    if (
        not isinstance(general, list)
        or not isinstance(talent, list)
        or len(general) != 5
        or len(talent) != 8
    ):
        raise InvalidState(
            "必須設定一般池5項、藝能池8項。"
        )

    names = general + talent

    if (
        len(set(names)) != 13
        or not all(
            isinstance(x, str) and x.strip()
            for x in names
        )
    ):
        raise InvalidState(
            "官方屬性名稱有重複或空白。"
        )

    if not set(MAIN_SKILLS).issubset(set(talent)):
        raise InvalidState(
            "藝能池缺少五項正式主攻技能。"
        )

    return schema


def calculate_values(schema, main, general, talent):
    if main not in MAIN_SKILLS:
        raise InvalidState(
            "主攻只能選擇演繹、舞蹈、"
            "歌唱、綜藝或主持。"
        )

    if set(general) != set(schema["general"]):
        raise InvalidState(
            "一般池配點與官方屬性不符。"
        )

    if set(talent) != set(schema["talent"]):
        raise InvalidState(
            "藝能池配點與官方屬性不符。"
        )

    all_points = {
        **general,
        **talent,
    }

    for name, value in all_points.items():
        if type(value) is not int or value < 0:
            raise InvalidState(
                f"「{name}」必須是非負整數。"
            )

    if sum(general.values()) > POOL_LIMIT:
        raise InvalidState(
            "一般池不能超過500點。"
        )

    if sum(talent.values()) > POOL_LIMIT:
        raise InvalidState(
            "藝能池不能超過500點。"
        )

    results = {}

    for name, points in all_points.items():
        base = (
            BASE_MAIN
            if name == main
            else BASE_NORMAL
        )

        if base + points > CREATE_CAP:
            raise InvalidState(
                f"「{name}」基礎值加配點"
                "不能超過150。"
            )

        results[name] = (
            base
            + points
            + (
                BONUS_MAIN
                if name == main
                else 0
            )
        )

    return results


def validate_initial_attributes(
    conn,
    guild_id,
    character_id,
):
    """驗證已寫入資料庫的十三項屬性。

    適用於角色送審與正式啟用。
    不會修改已啟用角色的數值。
    """

    guild_id = str(guild_id)

    row = conn.execute(
        """
        SELECT settings_json
        FROM characters
        WHERE guild_id = ?
          AND id = ?
        """,
        (guild_id, character_id),
    ).fetchone()

    if row is None:
        raise InvalidState(
            "找不到角色。"
        )

    schema = read_schema(
        conn,
        guild_id,
    )

    settings = read_object(
        row["settings_json"],
        "角色設定",
    )

    if (
        settings.get("attribute_schema_version")
        != schema.get("version")
    ):
        raise InvalidState(
            "角色屬性版本與官方設定不符。"
        )

    main = settings.get("main_track")

    if main not in MAIN_SKILLS:
        raise InvalidState(
            "角色尚未選擇有效的主攻技能。"
        )

    if settings.get("secondary_track") not in (
        None,
        "",
    ):
        raise InvalidState(
            "此角色保留了舊版副賽道配點，"
            "請在草稿中重新確認主攻與配點。"
        )

    allocations = settings.get(
        "initial_allocation"
    )

    if not isinstance(allocations, dict):
        raise InvalidState(
            "尚未完成初始配點。"
        )

    general = allocations.get("general")
    talent = allocations.get("talent")

    if not isinstance(general, dict):
        raise InvalidState(
            "一般池配點資料不完整。"
        )

    if not isinstance(talent, dict):
        raise InvalidState(
            "藝能池配點資料不完整。"
        )

    expected = calculate_values(
        schema,
        main,
        general,
        talent,
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

    actual = {
        item["name"]: item["value"]
        for item in rows
    }

    if len(rows) != 13 or actual != expected:
        raise InvalidState(
            "目前的十三項屬性與配點紀錄"
            "不一致，請重新完成草稿配點。"
        )

    # 保留舊角色啟用流程使用的回傳欄位。
    # 但副賽道不再有任何技能或數值效果。
    return {
        "schema_version": schema["version"],
        "main_track": main,
        "secondary_track": None,
        "general_spent": sum(general.values()),
        "talent_spent": sum(talent.values()),
        "attributes": expected,
    }
