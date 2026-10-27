
"""Discord RP｜十間商店分類面板。

沿用 modules.shop 唯一的 /商城 指令。
不重複註冊指令，不修改 Command.callback。
"""

from __future__ import annotations

import logging
import sqlite3

import discord
from discord.ext import commands

from core.database import get_connection
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)
from modules import shop

LOG = logging.getLogger(__name__)

STORES = (
    ("B01", "👗 服飾精品店"),
    ("B02", "💄 美妝保養店"),
    ("B03", "💎 珠寶鐘錶店"),
    ("B04", "📱 數位科技店"),
    ("B05", "🎭 演藝用品店"),
    ("B06", "📚 書籍文具店"),
    ("B07", "🏠 家居生活店"),
    ("B08", "🎮 娛樂收藏店"),
    ("B09", "🎁 特殊道具店"),
    ("B10", "🖤 黑卡隱藏商店"),
)


def ensure_access(actor, character_id, store=None):
    with get_connection() as conn:
        shop.require_character(
            conn, actor, character_id
        )

        if store == "B10":
            if not shop.has_black_access(
                conn, actor.guild_id, character_id
            ):
                raise PermissionDenied(
                    "只有有效黑卡會員可以查看此商店。"
                )


def list_store(actor, character_id, store, page=0):
    if store not in dict(STORES):
        raise InvalidState("商店不存在。")

    ensure_access(actor, character_id, store)

    scope = "black" if store == "B10" else "public"

    query = "guild_id=? AND scope=? AND active=1"

    params = [actor.guild_id, scope]

    if store != "B10":
        query += (
            " AND (category LIKE ? OR category LIKE ?)"
        )

        params.extend([
            f"%{store}%",
            f"%{dict(STORES)[store].split(' ', 1)[-1]}%",
        ])

    with get_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM shop_products WHERE "
            + query,
            params,
        ).fetchone()[0]

        last = max(0, (total - 1) // 10)
        page = max(0, min(int(page), last))

        rows = conn.execute(
            "SELECT id,name,price,stock "
            "FROM shop_products WHERE "
            + query
            + " ORDER BY id LIMIT 10 OFFSET ?",
            params + [page * 10],
        ).fetchall()

    lines = []

    for row in rows:
        stock = (
            "不限"
            if row["stock"] is None
            else row["stock"]
        )

        lines.append(
            f"#{row['id']}｜{row['name']}｜"
            f"{int(row['price']):,} 金幣｜"
            f"庫存：{stock}"
        )

    content = (
        f"**{dict(STORES)[store]}**\n"
        f"商品：{total} 件｜"
        f"第 {page + 1}/{last + 1} 頁\n\n"
        + (
            "\n".join(lines)
            if lines
            else "目前尚未上架商品。"
        )
        + "\n\n購買請輸入清單中的 #數字商品編號。"
    )

    return content[:1900], page, last


class StorePicker(discord.ui.Select):
    def __init__(self, menu, black_allowed):
        self.menu = menu

        options = [
            discord.SelectOption(
                label=label,
                value=code,
            )
            for code, label in STORES
            if code != "B10" or black_allowed
        ]

        super().__init__(
            placeholder="選擇官方商店",
            options=options,
            row=0,
        )

    async def callback(self, interaction):
        actor = Actor.from_interaction(interaction)

        try:
            code = self.values[0]

            content, page, last = list_store(
                actor,
                self.menu.character_id,
                code,
            )

            await interaction.response.edit_message(
                content=content,
                view=ShopHome(
                    self.menu.owner_id,
                    self.menu.character_id,
                    code == "B10",
                    code,
                    self.menu.guild_id,
                    page=page,
                    last=last,
                ),
            )

        except (
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )


class ShopHome(discord.ui.View):
    """沿用舊商城選單建構介面。"""

    def __init__(
        self,
        owner_id,
        character_id,
        black=False,
        category="",
        guild_id=None,
        *,
        page=0,
        last=0,
    ):
        super().__init__(timeout=900)

        self.owner_id = int(owner_id)
        self.character_id = int(character_id)
        self.black = bool(black)
        self.category = str(category or "")
        self.guild_id = guild_id
        self.page = page
        self.last = last

        if self.category not in dict(STORES):
            self.category = ""
            self.black = False

        if not self.category:
            with get_connection() as conn:
                black_allowed = shop.has_black_access(
                    conn,
                    str(guild_id),
                    self.character_id,
                )

            self.add_item(
                StorePicker(self, black_allowed)
            )

            self.buy_button.disabled = True
            self.previous.disabled = True
            self.next.disabled = True

        else:
            self.previous.disabled = page <= 0
            self.next.disabled = page >= last

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or interaction.guild_id != self.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的商城選單。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="🛒 購買商品",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def buy_button(self, interaction, button):
        actor = Actor.from_interaction(interaction)

        try:
            ensure_access(
                actor,
                self.character_id,
                self.category,
            )

        except (
            InvalidState,
            PermissionDenied,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            shop.ShopActionModal(self, "buy")
        )

    async def move(self, interaction, requested):
        actor = Actor.from_interaction(interaction)

        try:
            content, page, last = list_store(
                actor,
                self.character_id,
                self.category,
                requested,
            )

            await interaction.response.edit_message(
                content=content,
                view=ShopHome(
                    self.owner_id,
                    self.character_id,
                    self.black,
                    self.category,
                    self.guild_id,
                    page=page,
                    last=last,
                ),
            )

        except (
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def previous(self, interaction, button):
        await self.move(
            interaction,
            self.page - 1,
        )

    @discord.ui.button(
        label="➡️ 下一頁",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def next(self, interaction, button):
        await self.move(
            interaction,
            self.page + 1,
        )

    @discord.ui.button(
        label="🏠 商城分類",
        style=discord.ButtonStyle.primary,
        row=3,
    )
    async def back(self, interaction, button):
        await interaction.response.edit_message(
            content=(
                "🛍️ **官方商城**\n"
                "請從下方選擇商店。\n"
                "背包、贈送與使用請前往 /查看。"
            ),
            view=ShopHome(
                self.owner_id,
                self.character_id,
                False,
                "",
                self.guild_id,
            ),
        )

    @discord.ui.button(
        label="⚡ 行動值補給",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def action_info(self, interaction, button):
        await interaction.response.send_message(
            (
                "⚡ 行動值購買尚未連接"
                "已驗證的扣款與計價功能，"
                "暫不開放付款。"
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    # 只替換舊商城介面類別。
    # 不建立第二個 /商城 指令。
    shop.ShopMenu = ShopHome

    LOG.info(
        "已啟用十間商店分類面板，"
        "沿用原有 /商城 指令。"
    )
