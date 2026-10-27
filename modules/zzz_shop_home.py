"""商城首頁分類擴充：新增 modules/zzz_shop_home.py。

不建立第二個 /商城，不改動金流、角色背包和 CP 購買掛鉤。
目前資料庫沒有完整行動值付費接口，因此只提供狀態說明，絕不扣款。
注意：本版只顯示資料庫內已上架商品，不自動將候選商品視為已上架。
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord.ext import commands

from core.database import get_connection
from core.permissions import Actor, InvalidState, PermissionDenied
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

# 既有商品的 category 若尚未轉為 B01/B02...，不會擅自改動資料。
def products_for_store(actor, cid: int, store: str, page: int):
    with get_connection() as conn:
        shop.require_character(conn, actor, cid)
        black = store == "B10"
        if black and not shop.has_black_access(conn, actor.guild_id, cid):
            raise PermissionDenied("只有有效黑卡會員才能進入隱藏商店。")
        scope = "black" if black else "public"
        # 接受 category=B01，或 category 開頭帶商店代號的舊目錄。
        if black:
            match_sql, match_args = "", []
        else:
            match_sql = " AND (category = ? OR category LIKE ?)"
            match_args = [store, store + "-%"]
        where = "WHERE guild_id=? AND scope=? AND active=1" + match_sql
        params = [actor.guild_id, scope] + match_args
        total = conn.execute("SELECT COUNT(*) FROM shop_products " + where, params).fetchone()[0]
        max_page = max(0, (total - 1) // 10)
        page = max(0, min(int(page), max_page))
        rows = conn.execute(
            "SELECT id,name,category,price,stock FROM shop_products "
            + where + " ORDER BY id LIMIT 10 OFFSET ?", params + [page * 10]
        ).fetchall()
    return total, page, max_page, rows


class StoreSelect(discord.ui.Select):
    def __init__(self, actor, cid):
        self.actor, self.cid = actor, cid
        with get_connection() as db:
            shop.require_character(db, actor, cid)
            black_ok = shop.has_black_access(db, actor.guild_id, cid)
        options = [discord.SelectOption(label=label, value=code)
                   for code, label in STORES if code != "B10" or black_ok]
        super().__init__(placeholder="選擇要逛的商店", options=options, row=0)

    async def callback(self, interaction):
        actor = Actor.from_interaction(interaction)
        if str(interaction.user.id) != self.actor.user_id:
            await interaction.response.send_message("請開啟自己的商城選單。", ephemeral=True)
            return
        code = self.values[0]
        try:
            title, view = make_store_screen(actor, self.cid, code, 0)
        except (InvalidState, PermissionDenied, ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        await interaction.response.edit_message(content=title, view=view)


class Home(discord.ui.View):
    def __init__(self, actor, cid):
        super().__init__(timeout=900)
        self.actor, self.cid = actor, cid
        self.add_item(StoreSelect(actor, cid))

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != self.actor.user_id:
            await interaction.response.send_message("請使用自己的商城選單。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⚡ 行動值補給說明", style=discord.ButtonStyle.secondary, row=1)
    async def action_info(self, interaction, button):
        await interaction.response.send_message(
            "⚡ 行動值購買功能尚未連接正式價格與扣款系統，目前不開放付款，也不會扣除金幣。",
            ephemeral=True,
        )


class StoreScreen(discord.ui.View):
    def __init__(self, actor, cid, code, page, max_page):
        super().__init__(timeout=900)
        self.actor, self.cid, self.code, self.page = actor, cid, code, page
        self.owner_id = int(actor.user_id)
        self.character_id = cid
        self.previous.disabled = page == 0
        self.next.disabled = page >= max_page

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != self.actor.user_id:
            await interaction.response.send_message("請使用自己的商城選單。", ephemeral=True)
            return False
        return True

    async def move(self, interaction, page):
        actor = Actor.from_interaction(interaction)
        try:
            title, view = make_store_screen(actor, self.cid, self.code, page)
        except (InvalidState, PermissionDenied, ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        await interaction.response.edit_message(content=title, view=view)

    @discord.ui.button(label="🛒 購買商品", style=discord.ButtonStyle.success, row=0)
    async def buy(self, interaction, button):
        # 直接沿用原版購買表單及交易流程，保留好感度擴充掛鉤。
        proxy = shop.ShopMenu(self.owner_id, self.cid, self.code == "B10", self.code)
        await interaction.response.send_modal(shop.ShopActionModal(proxy, "buy"))

    @discord.ui.button(label="⬅️ 上一頁", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction, button):
        await self.move(interaction, self.page - 1)

    @discord.ui.button(label="➡️ 下一頁", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction, button):
        await self.move(interaction, self.page + 1)

    @discord.ui.button(label="🏠 商城首頁", style=discord.ButtonStyle.primary, row=2)
    async def home(self, interaction, button):
        actor = Actor.from_interaction(interaction)
        await interaction.response.edit_message(content=home_text(self.cid), view=Home(actor, self.cid))


def home_text(cid):
    return (f"🛍️ **官方商城**｜角色 #{cid}\n"
            "選擇商店即可瀏覽及購買商品。\n"
            "🎒 背包、贈送與使用請前往 `/查看`。\n"
            "B09、B10 的商品目錄目前可留空。")


def make_store_screen(actor, cid, code, page):
    if code not in dict(STORES):
        raise ValueError("商店編號不正確。")
    total, page, maximum, rows = products_for_store(actor, cid, code, page)
    header = f"**{dict(STORES)[code]}**｜{total} 件已上架｜第 {page+1}/{maximum+1} 頁\n"
    text = "\n".join(
        f"#{r['id']}｜{r['name']}｜{r['price']:,} 金幣｜庫存："
        f"{'不限' if r['stock'] is None else r['stock']}" for r in rows
    ) if rows else "目前沒有已上架商品。"
    # 避免使用者誤以 FS/BT 等自訂編號等同資料庫流水號。
    return header + text + "\n\n購買時請輸入畫面顯示的 #數字商品編號。", StoreScreen(actor, cid, code, page, maximum)


class ShopHomeAddon(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    original = shop.Shop.shop
    # 不更換 Discord slash command 本體；只替換原有指令 callback。
    async def new_callback(self, interaction: discord.Interaction, 角色編號: int,
                           黑卡商城: bool = False, 商品分類: str = ""):
        try:
            actor = Actor.from_interaction(interaction)
            with get_connection() as conn:
                shop.require_character(conn, actor, 角色編號)
            if 黑卡商城:
                content, view = make_store_screen(actor, 角色編號, "B10", 0)
            elif 商品分類 and 商品分類 in dict(STORES):
                content, view = make_store_screen(actor, 角色編號, 商品分類, 0)
            else:
                content, view = home_text(角色編號), Home(actor, 角色編號)
            await interaction.response.send_message(content, view=view, ephemeral=True)
        except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as exc:
            if interaction.response.is_done():
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
    original.callback = new_callback
    await bot.add_cog(ShopHomeAddon(bot))

