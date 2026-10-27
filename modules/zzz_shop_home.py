
"""商城首頁分類擴充：modules/zzz_shop_home.py。

功能：
- B01～B10 十間商店分類
- 黑卡隱藏商店權限檢查
- 商品瀏覽及分頁
- 沿用原本商城的購買交易
- 行動值購買功能預留入口

不建立第二個 /商城 指令。
不修改 Discord Command.callback 唯讀屬性。
不修改玩家金流、背包或 CP 好感度資料。
"""

from __future__ import annotations

import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)
from modules import shop

LOG = logging.getLogger(__name__)

STORES = [
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
]


# ==========================================
# 商品資料查詢
# ==========================================

def products_for_store(
    actor,
    cid: int,
    store: str,
    page: int,
):
    with get_connection() as conn:
        shop.require_character(
            conn,
            actor,
            cid,
        )

        black = store == "B10"

        if black and not shop.has_black_access(
            conn,
            actor.guild_id,
            cid,
        ):
            raise PermissionDenied(
                "只有有效黑卡會員才能進入隱藏商店。"
            )

        scope = "black" if black else "public"

        if black:
            match_sql = ""
            match_args = []
        else:
            match_sql = (
                " AND (category = ? OR category LIKE ?)"
            )
            match_args = [
                store,
                store + "-%",
            ]

        where = (
            "WHERE guild_id=? "
            "AND scope=? "
            "AND active=1"
            + match_sql
        )

        params = [
            actor.guild_id,
            scope,
        ] + match_args

        total = conn.execute(
            "SELECT COUNT(*) "
            "FROM shop_products "
            + where,
            params,
        ).fetchone()[0]

        max_page = max(
            0,
            (total - 1) // 10,
        )

        page = max(
            0,
            min(int(page), max_page),
        )

        rows = conn.execute(
            "SELECT id, name, category, price, stock "
            "FROM shop_products "
            + where
            + " ORDER BY id "
            "LIMIT 10 OFFSET ?",
            params + [page * 10],
        ).fetchall()

    return total, page, max_page, rows


# ==========================================
# 商店分類選單
# ==========================================

class StoreSelect(discord.ui.Select):
    def __init__(self, actor, cid):
        self.actor = actor
        self.cid = cid

        with get_connection() as conn:
            shop.require_character(
                conn,
                actor,
                cid,
            )

            black_ok = shop.has_black_access(
                conn,
                actor.guild_id,
                cid,
            )

        options = [
            discord.SelectOption(
                label=label,
                value=code,
            )
            for code, label in STORES
            if code != "B10" or black_ok
        ]

        super().__init__(
            placeholder="選擇要逛的商店",
            options=options,
            row=0,
        )

    async def callback(self, interaction):
        actor = Actor.from_interaction(
            interaction
        )

        if str(interaction.user.id) != self.actor.user_id:
            await interaction.response.send_message(
                "⛔ 請開啟自己的商城選單。",
                ephemeral=True,
            )
            return

        code = self.values[0]

        try:
            title, view = make_store_screen(
                actor,
                self.cid,
                code,
                0,
            )

        except (
            InvalidState,
            PermissionDenied,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=title,
            view=view,
        )


# ==========================================
# 商城首頁
# ==========================================

class Home(discord.ui.View):
    def __init__(self, actor, cid):
        super().__init__(timeout=900)

        self.actor = actor
        self.cid = cid

        self.add_item(
            StoreSelect(actor, cid)
        )

    async def interaction_check(
        self,
        interaction,
    ):
        if str(interaction.user.id) != self.actor.user_id:
            await interaction.response.send_message(
                "⛔ 請使用自己的商城選單。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="⚡ 行動值補給說明",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def action_info(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_message(
            (
                "⚡ **行動值補給**\n\n"
                "行動值購買功能尚未連接"
                "正式價格與扣款系統。\n"
                "目前不開放付款，"
                "也不會扣除玩家金幣。"
            ),
            ephemeral=True,
        )


# ==========================================
# 商品頁面
# ==========================================

class StoreScreen(discord.ui.View):
    def __init__(
        self,
        actor,
        cid,
        code,
        page,
        max_page,
    ):
        super().__init__(timeout=900)

        self.actor = actor
        self.cid = cid
        self.code = code
        self.page = page

        self.owner_id = int(actor.user_id)
        self.character_id = cid

        self.previous.disabled = page == 0
        self.next.disabled = page >= max_page

    async def interaction_check(
        self,
        interaction,
    ):
        if str(interaction.user.id) != self.actor.user_id:
            await interaction.response.send_message(
                "⛔ 請使用自己的商城選單。",
                ephemeral=True,
            )
            return False

        return True

    async def move(
        self,
        interaction,
        page,
    ):
        actor = Actor.from_interaction(
            interaction
        )

        try:
            title, view = make_store_screen(
                actor,
                self.cid,
                self.code,
                page,
            )

        except (
            InvalidState,
            PermissionDenied,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=title,
            view=view,
        )

    @discord.ui.button(
        label="🛒 購買商品",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def buy(
        self,
        interaction,
        button,
    ):
        proxy = shop.ShopMenu(
            self.owner_id,
            self.cid,
            self.code == "B10",
            self.code,
            interaction.guild_id,
        )

        await interaction.response.send_modal(
            shop.ShopActionModal(
                proxy,
                "buy",
            )
        )

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous(
        self,
        interaction,
        button,
    ):
        await self.move(
            interaction,
            self.page - 1,
        )

    @discord.ui.button(
        label="➡️ 下一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next(
        self,
        interaction,
        button,
    ):
        await self.move(
            interaction,
            self.page + 1,
        )

    @discord.ui.button(
        label="🏠 商城首頁",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def home(
        self,
        interaction,
        button,
    ):
        actor = Actor.from_interaction(
            interaction
        )

        await interaction.response.edit_message(
            content=home_text(self.cid),
            view=Home(
                actor,
                self.cid,
            ),
        )


# ==========================================
# 商城文字
# ==========================================

def home_text(cid):
    return (
        f"🛍️ **官方商城**｜角色 #{cid}\n\n"
        "請選擇商店瀏覽及購買商品。\n\n"
        "🎒 背包、贈送、使用物品："
        "請前往 `/查看`。\n"
        "🎁 B09 特殊道具店："
        "商品持續新增中。\n"
        "🖤 B10 黑卡隱藏商店："
        "僅有效黑卡會員可見。\n\n"
        "⚡ 行動值快捷補給："
        "尚未開放付款。"
    )


def make_store_screen(
    actor,
    cid,
    code,
    page,
):
    if code not in dict(STORES):
        raise ValueError(
            "商店編號不正確。"
        )

    total, page, maximum, rows = (
        products_for_store(
            actor,
            cid,
            code,
            page,
        )
    )

    header = (
        f"**{dict(STORES)[code]}**\n"
        f"已上架商品：{total} 件\n"
        f"第 {page + 1}/{maximum + 1} 頁\n\n"
    )

    if rows:
        lines = []

        for row in rows:
            stock = (
                "不限"
                if row["stock"] is None
                else row["stock"]
            )

            lines.append(
                f"#{row['id']}｜"
                f"{row['name']}｜"
                f"{row['price']:,} 金幣｜"
                f"庫存：{stock}"
            )

        text = "\n".join(lines)

    else:
        text = "目前沒有已上架商品。"

    footer = (
        "\n\n🛒 購買商品時，"
        "請使用畫面顯示的 #商品編號。"
    )

    return (
        header + text + footer,
        StoreScreen(
            actor,
            cid,
            code,
            page,
            maximum,
        ),
    )


# ==========================================
# 商城正式指令
# ==========================================

class ShopHomeAddon(commands.Cog):
    """商城分類與購買入口。"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="商城",
        description="瀏覽十間商店並購買商品",
    )
    @app_commands.guild_only()
    async def shop_home(
        self,
        interaction: discord.Interaction,
        角色編號: int,
        黑卡商城: bool = False,
        商品分類: str = "",
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            with get_connection() as conn:
                shop.require_character(
                    conn,
                    actor,
                    角色編號,
                )

            if 黑卡商城:
                content, view = make_store_screen(
                    actor,
                    角色編號,
                    "B10",
                    0,
                )

            elif (
                商品分類
                and 商品分類 in dict(STORES)
            ):
                content, view = make_store_screen(
                    actor,
                    角色編號,
                    商品分類,
                    0,
                )

            else:
                content = home_text(
                    角色編號
                )

                view = Home(
                    actor,
                    角色編號,
                )

            await interaction.response.send_message(
                content,
                view=view,
                ephemeral=True,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"⚠️ {exc}",
                    ephemeral=True,
                )

            else:
                await interaction.response.send_message(
                    f"⚠️ {exc}",
                    ephemeral=True,
                )


# ==========================================
# 載入模組
# ==========================================

async def setup(bot: commands.Bot):
    """使用正式 discord.py API 替換商城入口。"""

    old_command = bot.tree.get_command(
        "商城"
    )

    if old_command is not None:
        bot.tree.remove_command(
            "商城",
            type=discord.AppCommandType.chat_input,
        )

    try:
        await bot.add_cog(
            ShopHomeAddon(bot)
        )

    except Exception:
        # 新指令註冊失敗時，恢復原本商城入口。
        if (
            old_command is not None
            and bot.tree.get_command("商城") is None
        ):
            bot.tree.add_command(
                old_command
            )

        raise

    LOG.info(
        "新版十間商店首頁已載入，"
        "使用正式指令註冊方式。"
    )
