
"""Discord RP 角色初始屬性及雙配點池系統。"""

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


def parse_names(raw: str) -> list[str]:
    names = [
        name.strip()
        for name in raw.replace("，", ",").split(",")
    ]

    if any(not name or len(name) > 30 for name in names):
        raise ValueError(
            "屬性名稱不得空白，每項最多 30 字。"
        )

    if len(names) != len(set(names)):
        raise ValueError("屬性名稱不得重複。")

    return names


def load_schema(conn, guild_id: str) -> dict:
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
        (guild_id,),
    ).fetchone()

    if row is None:
        raise InvalidState(
            "管理員尚未設定官方十三項屬性。"
        )

    schema = json.loads(row["data_json"])

    if (
        len(schema["general"])
        + len(schema["talent"])
        != TOTAL_ATTRIBUTES
    ):
        raise InvalidState(
            "官方屬性清單不完整。"
        )

    return schema


def parse_allocation(
    raw: str,
    expected_names: list[str],
) -> dict:
    """依官方清單順序讀取各項配點。"""

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
                "JSON 配點必須是屬性名稱與數值的對應表。"
            )

        return values

    pieces = [
        item.strip()
        for item in raw.replace("，", ",").split(",")
    ]

    if len(pieces) != len(expected_names):
        raise ValueError(
            f"這個配點池需要輸入 "
            f"{len(expected_names)} 個整數。"
        )

    try:
        numbers = [int(item) for item in pieces]
    except ValueError as exc:
        raise ValueError(
            "每項配點都必須是整數。"
        ) from exc

    return dict(zip(expected_names, numbers))


def calculate_initial(
    schema: dict,
    main: str,
    secondary: str,
    general: dict,
    talent: dict,
) -> dict:
    """核對兩個配點池並產生初始屬性。"""

    all_names = (
        schema["general"] + schema["talent"]
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
            "主副賽道不得使用相同屬性。"
        )

    if set(general) != set(schema["general"]):
        raise ValueError(
            "一般池配點項目不符合官方清單。"
        )

    if set(talent) != set(schema["talent"]):
        raise ValueError(
            "藝能池配點項目不符合官方清單。"
        )

    values = list(general.values()) + list(
        talent.values()
    )

    if any(
        type(value) is not int or value < 0
        for value in values
    ):
        raise ValueError(
            "配點必須是非負整數。"
        )

    if sum(general.values()) > POOL_POINTS:
        raise ValueError(
            "一般配點池最多使用 500 點。"
        )

    if sum(talent.values()) > POOL_POINTS:
        raise ValueError(
            "藝能配點池最多使用 500 點。"
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
                f"「{name}」超過創角配點上限 150。"
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
    """管理員設定伺服器官方屬性清單。"""

    require_admin(actor)

    general = parse_names(general_raw)
    talent = parse_names(talent_raw)

    if not general or not talent:
        raise ValueError(
            "一般池與藝能池都不能是空的。"
        )

    all_names = general + talent

    if len(all_names) != TOTAL_ATTRIBUTES:
        raise ValueError(
            "兩個配點池必須合計十三項屬性。"
        )

    if len(set(all_names)) != TOTAL_ATTRIBUTES:
        raise ValueError(
            "兩個配點池不得有重複名稱。"
        )

    schema = {
        "general": general,
        "talent": talent,
        "version": 1,
    }

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
                "已有角色配點，不能直接變更"
                "官方清單，必須使用版本遷移。"
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
            VALUES (?, 'attributes', ?, 'official_schema', ?)
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
    """建立或更新草稿配點，正式角色不可重配。"""

    with transaction() as conn:
        row = conn.execute(
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
            row is None
            or row["owner_id"] != actor.user_id
            or row["character_type"] != "player"
        ):
            raise PermissionDenied(
                "只能替自己的玩家角色配點。"
            )

        if row["status"] != "draft":
            raise InvalidState(
                "只有草稿可以調整初始配點。"
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
            row["settings_json"] or "{}"
        )

        settings["main_track"] = main
        settings["secondary_track"] = secondary
        settings["attribute_schema_version"] = (
            schema["version"]
        )
        settings["initial_allocation"] = {
            "general": general,
            "talent": talent,
        }

        # 草稿允許重新調整。
        # 先清除本角色舊的初始配點，再寫入完整結果。
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
        description="查看官方十三項屬性及配點池",
    )
    async def show_schema(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            with get_connection() as conn:
                schema = load_schema(
                    conn,
                    actor.guild_id,
                )

            message = (
                "📊 **官方初始屬性**\n\n"
                "一般池（500 點）：\n"
                + "、".join(schema["general"])
                + "\n\n藝能池（500 點）：\n"
                + "、".join(schema["talent"])
                + "\n\n創角配點上限：150。\n"
                "主副賽道額外加成可突破上限。"
            )

            await interaction.response.send_message(
                message,
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @app_commands.command(
        name="設定官方屬性",
        description="管理員設定十三項屬性及配點池",
    )
    @app_commands.describe(
        general="一般池屬性名稱，用逗號分隔",
        talent="藝能池屬性名稱，用逗號分隔",
    )
    async def set_schema(
        self,
        interaction: discord.Interaction,
        general: str,
        talent: str,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            schema = save_official_schema(
                actor,
                general,
                talent,
            )

            message = (
                "✅ 官方屬性設定成功！\n"
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
        description="設定角色主副賽道及初始配點",
    )
    @app_commands.describe(
        character_id="自己的角色編號",
        main="主賽道對應屬性名稱",
        secondary="副賽道對應屬性名稱",
        general="一般池依官方順序輸入點數",
        talent="藝能池依官方順序輸入點數",
    )
    async def initial(
        self,
        interaction: discord.Interaction,
        character_id: int,
        main: str,
        secondary: str,
        general: str,
        talent: str,
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            actor = Actor.from_interaction(
                interaction
            )

            result = allocate_initial(
                actor,
                character_id,
                main.strip(),
                secondary.strip(),
                general,
                talent,
            )

            lines = [
                f"{name}：{value}"
                for name, value
                in result["values"].items()
            ]

            message = (
                f"✅ 角色 #{character_id} "
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
        description="查看角色目前的屬性數值",
    )
    @app_commands.describe(
        character_id="角色編號",
    )
    async def show_attributes(
        self,
        interaction: discord.Interaction,
        character_id: int,
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
                        character_id,
                    ),
                ).fetchone()

                if (
                    character is None
                    or (
                        character["owner_id"]
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
                        character_id,
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
