
"""Discord RP：正式十三項屬性核心。

一般池5項：500點
藝能池8項：500點
主攻技能：五選一
主攻基礎35，額外加成30
其他屬性基礎20
沒有副賽道

特殊規則：
- 影響指數為特殊管制數值。
- 創角時不允許玩家自行分配影響指數。
- 影響指數初始基礎值固定為20。
- 只能透過系統正式規則或指定獎勵增加。
- 不得透過隨機加成、商城購買或付費配點取得。

玩家從 /創角 配點，從 /查看 查看屬性。
本模組負責初始配點與草稿寫入。
"""

from __future__ import annotations

import json
import logging

from discord.ext import commands

from core.database import transaction

from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)

from core.attribute_validation import (
    MAIN_SKILLS,
    calculate_values,
)


LOG = logging.getLogger(__name__)


# ==========================================
# 基本設定
# ==========================================

POOL_POINTS = 500
CREATE_CAP = 150

BASE_NORMAL = 20
BASE_MAIN = 35
BONUS_MAIN = 30

RESTRICTED_INITIAL_STATS = frozenset({
    "影響指數",
})


# ==========================================
# 正式十三項屬性
# ==========================================

DEFAULT_GENERAL_ATTRIBUTES = [
    "流量熱度",
    "商業價值",
    "路人緣",
    "公關",
    "影響指數",
]

DEFAULT_TALENT_ATTRIBUTES = [
    "容貌",
    "才智",
    "歌唱",
    "舞蹈",
    "演繹",
    "主持",
    "綜藝",
    "個人才藝",
]


# ==========================================
# 預設屬性清單
# ==========================================

def default_schema():
    return {
        "general": list(
            DEFAULT_GENERAL_ATTRIBUTES
        ),
        "talent": list(
            DEFAULT_TALENT_ATTRIBUTES
        ),
        "version": 1,
    }


# ==========================================
# 官方屬性清單驗證
# ==========================================

def validate_schema(schema):

    if not isinstance(schema, dict):
        raise InvalidState(
            "官方屬性資料格式錯誤。"
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
            "官方屬性應為一般5項、藝能8項。"
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
            "十三項屬性名稱有重複或空白。"
        )

    if not set(MAIN_SKILLS).issubset(
        set(talent)
    ):
        raise InvalidState(
            "藝能池必須包含五項主攻技能。"
        )

    # 影響指數必須保留在一般池。
    if "影響指數" not in general:
        raise InvalidState(
            "一般屬性池必須包含影響指數。"
        )

    return schema


# ==========================================
# 讀取官方屬性清單
# ==========================================

def load_schema(conn, guild_id):

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
        return default_schema()

    try:
        schema = json.loads(
            row["data_json"]
        )

    except (ValueError, TypeError) as exc:
        raise InvalidState(
            "官方屬性設定無法讀取。"
        ) from exc

    return validate_schema(schema)


# ==========================================
# 確保有預設官方屬性設定
# ==========================================

def ensure_default_schema(conn, guild_id):

    row = conn.execute(
        """
        SELECT id
        FROM module_records
        WHERE guild_id = ?
          AND module = 'attributes'
          AND record_type = 'official_schema'
        LIMIT 1
        """,
        (str(guild_id),),
    ).fetchone()

    if row:
        return

    conn.execute(
        """
        INSERT OR IGNORE INTO guilds (guild_id)
        VALUES (?)
        """,
        (str(guild_id),),
    )

    conn.execute(
        """
        INSERT INTO module_records (
            guild_id,
            module,
            owner_id,
            record_type,
            data_json
        )
        VALUES (
            ?,
            'attributes',
            'system',
            'official_schema',
            ?
        )
        """,
        (
            str(guild_id),
            json.dumps(
                default_schema(),
                ensure_ascii=False,
            ),
        ),
    )


# ==========================================
# 特殊管制配點驗證
# ==========================================

def validate_restricted_points(
    general,
    talent,
):
    """防止玩家自行分配影響指數。

    這裡檢查的是額外配點，不是最終值。
    最終初始值仍由系統給予20。
    """

    for name in RESTRICTED_INITIAL_STATS:

        general_value = general.get(
            name,
            0,
        )

        talent_value = talent.get(
            name,
            0,
        )

        if (
            type(general_value) is not int
            or general_value != 0
        ):
            raise InvalidState(
                f"「{name}」禁止自行配點。"
                "請將額外點數設定為0。"
            )

        if (
            type(talent_value) is not int
            or talent_value != 0
        ):
            raise InvalidState(
                f"「{name}」禁止自行配點。"
                "請將額外點數設定為0。"
            )


# ==========================================
# 解析配點資料
# ==========================================

def parse_allocation(raw, names):
    """支援 JSON 或按官方順序輸入的整數字串。

    一般池仍須包含影響指數欄位，
    但影響指數只能填0。
    """

    if isinstance(raw, dict):
        result = dict(raw)

    elif isinstance(raw, str):
        raw = raw.strip()

        if raw.startswith("{"):
            try:
                result = json.loads(raw)

            except json.JSONDecodeError as exc:
                raise ValueError(
                    "配點 JSON 格式錯誤。"
                ) from exc

        else:
            values = [
                item.strip()
                for item in raw.replace(
                    "，",
                    ",",
                ).split(",")
            ]

            if len(values) != len(names):
                raise ValueError(
                    f"此配點池須填寫"
                    f"{len(names)}個整數。"
                )

            try:
                result = dict(
                    zip(
                        names,
                        [
                            int(x)
                            for x in values
                        ],
                    )
                )

            except ValueError as exc:
                raise ValueError(
                    "配點必須是整數。"
                ) from exc

    else:
        raise ValueError(
            "配點格式不正確。"
        )

    if not isinstance(result, dict):
        raise ValueError(
            "配點必須是名稱與整數對應。"
        )

    if set(result) != set(names):
        raise ValueError(
            "配點項目與官方清單不同。"
        )

    # 輸入階段就阻止違規配點。
    for name in RESTRICTED_INITIAL_STATS:

        if name not in result:
            continue

        value = result[name]

        if (
            type(value) is not int
            or value != 0
        ):
            raise ValueError(
                f"「{name}」不能自行分配點數。"
                "請填寫0。"
            )

    return result


# ==========================================
# 計算初始屬性
# ==========================================

def calculate_initial(
    schema,
    main,
    *args,
):
    """計算初始十三項屬性。

    正式呼叫：
        calculate_initial(
            schema,
            main,
            general,
            talent
        )

    保留舊呼叫方式：
        calculate_initial(
            schema,
            main,
            None,
            general,
            talent
        )

    不接受舊版副賽道。
    """

    schema = validate_schema(schema)

    secondary = None
    if len(args) == 2:
        general, talent = args

    elif len(args) == 3:
        secondary, general, talent = args

        secondary = secondary or None

    else:
        raise TypeError(
            "calculate_initial 參數數量不正確。"
        )

    if (
        not isinstance(general, dict)
        or not isinstance(talent, dict)
    ):
        raise ValueError(
            "配點資料必須是字典。"
        )

    # 正式計算前再次檢查。
    validate_restricted_points(
        general,
        talent,
    )

    try:
        values = calculate_values(
            schema,
            main,
            general,
            talent,
            secondary,
        )

    except InvalidState as exc:
        raise ValueError(
            str(exc)
        ) from exc

    # 影響指數初始值只能由系統提供。
    if values.get("影響指數") != BASE_NORMAL:
        raise ValueError(
            "影響指數初始值必須維持20。"
        )

    return values


# ==========================================
# 儲存草稿初始配點
# ==========================================

def allocate_initial(
    actor: Actor,
    character_id: int,
    main: str,
    *args,
):
    """儲存草稿的正式初始十三項屬性。

    玩家不能替他人配點。
    僅允許未送審草稿。
    初始配點不得增加影響指數。
    """

    secondary = None
    if len(args) == 2:
        general_raw, talent_raw = args

    elif len(args) == 3:
        secondary, general_raw, talent_raw = args

        secondary = secondary or None

    else:
        raise TypeError(
            "allocate_initial 參數數量不正確。"
        )

    with transaction() as conn:

        # ----------------------------------
        # 讀取角色
        # ----------------------------------

        character = conn.execute(
            """
            SELECT *
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchone()

        if (
            character is None
            or str(character["owner_id"])
            != str(actor.user_id)
            or character["character_type"]
            != "player"
        ):
            raise PermissionDenied(
                "只能替自己的原創角色配點。"
            )

        if character["status"] != "draft":
            raise InvalidState(
                "正式角色不能重新分配初始點數。"
            )

        # ----------------------------------
        # 官方屬性設定
        # ----------------------------------

        ensure_default_schema(
            conn,
            actor.guild_id,
        )

        schema = load_schema(
            conn,
            actor.guild_id,
        )

        # ----------------------------------
        # 一般池配點
        # ----------------------------------

        general = parse_allocation(
            general_raw,
            schema["general"],
        )

        # ----------------------------------
        # 藝能池配點
        # ----------------------------------

        talent = parse_allocation(
            talent_raw,
            schema["talent"],
        )

        # ----------------------------------
        # 影響指數特殊限制
        # ----------------------------------

        validate_restricted_points(
            general,
            talent,
        )

        # ----------------------------------
        # 初始屬性計算
        # ----------------------------------

        values = calculate_initial(
            schema,
            main,
            secondary,
            general,
            talent,
        )

        if values.get(
            "影響指數"
        ) != BASE_NORMAL:
            raise InvalidState(
                "影響指數初始值必須為20。"
            )

        # ----------------------------------
        # 讀取角色原設定
        # ----------------------------------

        try:
            settings = json.loads(
                character["settings_json"] or "{}"
            )

        except (TypeError, ValueError):
            raise InvalidState(
                "角色設定資料已損壞。"
            )

        if not isinstance(settings, dict):
            raise InvalidState(
                "角色設定資料格式錯誤。"
            )

        # ----------------------------------
        # 保存主攻技能
        # ----------------------------------

        settings["main_track"] = main

        settings['secondary_track'] = secondary
        settings['wizard_secondary'] = secondary

        # ----------------------------------
        # 保存屬性版本
        # ----------------------------------

        settings["attribute_schema_version"] = (
            schema["version"]
        )

        # ----------------------------------
        # 保存初始配點紀錄
        # ----------------------------------

        settings["initial_allocation"] = {
            "general": general,
            "talent": talent,
        }

        # ----------------------------------
        # 更新十三項初始屬性
        # ----------------------------------

        # 此操作僅允許草稿。
        # 不會覆蓋已啟用角色後續獲得的獎勵。
        conn.execute(
            """
            DELETE FROM attributes
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                actor.guild_id,
                character_id,
            ),
        )

        for name, value in values.items():
            conn.execute(
                """
                INSERT INTO attributes (
                    guild_id,
                    character_id,
                    name,
                    value,
                    version
                )
                VALUES (?, ?, ?, ?, 1)
                """,
                (
                    actor.guild_id,
                    character_id,
                    name,
                    value,
                ),
            )

        # ----------------------------------
        # 更新角色設定
        # ----------------------------------

        conn.execute(
            """
            UPDATE characters
            SET settings_json = ?,
                version = version + 1
            WHERE guild_id = ?
              AND id = ?
              AND status = 'draft'
            """,
            (
                json.dumps(
                    settings,
                    ensure_ascii=False,
                ),
                actor.guild_id,
                character_id,
            ),
        )

        # ----------------------------------
        # 審計紀錄
        # ----------------------------------

        conn.execute(
            """
            INSERT INTO audit_events (
                guild_id,
                actor_id,
                action,
                target_key,
                details_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                actor.guild_id,
                actor.user_id,
                "attributes.initial_allocate",
                f"character:{character_id}",
                json.dumps(
                    {
                        "main": main,
                        "general": general,
                        "talent": talent,
                        "values": values,
                        "restricted_stats": {
                            "影響指數": {
                                "allocated": 0,
                                "initial_value": (
                                    BASE_NORMAL
                                ),
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        # ----------------------------------
        # 回傳配點結果
        # ----------------------------------

        return {
            "values": values,
            "general_spent": sum(
                general.values()
            ),
            "talent_spent": sum(
                talent.values()
            ),
        }


# ==========================================
# Discord 屬性模組
# ==========================================

class Attributes(commands.Cog):
    """正式屬性核心。

    不另外註冊測試用斜線指令。
    玩家從 /創角 配點，
    從 /查看 查看屬性。
    """

    def __init__(self, bot):
        self.bot = bot


# ==========================================
# 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        Attributes(bot)
    )
