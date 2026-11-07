"""Discord RP：唯一主攻技能的初始屬性驗證。

十三項屬性，雙500點配點池。
主攻五選一：演繹、舞蹈、歌唱、綜藝、主持。
沒有副賽道。

特殊規則：
- 影響指數是系統管制數值。
- 創角時不可自行配點影響指數。
- 影響指數保留初始基礎值20。
- 影響指數只能透過系統規則或指定獎勵增加。
- 禁止購買、付費配點及隨機加成取得影響指數。

此檔僅驗證，不修改資料庫。
"""

from __future__ import annotations

import json

from core.permissions import InvalidState


# ==========================================
# 基本設定
# ==========================================

POOL_LIMIT = 500
CREATE_CAP = 150

BASE_NORMAL = 20
BASE_MAIN = 35
BASE_SECONDARY = 25
BONUS_MAIN = 30
BONUS_SECONDARY = 15

MAIN_SKILLS = (
    "演繹",
    "舞蹈",
    "歌唱",
    "綜藝",
    "主持",
)

# ==========================================
# 特殊管制屬性
# ==========================================

RESTRICTED_INITIAL_STATS = frozenset({
    "影響指數",
})


def validate_restricted_allocation(
    general,
    talent,
):
    """禁止創角時自行分配特殊管制數值。

    影響指數仍保留於官方十三項屬性清單。

    這裡檢查的是「玩家額外分配點數」，
    不是角色最終的影響指數數值。
    """

    for name in RESTRICTED_INITIAL_STATS:

        general_points = general.get(
            name,
            0,
        )

        talent_points = talent.get(
            name,
            0,
        )

        if (
            type(general_points) is not int
            or general_points != 0
        ):
            raise InvalidState(
                f"「{name}」禁止自行配點。"
                "請將額外配點調整為0。"
                "只能透過系統規則或指定獎勵取得。"
            )

        if (
            type(talent_points) is not int
            or talent_points != 0
        ):
            raise InvalidState(
                f"「{name}」禁止自行配點。"
                "請將額外配點調整為0。"
                "只能透過系統規則或指定獎勵取得。"
            )


# ==========================================
# JSON 資料讀取
# ==========================================

def read_object(raw, label):
    try:
        obj = json.loads(
            raw or "{}"
        )

    except (
        ValueError,
        TypeError,
    ) as exc:

        raise InvalidState(
            f"{label}的 JSON 格式不正確。"
        ) from exc

    if not isinstance(obj, dict):
        raise InvalidState(
            f"{label}必須是物件格式。"
        )

    return obj


# ==========================================
# 官方屬性清單
# ==========================================

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
        (
            str(guild_id),
        ),
    ).fetchone()

    if not row:
        raise InvalidState(
            "尚未建立官方十三項屬性清單。"
        )

    schema = read_object(
        row["data_json"],
        "官方屬性",
    )

    general = schema.get(
        "general"
    )

    talent = schema.get(
        "talent"
    )

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
            isinstance(x, str)
            and x.strip()
            for x in names
        )
    ):
        raise InvalidState(
            "官方屬性名稱有重複或空白。"
        )

    if not set(
        MAIN_SKILLS
    ).issubset(
        set(talent)
    ):
        raise InvalidState(
            "藝能池缺少五項正式主攻技能。"
        )

    # ======================================
    # 確認特殊管制屬性存在
    # ======================================

    for name in RESTRICTED_INITIAL_STATS:

        if name not in names:
            raise InvalidState(
                f"官方十三項屬性缺少「{name}」。"
            )

    return schema


# ==========================================
# 計算初始屬性
# ==========================================

def calculate_values(
    schema,
    main,
    general,
    talent,
    secondary=None,
):
    """計算正式初始屬性並檢查配點限制。"""

    # ======================================
    # 主攻檢查
    # ======================================

    if main not in MAIN_SKILLS:
        raise InvalidState(
            "主攻只能選擇演繹、舞蹈、"
            "歌唱、綜藝或主持。"
        )

    if secondary is not None and (secondary not in MAIN_SKILLS or secondary == main):
        raise InvalidState('副賽道必須是不同於主攻的有效技能。')

    # ======================================
    # 官方屬性結構檢查
    # ======================================

    if not isinstance(
        general,
        dict,
    ):
        raise InvalidState(
            "一般池配點資料格式錯誤。"
        )

    if not isinstance(
        talent,
        dict,
    ):
        raise InvalidState(
            "藝能池配點資料格式錯誤。"
        )

    if set(general) != set(
        schema["general"]
    ):
        raise InvalidState(
            "一般池配點與官方屬性不符。"
        )

    if set(talent) != set(
        schema["talent"]
    ):
        raise InvalidState(
            "藝能池配點與官方屬性不符。"
        )

    all_points = {
        **general,
        **talent,
    }

    # ======================================
    # 檢查所有配點數值
    # ======================================

    for name, value in all_points.items():

        if (
            type(value) is not int
            or value < 0
        ):
            raise InvalidState(
                f"「{name}」必須是非負整數。"
            )

    # ======================================
    # 影響指數特殊限制
    # ======================================

    validate_restricted_allocation(
        general,
        talent,
    )

    # ======================================
    # 雙500點配點池
    # ======================================

    if sum(
        general.values()
    ) > POOL_LIMIT:

        raise InvalidState(
            "一般池不能超過500點。"
        )

    if sum(
        talent.values()
    ) > POOL_LIMIT:

        raise InvalidState(
            "藝能池不能超過500點。"
        )

    # ======================================
    # 正式初始屬性計算
    # ======================================

    results = {}

    for name, points in all_points.items():

        # 影響指數禁止手動增加。
        # 初始值固定採用官方基礎值20。
        if name in RESTRICTED_INITIAL_STATS:

            results[name] = BASE_NORMAL

            continue

        base = (BASE_MAIN if name == main else BASE_SECONDARY if name == secondary else BASE_NORMAL)

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
                else BONUS_SECONDARY if name == secondary else 0
            )
        )

    return results


# ==========================================
# 驗證初始十三項屬性
# ==========================================

def validate_initial_attributes(
    conn,
    guild_id,
    character_id,
):
    """驗證已寫入資料庫的十三項初始屬性。

    適用於角色送審與正式啟用。

    不修改資料庫。
    不修改已啟用角色的數值。

    注意：
    此處驗證的是初始配點結果，
    不應用於檢查角色後續獲得獎勵後
    的即時總屬性。
    """

    guild_id = str(
        guild_id
    )

    row = conn.execute(
        """
        SELECT settings_json
        FROM characters
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            guild_id,
            character_id,
        ),
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

    # ======================================
    # 官方屬性版本驗證
    # ======================================

    if (
        settings.get(
            "attribute_schema_version"
        )
        != schema.get("version")
    ):
        raise InvalidState(
            "角色屬性版本與官方設定不符。"
        )

    # ======================================
    # 主攻驗證
    # ======================================

    main = settings.get(
        "main_track"
    )

    if main not in MAIN_SKILLS:
        raise InvalidState(
            "角色尚未選擇有效的主攻技能。"
        )

    secondary = settings.get('secondary_track') or None
    if secondary is not None and (secondary not in MAIN_SKILLS or secondary == main):
        raise InvalidState('副賽道必須與主攻不同，且屬於正式技能。')

    # ======================================
    # 取得初始配點
    # ======================================

    allocations = settings.get(
        "initial_allocation"
    )

    if not isinstance(
        allocations,
        dict,
    ):
        raise InvalidState(
            "尚未完成初始配點。"
        )

    general = allocations.get(
        "general"
    )

    talent = allocations.get(
        "talent"
    )

    if not isinstance(
        general,
        dict,
    ):
        raise InvalidState(
            "一般池配點資料不完整。"
        )

    if not isinstance(
        talent,
        dict,
    ):
        raise InvalidState(
            "藝能池配點資料不完整。"
        )

    # ======================================
    # 再次確認影響指數未違規配點
    # ======================================

    validate_restricted_allocation(
        general,
        talent,
    )

    # ======================================
    # 重新計算正式初始屬性
    # ======================================

    expected = calculate_values(
        schema,
        main,
        general,
        talent,
        secondary,
    )

    # ======================================
    # 讀取資料庫中的屬性
    # ======================================

    rows = conn.execute(
        """
        SELECT name, value
        FROM attributes
        WHERE guild_id = ?
          AND character_id = ?
        """,
        (
            guild_id,
            character_id,
        ),
    ).fetchall()

    actual = {
        item["name"]: item["value"]
        for item in rows
    }

    # ======================================
    # 十三項初始屬性一致性檢查
    # ======================================

    if (
        len(rows) != 13
        or actual != expected
    ):
        raise InvalidState(
            "目前的十三項初始屬性與配點紀錄"
            "不一致，請重新完成草稿配點。"
        )

    # ======================================
    # 保留原版回傳資料結構
    # ======================================

    return {
        "schema_version": schema["version"],

        "main_track": main,

        "secondary_track": secondary,

        "general_spent": sum(
            general.values()
        ),

        "talent_spent": sum(
            talent.values()
        ),

        "attributes": expected,
    }
