
"""Discord RP：正式角色選擇與自動辨識系統。

功能：
1. 每個伺服器、每位玩家獨立記錄目前角色
2. 只有一名正式角色時自動選用
3. 多名角色時記住上次選擇
4. 提供 /角色切換 互動選單
5. 角色退場、刪除、換主人後自動失效
6. 其他模組可呼叫 get_current_character()
7. 不把創角草稿誤設為正式使用角色

不修改銀行、商城、角色屬性及既有角色資料。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)

LOG = logging.getLogger(__name__)

ACTIVE_STATUS = "active"
PAGE_SIZE = 25


def init_character_context_db():
    """建立目前角色紀錄表，保留既有選擇。"""

    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            player_current_characters (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (guild_id, character_id)
                    REFERENCES characters(guild_id, id)
            )
            """
        )


def list_available_characters(
    guild_id,
    user_id,
):
    """取得玩家可正式使用的角色。"""

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, age, occupation,
                   character_type, status
            FROM characters
            WHERE guild_id = ?
              AND owner_id = ?
              AND status = 'active'
            ORDER BY id ASC
            """,
            (
                str(guild_id),
                str(user_id),
            ),
        ).fetchall()

        return [dict(row) for row in rows]


def get_current_character(
    guild_id,
    user_id,
):
    """回傳目前正式角色資料。

    沒有角色時回傳 None。
    有多名角色但尚未選擇時，
    拋出 InvalidState。

    各功能應傳入真正的互動操作者 ID，
    不可相信玩家自行提供的 user_id。
    """

    guild_id = str(guild_id)
    user_id = str(user_id)

    with transaction() as conn:
        characters = conn.execute(
            """
            SELECT *
            FROM characters
            WHERE guild_id = ?
              AND owner_id = ?
              AND status = 'active'
            ORDER BY id ASC
            """,
            (guild_id, user_id),
        ).fetchall()

        selected = conn.execute(
            """
            SELECT character_id
            FROM player_current_characters
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (guild_id, user_id),
        ).fetchone()

        if not characters:
            conn.execute(
                """
                DELETE FROM player_current_characters
                WHERE guild_id = ?
                  AND user_id = ?
                """,
                (guild_id, user_id),
            )
            return None

        if selected is not None:
            for character in characters:
                if character["id"] == selected["character_id"]:
                    return dict(character)

            # 角色退場、刪除或移轉所有權後，
            # 不再沿用舊的角色選擇。
            conn.execute(
                """
                DELETE FROM player_current_characters
                WHERE guild_id = ?
                  AND user_id = ?
                """,
                (guild_id, user_id),
            )

        if len(characters) == 1:
            character = characters[0]

            conn.execute(
                """
                INSERT INTO player_current_characters (
                    guild_id, user_id, character_id
                )
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET
                    character_id = excluded.character_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    guild_id,
                    user_id,
                    character["id"],
                ),
            )

            return dict(character)

        raise InvalidState(
            "你有多名正式角色，"
            "請先使用 /角色切換 選擇目前角色。"
        )


def get_current_character_for_actor(actor: Actor):
    """給其他模組使用的角色辨識入口。"""

    return get_current_character(
        actor.guild_id,
        actor.user_id,
    )


def require_current_character(actor: Actor):
    """要求玩家已有正式使用中的角色。"""

    character = get_current_character_for_actor(actor)

    if character is None:
        raise InvalidState(
            "你目前沒有正式啟用的角色。"
            "請先完成 /創角 與角色審核。"
        )

    return character


def set_current_character(
    guild_id,
    user_id,
    character_id,
):
    """選擇角色，必須再次確認所有權與啟用狀態。"""

    guild_id = str(guild_id)
    user_id = str(user_id)

    if type(character_id) is not int or character_id <= 0:
        raise ValueError("角色編號不正確。")

    with transaction() as conn:
        character = conn.execute(
            """
            SELECT *
            FROM characters
            WHERE guild_id = ?
              AND id = ?
              AND owner_id = ?
              AND status = 'active'
            """,
            (
                guild_id,
                character_id,
                user_id,
            ),
        ).fetchone()

        if character is None:
            raise PermissionDenied(
                "無法切換：角色不存在、尚未啟用，"
                "或不屬於你。"
            )

        conn.execute(
            """
            INSERT INTO player_current_characters (
                guild_id,
                user_id,
                character_id
            )
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET
                character_id = excluded.character_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                guild_id,
                user_id,
                character_id,
            ),
        )

        return dict(character)


class CharacterSelect(discord.ui.Select):
    def __init__(self, actor, characters):
        self.actor = actor

        options = []

        for character in characters:
            options.append(
                discord.SelectOption(
                    label=str(character["name"])[:100],
                    value=str(character["id"]),
                    description=(
                        f"角色 #{character['id']}｜"
                        f"{character['occupation'] or '未設定職業'}"
                    )[:100],
                )
            )

        super().__init__(
            placeholder="選擇目前使用角色",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        if (
            str(interaction.user.id) != self.actor.user_id
            or str(interaction.guild_id) != self.actor.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的角色選單。",
                ephemeral=True,
            )
            return

        try:
            character = await asyncio.to_thread(
                set_current_character,
                self.actor.guild_id,
                self.actor.user_id,
                int(self.values[0]),
            )

            await interaction.response.edit_message(
                content=(
                    "✅ **角色切換成功**\n\n"
                    f"目前角色：{character['name']}\n"
                    f"角色編號：#{character['id']}\n"
                    f"職業：{character['occupation']}\n\n"
                    "之後使用銀行、商城及玩家申請時，"
                    "系統可自動辨識這名角色。"
                ),
                view=None,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

        except sqlite3.Error:
            LOG.exception("角色切換資料庫操作失敗")
            await interaction.response.send_message(
                "⚠️ 角色切換失敗，請聯絡管理員。",
                ephemeral=True,
            )


class CharacterSelectView(discord.ui.View):
    def __init__(self, actor, characters):
        super().__init__(timeout=300)
        self.actor = actor

        self.add_item(
            CharacterSelect(
                actor,
                characters,
            )
        )


class CharacterSwitch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            init_character_context_db
        )

    @app_commands.command(
        name="角色切換",
        description="選擇目前使用中的正式角色",
    )
    @app_commands.guild_only()
    async def switch(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = Actor.from_interaction(interaction)

            characters = await asyncio.to_thread(
                list_available_characters,
                actor.guild_id,
                actor.user_id,
            )

            if not characters:
                await interaction.response.send_message(
                    "你目前沒有已啟用的角色。\n"
                    "請先完成創角及審核。",
                    ephemeral=True,
                )
                return

            if len(characters) == 1:
                character = await asyncio.to_thread(
                    set_current_character,
                    actor.guild_id,
                    actor.user_id,
                    characters[0]["id"],
                )

                await interaction.response.send_message(
                    "✅ 已自動選擇唯一的正式角色："
                    f"**{character['name']}**",
                    ephemeral=True,
                )
                return

            current_name = "尚未選擇"

            try:
                current = await asyncio.to_thread(
                    get_current_character_for_actor,
                    actor,
                )

                if current is not None:
                    current_name = current["name"]

            except InvalidState:
                pass

            # Discord 每頁最多25個選項。
            # 角色數量不限，使用上一頁／下一頁瀏覽。
            view = CharacterPagedView(
                actor,
                characters,
            )

            await interaction.response.send_message(
                f"🎭 **角色切換**\n"
                f"目前角色：{current_name}\n"
                "請從下方選擇角色。",
                view=view,
                ephemeral=True,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

        except sqlite3.Error:
            LOG.exception("查詢可用角色失敗")
            await interaction.response.send_message(
                "⚠️ 讀取角色資料失敗。",
                ephemeral=True,
            )


class CharacterPagedView(discord.ui.View):
    def __init__(
        self,
        actor,
        characters,
        page=0,
    ):
        super().__init__(timeout=300)

        self.actor = actor
        self.characters = characters
        self.page = page

        maximum = max(
            0,
            (len(characters) - 1) // PAGE_SIZE,
        )

        self.maximum = maximum

        start = page * PAGE_SIZE
        end = start + PAGE_SIZE

        self.add_item(
            CharacterSelect(
                actor,
                characters[start:end],
            )
        )

        self.previous.disabled = page <= 0
        self.next.disabled = page >= maximum

    async def interaction_check(self, interaction):
        if (
            str(interaction.user.id) != self.actor.user_id
            or str(interaction.guild_id) != self.actor.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的角色選單。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous(self, interaction, button):
        await interaction.response.edit_message(
            content=(
                "🎭 **角色切換**\n"
                f"第 {self.page} 頁"
            ),
            view=CharacterPagedView(
                self.actor,
                self.characters,
                self.page - 1,
            ),
        )

    @discord.ui.button(
        label="➡️ 下一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next(self, interaction, button):
        await interaction.response.edit_message(
            content=(
                "🎭 **角色切換**\n"
                f"第 {self.page + 2} 頁"
            ),
            view=CharacterPagedView(
                self.actor,
                self.characters,
                self.page + 1,
            ),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        CharacterSwitch(bot)
    )
