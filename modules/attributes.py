
"""Discord RP：十三項屬性及雙配點池系統。

一般屬性池：500點
藝能屬性池：500點
初始配點上限：150（額外賽道加成可突破）

自動提供官方預設十三項屬性。
既有伺服器設定不會被覆蓋。
"""

from __future__ import annotations

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
    require_admin,
)

LOG = logging.getLogger(__name__)

POOL_POINTS = 500
BASE_NORMAL = 20
BASE_MAIN = 35
BASE_SECONDARY = 25
BONUS_MAIN = 30
BONUS_SECONDARY = 15
CREATE_CAP = 150
TOTAL_ATTRIBUTES = 13


# ===== 官方預設十三項屬性 =====

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


def default_schema() -> dict:
    return {
        "general": list(DEFAULT_GENERAL_ATTRIBUTES),
        "talent": list(DEFAULT_TALENT_ATTRIBUTES),
        "version": 1,
    }


def parse_names(raw: str) -> list[str]:
    names = [
        name.strip()
        for name in raw.replace("，", ",").split(",")
    ]

    if any(
        not name or len(name) > 30
        for name in names
    ):
        raise ValueError(
            "屬性名稱不得空白，每項最多30字。"
        )

    if len(names) != len(set(names)):
        raise ValueError(
            "屬性名稱不得重複。"
        )

    return names


def validate_schema(schema: dict) -> dict:
    if not isinstance(schema, dict):
        raise InvalidState(
            "官方屬性資料格式不正確。"
        )

    general = schema.get("general")
    talent = schema.get("talent")

    if (
        not isinstance(general, list)
        or not isinstance(talent, list)
        or not general
        or not talent
    ):
        raise InvalidState(
            "一般池或藝能池的設定不正確。"
        )

    names = general + talent

    if len(names) != TOTAL_ATTRIBUTES:
        raise InvalidState(
            "兩個配點池必須合計十三項屬性。"
        )

    if any(
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 30
        for name in names
    ):
        raise InvalidState(
            "存在無效的屬性名稱。"
        )

    if len(set(names)) != TOTAL_ATTRIBUTES:
        raise InvalidState(
            "屬性名稱不能重複。"
        )

    return schema


def load_schema(
    conn,
    guild_id: str,
) -> dict:
    """讀取官方設定；若尚未設定，提供預設值。"""

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
        schema = json.loads(row["data_json"])
    except (TypeError, ValueError) as exc:
        raise InvalidState(
            "官方屬性資料損壞，請聯絡管理員。"
        ) from exc

    return validate_schema(schema)


def ensure_default_schema(
    conn,
    guild_id: str,
):
    """在可寫入的資料庫交易中保存預設屬性。"""

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

    if row is not None:
        return

    schema = default_schema()

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
                schema,
                ensure_ascii=False,
            ),
        ),
    )

    LOG.info(
        "伺服器 %s 已建立預設十三項屬性",
        guild_id,
    )


def parse_allocation(
    raw: str,
    expected_names: list[str],
) -> dict:
    """支援 JSON 或依順序輸入的配點。"""

    raw = raw.strip()

    if raw.startswith("{"):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "JSON 配點格式有誤。"
            ) from exc

        if not isinstance(values, dict):
            raise ValueError(
                "JSON 配點必須使用名稱與數值對應。"
            )

        return values

    pieces = [
        item.strip()
        for item in raw.replace("，", ",").split(",")
    ]

    if len(pieces) != len(expected_names):
        raise ValueError(
            f"此配點池需要輸入"
            f"{len(expected_names)}個整數。"
        )

    try:
        numbers = [
            int(item)
            for item in pieces
        ]
    except ValueError as exc:
        raise ValueError(
            "每項配點都必須是整數。"
        ) from exc

    return dict(
        zip(expected_names, numbers)
    )


def calculate_initial(
    schema: dict,
    main: str,
    secondary: str,
    general: dict,
    talent: dict,
) -> dict:
    """計算並驗證角色初始十三項屬性。"""

    schema = validate_schema(schema)

    all_names = (
        schema["general"]
        + schema["talent"]
    )

    if main not in all_names:
        raise ValueError(
            "主賽道必須對應官方屬性。"
        )

    if secondary not in all_names:
        raise ValueError(
            "副賽道必須對應官方屬性。"
        )

    if main == secondary:
        raise ValueError(
            "主、副賽道不得相同。"
        )

    if set(general) != set(schema["general"]):
        raise ValueError(
            "一般池配點項目不符合官方清單。"
        )

    if set(talent) != set(schema["talent"]):
        raise ValueError(
            "藝能池配點項目不符合官方清單。"
        )

    values = (
        list(general.values())
        + list(talent.values())
    )

    if any(
        type(value) is not int
        or value < 0
        for value in values
    ):
        raise ValueError(
            "配點必須是非負整數。"
        )

    if sum(general.values()) > POOL_POINTS:
        raise ValueError(
            "一般配點池不可超過500點。"
        )

    if sum(talent.values()) > POOL_POINTS:
        raise ValueError(
            "藝能配點池不可超過500點。"
        )

    allocations = {
        **general,
        **talent,
    }

    results = {}

    for name in all_names:

        if name == main:
            base = BASE_MAIN
            bonus = BONUS_MAIN

        elif name == secondary:
            base = BASE_SECONDARY
            bonus = BONUS_SECONDARY

        else:
            base = BASE_NORMAL
            bonus = 0

        allocated = allocations[name]

        if base + allocated > CREATE_CAP:
            raise ValueError(
                f"「{name}」超過"
                "創角配點上限150。"
            )

        results[name] = (
            base + allocated + bonus
        )

    return results


def save_official_schema(
    actor: Actor,
    general_raw: str,
    talent_raw: str,
):
    """管理員設定官方屬性清單。"""

    require_admin(actor)

    general = parse_names(general_raw)
    talent = parse_names(talent_raw)

    schema = validate_schema({
        "general": general,
        "talent": talent,
        "version": 1,
    })

    with transaction() as conn:

        existing = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM attributes
            WHERE guild_id = ?
            """,
            (actor.guild_id,),
        ).fetchone()

        if existing["total"] > 0:
            raise InvalidState(
                "已有角色配點，不能直接改動"
                "官方屬性清單，需先進行版本遷移。"
            )

        conn.execute(
            """
            INSERT OR IGNORE INTO guilds (guild_id)
            VALUES (?)
            """,
            (actor.guild_id,),
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
                ?, 'attributes', ?,
                'official_schema', ?
            )
            """,
            (
                actor.guild_id,
                actor.user_id,
                json.dumps(
                    schema,
                    ensure_ascii=False,
                ),
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
            VALUES (
                ?, ?,
                'attributes.schema.set',
                'official_schema',
                ?
            )
            """,
            (
                actor.guild_id,
                actor.user_id,
                json.dumps(
                    schema,
                    ensure_ascii=False,
                ),
            ),
        )

    return schema


def allocate_initial(
    actor: Actor,
    character_id: int,
    main: str,
    secondary: str,
    general_raw: str,
    talent_raw: str,
):
    """草稿配點。正式角色不能重新配點。"""

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
                "只能替自己的玩家角色配點。"
            )

        if character["status"] != "draft":
            raise InvalidState(
                "只有草稿可以調整初始配點。"
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

        results = calculate_initial(
            schema,
            main,
            secondary,
            general,
            talent,
        )

        settings = json.loads(
            character["settings_json"] or "{}"
        )

        settings["main_track"] = main
        settings["secondary_track"] = secondary

        settings[
            "attribute_schema_version"
        ] = schema["version"]

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

        for name, value in results.items():
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
            VALUES (
                ?, ?,
                'attributes.initial_allocate',
                ?,
                ?
            )
            """,
            (
                actor.guild_id,
                actor.user_id,
                f"character:{character_id}",
                json.dumps(
                    {
                        "main": main,
                        "secondary": secondary,
                        "general": general,
                        "talent": talent,
                        "results": results,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        return {
            "values": results,
            "general_spent": sum(
                general.values()
            ),
            "talent_spent": sum(
                talent.values()
            ),
        }


class Attributes(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="屬性清單",
        description="查看官方十三項屬性與配點池",
    )
    async def show_schema(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            with transaction() as conn:
                ensure_default_schema(
                    conn,
                    actor.guild_id,
                )

                schema = load_schema(
                    conn,
                    actor.guild_id,
                )

            message = (
                "📊 **官方十三項屬性**\n\n"
                "一般池（500點）：\n"
                + "、".join(schema["general"])
                + "\n\n藝能池（500點）：\n"
                + "、".join(schema["talent"])
                + "\n\n創角單項上限：150\n"
                "主、副賽道額外加成"
                "不受此上限限制。\n\n"
                "系統追蹤數值另行計算，"
                "不占用配點。"
            )

            await interaction.response.send_message(
                message,
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @app_commands.command(
        name="設定官方屬性",
        description="管理員設定十三項屬性名稱",
    )
    @app_commands.describe(
        一般屬性="一般池名稱，以逗號分隔",
        藝能屬性="藝能池名稱，以逗號分隔",
    )
    async def set_schema(
        self,
        interaction: discord.Interaction,
        一般屬性: str,
        藝能屬性: str,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            schema = save_official_schema(
                actor,
                一般屬性,
                藝能屬性,
            )

            message = (
                "✅ 官方屬性設定成功！\n\n"
                "一般池："
                + "、".join(schema["general"])
                + "\n藝能池："
                + "、".join(schema["talent"])
            )

            await interaction.response.send_message(
                message,
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @app_commands.command(
        name="初始配點",
        description="設定角色主副賽道及初始屬性",
    )
    @app_commands.describe(
        角色編號="草稿角色編號，測試期間使用",
        主賽道="主賽道對應的屬性名稱",
        副賽道="副賽道對應的屬性名稱",
        一般配點="依官方清單順序輸入整數",
        藝能配點="依官方清單順序輸入整數",
    )
    async def initial(
        self,
        interaction: discord.Interaction,
        角色編號: int,
        主賽道: str,
        副賽道: str,
        一般配點: str,
        藝能配點: str,
    ):
        await interaction.response.defer(
            ephemeral=True,
        )

        try:
            actor = Actor.from_interaction(
                interaction
            )

            result = allocate_initial(
                actor,
                角色編號,
                主賽道.strip(),
                副賽道.strip(),
                一般配點,
                藝能配點,
            )

            lines = [
                f"{name}：{value}"
                for name, value
                in result["values"].items()
            ]

            message = (
                f"✅ 角色 #{角色編號}"
                "初始配點已儲存！\n\n"
                f"一般池：{result['general_spent']}"
                "/500\n"
                f"藝能池：{result['talent_spent']}"
                "/500\n\n"
                + "\n".join(lines)
            )

            await interaction.followup.send(
                message,
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @app_commands.command(
        name="屬性",
        description="查看角色的十三項屬性",
    )
    @app_commands.describe(
        角色編號="角色編號",
    )
    async def show_attributes(
        self,
        interaction: discord.Interaction,
        角色編號: int,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            with get_connection() as conn:
                character = conn.execute(
                    """
                    SELECT owner_id
                    FROM characters
                    WHERE guild_id = ?
                      AND id = ?
                    """,
                    (
                        actor.guild_id,
                        角色編號,
                    ),
                ).fetchone()

                if (
                    character is None
                    or (
                        str(character["owner_id"])
                        != actor.user_id
                        and not actor.is_admin
                    )
                ):
                    raise PermissionDenied(
                        "無權查看這名角色的私人配點。"
                    )

                rows = conn.execute(
                    """
                    SELECT name, value
                    FROM attributes
                    WHERE guild_id = ?
                      AND character_id = ?
                    ORDER BY name
                    """,
                    (
                        actor.guild_id,
                        角色編號,
                    ),
                ).fetchall()

            if not rows:
                message = "尚未完成初始配點。"
            else:
                message = "\n".join(
                    f"{row['name']}：{row['value']}"
                    for row in rows
                )

            await interaction.response.send_message(
                message,
                ephemeral=True,
            )

        except PermissionDenied as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Attributes(bot))
