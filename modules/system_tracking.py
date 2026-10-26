
"""Discord RP：角色系統動態追蹤數值。

追蹤：
1. 死忠粉絲數
2. 活躍黑粉數
3. 大眾認知度
4. CP 好感度（由關係系統另行管理）

與十三項創角配點完全獨立。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import (
    get_connection,
    transaction,
)
from core.permissions import Actor, PermissionDenied

LOG = logging.getLogger(__name__)

TRACKING_FIELDS = {
    "loyal_fans": "死忠粉絲數",
    "active_antis": "活躍黑粉數",
    "public_awareness": "大眾認知度",
}


def init_tracking_db():
    """建立獨立的系統追蹤資料表。"""

    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            character_system_tracking (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                loyal_fans INTEGER NOT NULL DEFAULT 0
                    CHECK(loyal_fans >= 0),
                active_antis INTEGER NOT NULL DEFAULT 0
                    CHECK(active_antis >= 0),
                public_awareness INTEGER NOT NULL DEFAULT 0
                    CHECK(public_awareness >= 0),
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    guild_id,
                    character_id
                )
            )
            """
        )


def ensure_tracking(
    conn,
    guild_id,
    character_id,
):
    """確保角色有追蹤紀錄，不重設既有資料。"""

    guild_id = str(guild_id)
    character_id = int(character_id)

    character = conn.execute(
        """
        SELECT id
        FROM characters
        WHERE guild_id = ?
          AND id = ?
          AND status != 'deleted'
        """,
        (guild_id, character_id),
    ).fetchone()

    if character is None:
        raise ValueError("角色不存在。")

    conn.execute(
        """
        INSERT OR IGNORE INTO character_system_tracking (
            guild_id,
            character_id
        )
        VALUES (?, ?)
        """,
        (guild_id, character_id),
    )

    return conn.execute(
        """
        SELECT *
        FROM character_system_tracking
        WHERE guild_id = ?
          AND character_id = ?
        """,
        (guild_id, character_id),
    ).fetchone()


def get_tracking(guild_id, character_id):
    """取得角色追蹤數值，供角色面板使用。"""

    with transaction() as conn:
        row = ensure_tracking(
            conn,
            guild_id,
            character_id,
        )
        return dict(row)


def change_tracking(
    guild_id,
    character_id,
    field,
    amount,
):
    """供經過授權驗證的系統結算程式呼叫。

    注意：本函式本身不驗證玩家操作權限，
    不可直接接到玩家可以任意填數字的指令。
    """

    if field not in TRACKING_FIELDS:
        raise ValueError("未知的追蹤項目。")

    if type(amount) is not int:
        raise ValueError("變動值必須是整數。")

    if abs(amount) > 1_000_000_000:
        raise ValueError("單次變動值過大。")

    with transaction() as conn:
        ensure_tracking(
            conn,
            guild_id,
            character_id,
        )

        # 欄位名稱只能來自固定白名單。
        conn.execute(
            f"""
            UPDATE character_system_tracking
            SET {field} = MAX(
                    0,
                    {field} + ?
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                amount,
                str(guild_id),
                int(character_id),
            ),
        )

        row = conn.execute(
            """
            SELECT *
            FROM character_system_tracking
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                str(guild_id),
                int(character_id),
            ),
        ).fetchone()

        return dict(row)


def format_tracking(row, character_name):
    return (
        f"📊 **{character_name}｜系統追蹤**\n\n"
        f"❤️ 死忠粉絲數：{row['loyal_fans']:,}\n"
        f"🌪️ 活躍黑粉數：{row['active_antis']:,}\n"
        f"🌍 大眾認知度：{row['public_awareness']:,}\n"
        "💕 CP 好感度：無\n\n"
        "以上數值由系統追蹤，"
        "不消耗創角配點。\n"
        "CP 好感度由人物關係系統管理。"
    )


def owned_characters(actor):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, status
            FROM characters
            WHERE guild_id = ?
              AND owner_id = ?
              AND status != 'deleted'
            ORDER BY id DESC
            """,
            (
                actor.guild_id,
                actor.user_id,
            ),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


class TrackingCharacterSelect(discord.ui.Select):
    def __init__(self, actor, characters):
        self.actor = actor

        options = [
            discord.SelectOption(
                label=str(c["name"] or "未命名角色")[:80],
                description=(
                    f"角色 #{c['id']}｜{c['status']}"
                )[:100],
                value=str(c["id"]),
            )
            for c in characters[:25]
        ]

        super().__init__(
            placeholder="選擇要查看的角色",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        if (
            str(interaction.user.id)
            != self.actor.user_id
            or str(interaction.guild_id)
            != self.actor.guild_id
        ):
            await interaction.response.send_message(
                "這不是你的角色選單。",
                ephemeral=True,
            )
            return

        character_id = int(self.values[0])

        # 使用當下再次驗證所有權。
        characters = owned_characters(
            self.actor
        )

        character = next(
            (
                c for c in characters
                if c["id"] == character_id
            ),
            None,
        )

        if character is None:
            await interaction.response.send_message(
                "角色不存在或已無權查看。",
                ephemeral=True,
            )
            return

        try:
            data = get_tracking(
                self.actor.guild_id,
                character_id,
            )

            await interaction.response.edit_message(
                content=format_tracking(
                    data,
                    character["name"],
                ),
                view=None,
            )

        except (ValueError, sqlite3.Error):
            LOG.exception("讀取角色追蹤資料失敗")
            await interaction.response.send_message(
                "讀取資料失敗，請聯絡管理員。",
                ephemeral=True,
            )


class TrackingCharacterView(discord.ui.View):
    def __init__(self, actor, characters):
        super().__init__(timeout=300)
        self.add_item(
            TrackingCharacterSelect(
                actor,
                characters,
            )
        )


class SystemTracking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            init_tracking_db
        )

    @app_commands.command(
        name="追蹤數值",
        description="查看自己角色的系統追蹤數值",
    )
    @app_commands.guild_only()
    async def show_tracking(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            characters = owned_characters(actor)

            if not characters:
                await interaction.response.send_message(
                    "你目前沒有可查看的角色。",
                    ephemeral=True,
                )
                return

            if len(characters) == 1:
                character = characters[0]

                data = get_tracking(
                    actor.guild_id,
                    character["id"],
                )

                await interaction.response.send_message(
                    format_tracking(
                        data,
                        character["name"],
                    ),
                    ephemeral=True,
                )
                return

            message = "請選擇要查看的角色。"

            if len(characters) > 25:
                message += (
                    "\n目前先顯示最近25名角色。"
                )

            await interaction.response.send_message(
                message,
                view=TrackingCharacterView(
                    actor,
                    characters,
                ),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            ValueError,
            sqlite3.Error,
        ):
            LOG.exception("系統追蹤查詢失敗")

            message = (
                "無法讀取系統追蹤數值，"
                "請聯絡管理員。"
            )

            if interaction.response.is_done():
                await interaction.followup.send(
                    message,
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    message,
                    ephemeral=True,
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        SystemTracking(bot)
    )
