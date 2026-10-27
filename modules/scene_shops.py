
"""Discord RP｜場景商店登記與頻道管理。

供 /官方 管理功能中心載入 SceneShopButton。
不註冊新的斜線指令，不影響 B01～B10 官方商店。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from urllib.parse import urlsplit

import discord
from discord.ext import commands

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)

KINDS = (
    "餐廳", "診所", "酒店", "美容院",
    "咖啡廳", "酒吧", "旅館", "商店", "其他"
)


# ==========================================
# 權限
# ==========================================

def is_owner(interaction):
    return (
        interaction.guild is not None
        and interaction.guild.owner_id
        == interaction.user.id
    )


def require_owner(interaction):
    if not is_owner(interaction):
        raise PermissionError(
            "只有伺服器群主可以管理場景商店。"
        )


# ==========================================
# 資料庫
# ==========================================

def init_db():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scene_shops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                channel_id TEXT NOT NULL,
                channel_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','closed')),
                created_by TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, channel_id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_scene_shops_guild
            ON scene_shops(guild_id, status, id)
        """)


# ==========================================
# Discord 連結
# ==========================================

def parse_channel_link(guild_id, raw):
    text = str(raw or "").strip()

    if text.isdecimal():
        return int(text)

    p = urlsplit(text)

    if (
        p.scheme != "https"
        or p.hostname not in (
            "discord.com",
            "www.discord.com",
            "discordapp.com",
        )
        or p.username
        or p.password
        or p.query
        or p.fragment
    ):
        raise ValueError(
            "請填寫頻道 ID 或 Discord 頻道連結。"
        )

    match = re.fullmatch(
        r"/channels/(\d+)/(\d+)(?:/\d+)?/?",
        p.path,
    )

    if (
        not match
        or match.group(1) != str(guild_id)
    ):
        raise ValueError(
            "連結必須屬於目前的伺服器。"
        )

    return int(match.group(2))


async def resolve_channel(interaction, raw):
    channel_id = parse_channel_link(
        interaction.guild_id,
        raw,
    )

    channel = interaction.guild.get_channel_or_thread(
        channel_id
    )

    if channel is None:
        channel = await interaction.guild.fetch_channel(
            channel_id
        )

    if not isinstance(
        channel,
        (discord.TextChannel, discord.Thread),
    ):
        raise ValueError(
            "請指定文字頻道或論壇貼文討論串。"
        )

    if channel.guild.id != interaction.guild_id:
        raise ValueError(
            "頻道不屬於目前伺服器。"
        )

    member = interaction.guild.me

    if member is None:
        member = await interaction.guild.fetch_member(
            interaction.client.user.id
        )

    if not channel.permissions_for(member).view_channel:
        raise ValueError(
            "機器人沒有查看這個頻道的權限。"
        )

    return channel


# ==========================================
# 建立商店
# ==========================================

def save_shop(
    guild_id,
    user_id,
    name,
    kind,
    description,
    channel_id,
):
    name = name.strip()
    description = description.strip()

    if not 1 <= len(name) <= 80:
        raise ValueError("店名必須介於 1～80 字。")

    if kind not in KINDS:
        raise ValueError("商店類型不合法。")

    if len(description) > 500:
        raise ValueError("簡介不得超過 500 字。")

    url = (
        f"https://discord.com/channels/"
        f"{guild_id}/{channel_id}"
    )

    with transaction() as conn:
        existing = conn.execute(
            """
            SELECT id FROM scene_shops
            WHERE guild_id=? AND channel_id=?
            """,
            (str(guild_id), str(channel_id)),
        ).fetchone()

        if existing:
            raise ValueError(
                f"這個頻道已登記為商店 "
                f"#{existing['id']}。"
            )

        cursor = conn.execute(
            """
            INSERT INTO scene_shops (
                guild_id, name, kind,
                description, channel_id,
                channel_url, created_by, updated_by
            )
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                str(guild_id),
                name,
                kind,
                description,
                str(channel_id),
                url,
                str(user_id),
                str(user_id),
            ),
        )

        return cursor.lastrowid


# ==========================================
# 查詢與修改
# ==========================================

def list_shops(guild_id):
    with get_connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM scene_shops
                WHERE guild_id=?
                ORDER BY id DESC
                LIMIT 25
                """,
                (str(guild_id),),
            ).fetchall()
        ]


def edit_shop(
    guild_id,
    user_id,
    shop_id,
    status=None,
    name=None,
    description=None,
):
    with transaction() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM scene_shops
            WHERE guild_id=? AND id=?
            """,
            (str(guild_id), shop_id),
        ).fetchone()

        if not row:
            raise ValueError("找不到這間場景商店。")

        if status not in (None, "open", "closed"):
            raise ValueError("營業狀態不合法。")

        name = (
            name.strip()
            if name and name.strip()
            else row["name"]
        )

        description = (
            description.strip()
            if description and description.strip()
            else row["description"]
        )

        if (
            not 1 <= len(name) <= 80
            or len(description) > 500
        ):
            raise ValueError("名稱或簡介長度不合法。")

        conn.execute(
            """
            UPDATE scene_shops
            SET name=?,
                description=?,
                status=?,
                updated_by=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND id=?
            """,
            (
                name,
                description,
                status or row["status"],
                str(user_id),
                str(guild_id),
                shop_id,
            ),
        )


# ==========================================
# 建立表單
# ==========================================

class CreateSceneModal(
    discord.ui.Modal,
    title="建立場景商店",
):
    name = discord.ui.TextInput(
        label="商店名稱",
        max_length=80,
    )

    kind = discord.ui.TextInput(
        label="類型：餐廳／診所／酒店／美容院等",
        max_length=10,
    )

    channel = discord.ui.TextInput(
        label="頻道 ID 或 Discord 頻道連結",
        max_length=200,
    )

    description = discord.ui.TextInput(
        label="商店簡介",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    async def on_submit(self, interaction):
        try:
            require_owner(interaction)

            channel = await resolve_channel(
                interaction,
                self.channel.value,
            )

            shop_id = save_shop(
                interaction.guild_id,
                interaction.user.id,
                str(self.name.value),
                str(self.kind.value).strip(),
                str(self.description.value or ""),
                channel.id,
            )

            await interaction.response.send_message(
                f"✅ 已建立場景商店 #{shop_id}\n"
                f"名稱：{discord.utils.escape_markdown(str(self.name.value))}\n"
                f"類型：{self.kind.value}\n"
                f"頻道：{channel.mention}\n"
                "狀態：營業中\n\n"
                "目前僅登記場所，尚未啟用服務收款。",
                ephemeral=True,
            )

        except (
            PermissionError,
            ValueError,
            sqlite3.Error,
            discord.HTTPException,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )


# ==========================================
# 修改表單
# ==========================================

class EditSceneModal(
    discord.ui.Modal,
    title="管理場景商店",
):
    shop_id = discord.ui.TextInput(
        label="場景商店編號",
        max_length=12,
    )

    status = discord.ui.TextInput(
        label="營業狀態：營業／停業（留空不變）",
        required=False,
        max_length=5,
    )

    name = discord.ui.TextInput(
        label="新店名（留空不變）",
        required=False,
        max_length=80,
    )

    description = discord.ui.TextInput(
        label="新簡介（留空不變）",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    async def on_submit(self, interaction):
        try:
            require_owner(interaction)

            shop_id = int(self.shop_id.value)

            if shop_id <= 0:
                raise ValueError("商店編號必須大於 0。")

            raw = str(
                self.status.value or ""
            ).strip()

            mapping = {
                "": None,
                "營業": "open",
                "停業": "closed",
            }

            if raw not in mapping:
                raise ValueError(
                    "營業狀態請填營業、停業或留空。"
                )

            edit_shop(
                interaction.guild_id,
                interaction.user.id,
                shop_id,
                mapping[raw],
                str(self.name.value or ""),
                str(self.description.value or ""),
            )

            await interaction.response.send_message(
                f"✅ 場景商店 #{shop_id} 已更新。",
                ephemeral=True,
            )

        except (
            PermissionError,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )


# ==========================================
# 管理面板
# ==========================================

class SceneShopView(discord.ui.View):
    def __init__(self, interaction):
        super().__init__(timeout=600)

        self.owner_id = interaction.user.id
        self.guild_id = interaction.guild_id

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or interaction.guild_id != self.guild_id
            or not is_owner(interaction)
        ):
            await interaction.response.send_message(
                "⛔ 你沒有權限操作這個面板。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="➕ 建立場景商店",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def create(self, interaction, button):
        await interaction.response.send_modal(
            CreateSceneModal()
        )

    @discord.ui.button(
        label="📋 商店清單",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def show(self, interaction, button):
        rows = list_shops(interaction.guild_id)

        lines = ["🏪 **場景商店列表**"]

        for item in rows:
            marker = (
                "🟢"
                if item["status"] == "open"
                else "🔴"
            )

            name = discord.utils.escape_markdown(
                item["name"]
            )

            lines.append(
                f"{marker} #{item['id']}｜"
                f"{name}｜{item['kind']}｜"
                f"<#{item['channel_id']}>"
            )

        await interaction.response.send_message(
            "\n".join(lines)[:1900]
            if rows
            else "目前沒有場景商店。",
            ephemeral=True,
        )

    @discord.ui.button(
        label="✏️ 修改／營業狀態",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def edit(self, interaction, button):
        await interaction.response.send_modal(
            EditSceneModal()
        )

    @discord.ui.button(
        label="⬅️ 返回管理中心",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(self, interaction, button):
        from modules.official_center import (
            AdminToolsView,
            management_text,
        )

        view = AdminToolsView(interaction)

        await interaction.response.edit_message(
            content=management_text(view),
            view=view,
        )


# ==========================================
# 官方管理入口
# ==========================================

class SceneShopButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🏪 場景商店",
            style=discord.ButtonStyle.secondary,
            row=3,
        )

    async def callback(self, interaction):
        if not is_owner(interaction):
            await interaction.response.send_message(
                "⛔ 只有群主可以管理場景商店。",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=(
                "🏪 **場景商店管理**\n\n"
                "建立餐廳、診所等 RP 場景店鋪，"
                "並綁定 Discord 頻道。\n\n"
                "目前為登記階段，暫不提供收款功能。"
            ),
            view=SceneShopView(interaction),
        )


# ==========================================
# 初始化
# ==========================================

async def setup(bot: commands.Bot):
    init_db()

    LOG.info("場景商店管理模組載入完成。")
