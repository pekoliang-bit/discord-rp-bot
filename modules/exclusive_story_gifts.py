"""劇情專屬物品｜綁定獲獎角色，僅該角色能使用。

依賴 modules.story_episodes 的 rp_episode_gifts 資料表。
指令：/專屬物品、/使用專屬物品
專屬物品是 RP 收藏道具，不會被一般商城轉贈、交易或消耗。
"""
from __future__ import annotations

import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)


def initialize() -> None:
    """僅新增使用記錄，不修改先前的劇情獎勵資料。"""
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_exclusive_gift_uses (
                guild_id TEXT NOT NULL,
                interaction_id TEXT NOT NULL,
                gift_rowid INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                player_id TEXT NOT NULL,
                used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id, interaction_id)
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_rp_exclusive_gift_uses
            ON rp_exclusive_gift_uses(guild_id, character_id, gift_rowid)
        """)


def require_owned_character(db, guild_id: int, user_id: int, character_id: int) -> None:
    if character_id <= 0:
        raise ValueError("角色編號不正確。")
    row = db.execute(
        """SELECT id FROM characters
           WHERE guild_id=? AND id=? AND owner_id=?
             AND character_type='player'
             AND status IN ('approved', 'active')""",
        (str(guild_id), character_id, str(user_id)),
    ).fetchone()
    if not row:
        raise ValueError("只有該角色的擁有者能替已啟用的角色使用專屬物品。")


def get_gifts(guild_id: int, user_id: int, character_id: int) -> list[dict]:
    with get_connection() as db:
        require_owned_character(db, guild_id, user_id, character_id)
        rows = db.execute(
            """SELECT rowid AS gift_id, gift_name, quantity,
                      story_id, episode_no
               FROM rp_episode_gifts
               WHERE guild_id=? AND player_id=? AND character_id=?
               ORDER BY story_id, episode_no, rowid""",
            (str(guild_id), str(user_id), character_id),
        ).fetchall()
        return [dict(row) for row in rows]


def record_use(guild_id: int, user_id: int, character_id: int,
               gift_id: int, interaction_id: int) -> str:
    """在同一筆交易中檢查擁有權及寫入使用紀錄。"""
    if gift_id <= 0:
        raise ValueError("物品編號不正確。")

    with transaction() as db:
        require_owned_character(db, guild_id, user_id, character_id)
        gift = db.execute(
            """SELECT gift_name, quantity FROM rp_episode_gifts
               WHERE rowid=? AND guild_id=? AND player_id=? AND character_id=?""",
            (gift_id, str(guild_id), str(user_id), character_id),
        ).fetchone()
        if gift is None or int(gift["quantity"]) <= 0:
            raise ValueError("該角色沒有這件專屬物品，不能使用。")

        existing = db.execute(
            """SELECT 1 FROM rp_exclusive_gift_uses
               WHERE guild_id=? AND interaction_id=?""",
            (str(guild_id), str(interaction_id)),
        ).fetchone()
        if existing:
            return "本次使用已記錄，沒有重複執行。"

        db.execute(
            """INSERT INTO rp_exclusive_gift_uses
                (guild_id,interaction_id,gift_rowid,character_id,player_id)
                VALUES (?,?,?,?,?)""",
            (str(guild_id), str(interaction_id), gift_id,
             character_id, str(user_id)),
        )
        return str(gift["gift_name"])


class ExclusiveStoryGifts(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        from modules.story_episodes import init_db
        init_db()
        initialize()

    @app_commands.command(name="專屬物品", description="查看自己指定角色取得的劇情專屬禮物")
    @app_commands.guild_only()
    async def gifts(self, interaction: discord.Interaction, 角色編號: int):
        try:
            gifts = get_gifts(interaction.guild_id, interaction.user.id, 角色編號)
            if not gifts:
                content = f"🎁 角色 #{角色編號} 尚未取得劇情專屬物品。"
            else:
                lines = [f"🎁 **角色 #{角色編號} 的劇情專屬物品**", ""]
                for gift in gifts[:35]:
                    safe_name = discord.utils.escape_mentions(
                        discord.utils.escape_markdown(gift["gift_name"])
                    )
                    lines.append(
                        f"`#{gift['gift_id']}`｜{safe_name} ×{gift['quantity']}"
                        f"（劇情 #{gift['story_id']}／第 {gift['episode_no']} 回）"
                    )
                if len(gifts) > 35:
                    lines.append(f"另有 {len(gifts)-35} 筆，尚未顯示。")
                lines.append("\n專屬物品只能由這隻角色使用，不能轉贈其他角色。")
                content = "\n".join(lines)[:1900]
            await interaction.response.send_message(content, ephemeral=True)
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)

    @app_commands.command(name="使用專屬物品", description="讓獲得劇情專屬物品的角色使用該物品")
    @app_commands.guild_only()
    async def use_gift(self, interaction: discord.Interaction,
                       角色編號: int, 物品編號: int):
        try:
            name = record_use(interaction.guild_id, interaction.user.id,
                              角色編號, 物品編號, interaction.id)
            safe_name = discord.utils.escape_mentions(discord.utils.escape_markdown(name))
            await interaction.response.send_message(
                f"🎁 角色 #{角色編號} 使用了專屬物品「{safe_name}」。\n"
                "這是 RP 道具使用紀錄；不會扣除物品或自動增加屬性。",
                ephemeral=True,
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ExclusiveStoryGifts(bot))
    LOG.info("劇情專屬物品系統已載入")
