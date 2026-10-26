
"""Discord RP：正式十三項屬性核心。

一般池5項：500點
藝能池8項：500點
主攻技能：五選一
主攻基礎35，額外加成30
其他屬性基礎20
沒有副賽道

本模組只保留核心功能。
玩家從 /創角 配點，從 /查看 查看屬性。
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

POOL_POINTS = 500
CREATE_CAP = 150
BASE_NORMAL = 20
BASE_MAIN = 35
BONUS_MAIN = 30

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
            isinstance(x, str)
            and x.strip()
            for x in names
        )
    ):
        raise InvalidState(
            "十三項屬性名稱有重複或空白。"
        )

    if not set(MAIN_SKILLS).issubset(set(talent)):
        raise InvalidState(
            "藝能池必須包含五項主攻技能。"
        )

    return schema


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
        VALUES (?, 'attributes', 'system',
                'official_schema', ?)
        """,
        (
            str(guild_id),
            json.dumps(
                default_schema(),
                ensure_ascii=False,
            ),
        ),
    )


def parse_allocation(raw, names):
    """支援 JSON 或按順序輸入的整數字串。"""

    if isinstance(raw, dict):
        result = raw

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
                        [int(x) for x in values],
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

    return result


def calculate_initial(
    schema,
    main,
    *args,
):
    """計算初始數值。

    正式呼叫：
        calculate_initial(
            schema, main, general, talent
        )

    亦接受舊呼叫形狀：
        calculate_initial(
            schema, main, None, general, talent
        )

    舊版副賽道若仍有值則拒絕，
    避免默默套用錯誤的加成。
    """

    schema = validate_schema(schema)

    if len(args) == 2:
        general, talent = args

    elif len(args) == 3:
        secondary, general, talent = args

        if secondary not in (None, ""):
            raise ValueError(
                "新版角色沒有副賽道，"
                "請更新創角畫面。"
            )
    else:
        raise TypeError(
            "calculate_initial 參數數量不正確。"
        )

    try:
        return calculate_values(
            schema,
            main,
            general,
            talent,
        )
    except InvalidState as exc:
        raise ValueError(str(exc)) from exc


def allocate_initial(
    actor: Actor,
    character_id: int,
    main: str,
    *args,
):
    """儲存草稿的初始十三項屬性。

    正式呼叫：
        allocate_initial(
            actor, character_id,
            main, general_raw, talent_raw
        )

    保留舊呼叫形狀，但不接受舊副賽道值。
    """

    if len(args) == 2:
        general_raw, talent_raw = args

    elif len(args) == 3:
        secondary, general_raw, talent_raw = args

        if secondary not in (None, ""):
            raise ValueError(
                "副賽道已取消，請重新選擇主攻。"
            )

    else:
        raise TypeError(
            "allocate_initial 參數數量不正確。"
        )

    with transaction() as conn:
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
            != actor.user_id
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

        ensure_default_schema(
            conn,
            actor.guild_id,
        )

        schema = load_schema(
            conn,
            actor.guild_id,
        )

        general = parse_allocation(
            general_raw,
            schema["general"],
        )

        talent = parse_allocation(
            talent_raw,
            schema["talent"],
        )

        values = calculate_initial(
            schema,
            main,
            general,
            talent,
        )

        try:
            settings = json.loads(
                character["settings_json"] or "{}"
            )
        except (TypeError, ValueError):
            raise InvalidState(
                "角色設定資料已損壞。"
            )

        settings["main_track"] = main

        # 舊草稿可能仍保留副賽道。
        # 不會刪除草稿，但新版配點須清除舊加成。
        settings.pop("secondary_track", None)
        settings.pop("wizard_secondary", None)

        settings["attribute_schema_version"] = (
            schema["version"]
        )

        settings["initial_allocation"] = {
            "general": general,
            "talent": talent,
        }

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
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        return {
            "values": values,
            "general_spent": sum(
                general.values()
            ),
            "talent_spent": sum(
                talent.values()
            ),
        }


class Attributes(commands.Cog):
    """屬性核心。

    不另外註冊測試用斜線指令。
    創角和查看系統負責玩家操作介面。
    """

    def __init__(self, bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Attributes(bot))
