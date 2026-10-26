
"""Discord RP 銀行快捷指令。

正式指令：
/存錢
/轉帳

原本保留：
/銀行
/提款
/定存
/定存解約

直接呼叫原有銀行核心，
不重複建立帳戶或自行修改餘額。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from core.economy import EconomyError
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)
from modules.economy import (
    bank_move,
    free_transfer,
    target_character,
)

LOG = logging.getLogger(__name__)


def table_exists(conn, name):
    return conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (name,),
    ).fetchone() is not None


def get_selected_character(conn, actor):
    """讀取角色切換系統的目前角色。

    若使用者尚未有目前角色紀錄，
    不會替多角色玩家擅自選擇付款人。
    """

    if not table_exists(
        conn,
        "player_current_characters",
    ):
        return None

    columns = {
        row["name"]
        for row in conn.execute(
            """
            PRAGMA table_info(
                player_current_characters
            )
            """
        )
    }

    needed = {
        "guild_id",
        "user_id",
        "character_id",
    }

    if not needed.issubset(columns):
        return None

    row = conn.execute(
        """
        SELECT character_id
        FROM player_current_characters
        WHERE guild_id = ?
          AND user_id = ?
        """,
        (
            actor.guild_id,
            actor.user_id,
        ),
    ).fetchone()

    if row is None:
        return None

    return int(row["character_id"])


def get_owned_characters(conn, actor):
    rows = conn.execute(
        """
        SELECT id, name
        FROM characters
        WHERE guild_id = ?
          AND owner_id = ?
          AND status = 'active'
          AND character_type = 'player'
        ORDER BY id
        """,
        (
            actor.guild_id,
            actor.user_id,
        ),
    ).fetchall()

    return [dict(row) for row in rows]


def resolve_character(actor, selected_id=None):
    """只允許使用本人已啟用的原創角色。"""

    with get_connection() as conn:
        characters = get_owned_characters(
            conn,
            actor,
        )

        if not characters:
            raise InvalidState(
                "你還沒有正式啟用的原創角色。"
                "請先使用 /創角 完成建立與審核。"
            )

        if selected_id is None:
            selected_id = get_selected_character(
                conn,
                actor,
            )

        if selected_id is None:
            if len(characters) == 1:
                selected_id = characters[0]["id"]
            else:
                raise InvalidState(
                    "你有多名正式角色，"
                    "請先使用 /角色切換 "
                    "選定要操作的角色。"
                )

        for character in characters:
            if int(character["id"]) == int(selected_id):
                return character

    raise PermissionDenied(
        "目前角色已失效，或不是你的正式原創角色。"
        "請重新使用 /角色切換。"
    )


async def error_reply(interaction, exc):
    if isinstance(exc, sqlite3.Error):
        LOG.exception(
            "銀行快捷指令資料庫錯誤"
        )
        message = (
            "銀行資料庫操作失敗，"
            "請聯絡管理員。"
        )
    else:
        message = str(exc)[:1700]

    if interaction.response.is_done():
        await interaction.followup.send(
            f"⚠️ {message}",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"⚠️ {message}",
            ephemeral=True,
        )


class BankShortcuts(commands.Cog):
    """角色銀行快捷操作。"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="存錢",
        description="將目前角色的現金存入銀行",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        金額="欲存入的金幣數量",
    )
    async def deposit(
        self,
        interaction: discord.Interaction,
        金額: app_commands.Range[
            int, 1, 1000000000000000
        ],
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            character = await asyncio.to_thread(
                resolve_character,
                actor,
            )

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            result = await asyncio.to_thread(
                bank_move,
                actor,
                character["id"],
                金額,
                "deposit",
            )

            await interaction.followup.send(
                (
                    "✅ **存款成功**\n\n"
                    f"角色：{character['name']}\n"
                    f"角色編號：#{character['id']}\n"
                    f"存入金額：{金額:,} 金幣\n"
                    f"銀行餘額："
                    f"{result.receiver_balance:,} 金幣"
                ),
                ephemeral=True,
            )

        except (
            ValueError,
            EconomyError,
            PermissionDenied,
            InvalidState,
            sqlite3.Error,
        ) as exc:
            await error_reply(interaction, exc)

    @app_commands.command(
        name="轉帳",
        description="從目前角色轉帳給其他正式角色",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        收款角色編號="收款方的角色編號",
        金額="欲轉出的金幣數量",
    )
    async def transfer_money(
        self,
        interaction: discord.Interaction,
        收款角色編號: int,
        金額: app_commands.Range[
            int, 1, 1000000000000000
        ],
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            sender = await asyncio.to_thread(
                resolve_character,
                actor,
            )

            receiver = await asyncio.to_thread(
                target_character,
                actor.guild_id,
                收款角色編號,
            )

            if int(sender["id"]) == 收款角色編號:
                raise ValueError(
                    "不能轉帳給同一名角色。"
                )

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            result = await asyncio.to_thread(
                free_transfer,
                actor,
                sender["id"],
                收款角色編號,
                金額,
            )

            await interaction.followup.send(
                (
                    "✅ **轉帳成功**\n\n"
                    f"付款角色：{sender['name']}"
                    f"（#{sender['id']}）\n"
                    f"收款角色：{receiver['name']}"
                    f"（#{收款角色編號}）\n"
                    f"轉帳金額：{金額:,} 金幣\n"
                    "轉帳手續費：0 金幣\n"
                    f"交易編號：{result.transaction_id}"
                ),
                ephemeral=True,
            )

        except (
            ValueError,
            EconomyError,
            PermissionDenied,
            InvalidState,
            sqlite3.Error,
        ) as exc:
            await error_reply(interaction, exc)


async def setup(bot: commands.Bot):
    await bot.add_cog(
        BankShortcuts(bot)
    )
