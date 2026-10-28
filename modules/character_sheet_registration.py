"""角色單連結登記：玩家 NPC / 官方 NPC / 已核准原創角色關聯。

新增 /角色單登記，日後可由 /創角 按鈕呼叫 register_sheet()。
NPC 登記免第二輪審核，前提是來源 Discord 訊息來自本人或群主。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from urllib.parse import urlsplit

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)
LINK_RE = re.compile(r"/channels/(\d+)/(\d+)/(\d+)/?")
VALID_TYPES = {"player": "原創角色", "player_npc": "玩家 NPC", "official_npc": "官方 NPC"}


def initialize():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS character_sheet_links (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            message_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            sheet_url TEXT NOT NULL,
            registered_by TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, character_id),
            UNIQUE (guild_id, message_id),
            FOREIGN KEY (guild_id, character_id) REFERENCES characters(guild_id,id)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_character_sheet_links_channel ON character_sheet_links(guild_id,channel_id)")


def parse_link(guild_id: int, raw: str):
    url = urlsplit(raw.strip())
    if (url.scheme != "https" or url.hostname not in {"discord.com", "www.discord.com", "discordapp.com"}
            or url.username or url.password or url.query or url.fragment):
        raise ValueError("請提供有效的 Discord 訊息連結。")
    match = LINK_RE.fullmatch(url.path)
    if not match or int(match.group(1)) != guild_id:
        raise ValueError("角色單連結必須是本伺服器內的訊息。")
    return int(match.group(2)), int(match.group(3))


async def validate_sheet(interaction: discord.Interaction, url: str):
    channel_id, message_id = parse_link(interaction.guild_id, url)
    try:
        channel = interaction.guild.get_channel_or_thread(channel_id)
        if channel is None:
            channel = await interaction.guild.fetch_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise ValueError("角色單必須是文字頻道或論壇貼文中的訊息。")
        if channel.guild.id != interaction.guild_id:
            raise ValueError("不能使用其他伺服器的訊息。")
        message = await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden) as exc:
        raise ValueError("無法讀取角色單訊息，請確認連結及機器人權限。") from exc
    if message.author.id not in {interaction.user.id, interaction.guild.owner_id}:
        raise PermissionError("角色單連結必須由本人或群主發出。")
    return channel, message


def register_sheet(guild_id: int, user_id: int, owner_id: int, kind: str, name: str,
                   channel_id: int, message_id: int, url: str, character_id: int | None = None):
    """交易內執行登記、數量約束及連結唯一性。原創角色只綁定已通過二審的現有資料。"""
    if kind not in VALID_TYPES:
        raise ValueError("未知角色類型。")
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise ValueError("角色名需為 1～100 字。")
    is_owner = user_id == owner_id
    if kind == "official_npc" and not is_owner:
        raise PermissionError("只有群主可以登記官方 NPC。")
    if kind == "player" and not character_id:
        raise ValueError("原創角色必須填寫已通過二審的角色編號，不能重複創角。")
    if kind != "player" and character_id:
        raise ValueError("NPC 建立請不要填寫原創角色編號。")

    with transaction() as db:
        db.execute("INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)", (str(guild_id),))
        linked = db.execute("SELECT character_id FROM character_sheet_links WHERE guild_id=? AND message_id=?",
                            (str(guild_id), str(message_id))).fetchone()
        if linked and (character_id is None or int(linked["character_id"]) != character_id):
            raise ValueError("這份角色單已登記過其他角色，不能重複使用。")

        if kind == "player":
            row = db.execute("""SELECT id, owner_id, character_type, status FROM characters
                WHERE guild_id=? AND id=?""", (str(guild_id), character_id)).fetchone()
            if row is None or row["character_type"] != "player" or row["status"] not in ("approved", "active"):
                raise ValueError("必須是已通過二審或已啟用的原創角色。")
            if not is_owner and row["owner_id"] != str(user_id):
                raise PermissionError("只能登記自己的原創角色。")
        else:
            if kind == "player_npc" and not is_owner:
                other = db.execute("""SELECT id FROM characters WHERE guild_id=? AND owner_id=?
                    AND character_type='player_npc' AND status!='deleted' LIMIT 1""",
                    (str(guild_id), str(user_id))).fetchone()
                if other:
                    raise ValueError("每名玩家最多一隻玩家 NPC；修改請沿用原登記紀錄。")
            cursor = db.execute("""INSERT INTO characters
                (guild_id,owner_id,name,character_type,status)
                VALUES (?,?,?,?, 'active')""",
                (str(guild_id), str(user_id), name, kind))
            character_id = cursor.lastrowid

        db.execute("""INSERT INTO character_sheet_links
            (guild_id,character_id,message_id,channel_id,sheet_url,registered_by)
            VALUES (?,?,?,?,?,?) ON CONFLICT(guild_id,character_id) DO UPDATE SET
              message_id=excluded.message_id, channel_id=excluded.channel_id,
              sheet_url=excluded.sheet_url, registered_by=excluded.registered_by,
              updated_at=CURRENT_TIMESTAMP""",
            (str(guild_id), character_id, str(message_id), str(channel_id), url.strip(), str(user_id)))
        return character_id


class ModifyNPCModal(discord.ui.Modal, title="修改玩家 NPC 角色單"):
    new_link = discord.ui.TextInput(label="新角色單訊息連結", max_length=250)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("只能在伺服器使用。", ephemeral=True)
            return
        try:
            channel, message = await validate_sheet(interaction, str(self.new_link))
            with transaction() as db:
                row = db.execute("""SELECT id FROM characters WHERE guild_id=? AND owner_id=?
                    AND character_type='player_npc' AND status!='deleted' ORDER BY id LIMIT 1""",
                    (str(interaction.guild_id), str(interaction.user.id))).fetchone()
                if row is None:
                    raise ValueError("目前沒有可修改的玩家 NPC。")
                conflict = db.execute("""SELECT character_id FROM character_sheet_links
                    WHERE guild_id=? AND message_id=?""",
                    (str(interaction.guild_id), str(message.id))).fetchone()
                if conflict and int(conflict["character_id"]) != row["id"]:
                    raise ValueError("此連結已登記其他角色。")
                db.execute("""INSERT INTO character_sheet_links
                    (guild_id,character_id,message_id,channel_id,sheet_url,registered_by)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(guild_id,character_id) DO UPDATE SET
                    message_id=excluded.message_id,channel_id=excluded.channel_id,
                    sheet_url=excluded.sheet_url,registered_by=excluded.registered_by,
                    updated_at=CURRENT_TIMESTAMP""",
                    (str(interaction.guild_id), row["id"], str(message.id), str(channel.id),
                     str(self.new_link.value), str(interaction.user.id)))
            await interaction.response.send_message(f"✅ NPC #{row['id']} 的角色單連結已更新。", ephemeral=True)
        except (ValueError, PermissionError, sqlite3.Error, discord.HTTPException) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)


class CharacterSheetRegistration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        initialize()

    @app_commands.command(name="角色單登記", description="登記已審核的原創角色、玩家 NPC 或官方 NPC 角色單")
    @app_commands.guild_only()
    @app_commands.describe(類型="角色種類", 角色名稱="角色名稱", 角色單連結="由你本人或群主發出的角色單訊息連結", 角色編號="原創角色需填入已通過二審的角色編號")
    @app_commands.choices(類型=[
        app_commands.Choice(name="原創角色（綁定已審核角色）", value="player"),
        app_commands.Choice(name="玩家 NPC（每人一隻）", value="player_npc"),
        app_commands.Choice(name="官方 NPC（僅群主）", value="official_npc"),
    ])
    async def register(self, interaction: discord.Interaction, 類型: app_commands.Choice[str],
                       角色名稱: str, 角色單連結: str, 角色編號: int | None = None):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel, message = await validate_sheet(interaction, 角色單連結)
            new_id = register_sheet(interaction.guild_id, interaction.user.id,
                                    interaction.guild.owner_id, 類型.value, 角色名稱,
                                    channel.id, message.id, 角色單連結, 角色編號)
            await interaction.followup.send(
                f"✅ {VALID_TYPES[類型.value]}登記成功！\n角色編號：`{new_id}`\n"
                f"角色單：{message.jump_url}\n"
                "已登記資料不需要再次審核。", ephemeral=True)
        except (ValueError, PermissionError, sqlite3.Error, discord.HTTPException) as exc:
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)

    @app_commands.command(name="玩家NPC修改", description="修改本人玩家 NPC 的角色單連結")
    @app_commands.guild_only()
    async def modify(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ModifyNPCModal())


async def setup(bot: commands.Bot):
    await bot.add_cog(CharacterSheetRegistration(bot))
