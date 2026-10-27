
"""商城介面整合模組。

將玩家商店加入既有 /商城 選單。

本模組不註冊第二個 /商城 指令。
必須在 modules.shop 及 modules.storefront
載入後執行。

保留原本的官方商城、黑卡商城與管理指令。
"""

from __future__ import annotations

import asyncio
import sqlite3

import discord
from discord.ext import commands

from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)
from core.database import get_connection

from modules import shop as official_shop
from modules import storefront


ERRORS = (
    ValueError,
    InvalidState,
    PermissionDenied,
    sqlite3.Error,
)


def owns_personal_shop(guild_id, user_id):
    """只有真正擁有個人商店的玩家才有店主管理入口。"""

    if guild_id is None:
        return False

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM personal_shops s
            JOIN characters c
              ON c.guild_id = s.guild_id
             AND c.id = s.character_id
            WHERE s.guild_id = ?
              AND c.owner_id = ?
              AND s.status != 'closed'
            LIMIT 1
            """,
            (
                str(guild_id),
                str(user_id),
            ),
        ).fetchone()

        return row is not None


def require_positive(value, label):
    try:
        number = int(str(value).strip())

    except (TypeError, ValueError):
        raise ValueError(
            f"{label}必須是正整數。"
        )

    if number <= 0:
        raise ValueError(
            f"{label}必須大於零。"
        )

    return number


def combined_inventory(actor, character_id):
    """將官方及玩家商品合併到同一個背包畫面。"""

    official = official_shop.inventory(
        actor,
        character_id,
    )

    personal = storefront.personal_inventory(
        actor,
        character_id,
    )

    return (
        f"🎒 角色 #{character_id} 的背包\n\n"
        "【官方商城商品】\n"
        f"{official}\n\n"
        "【玩家商店商品】\n"
        f"{personal}"
    )


def store_catalog(guild_id):
    stores = storefront.list_stores(
        guild_id
    )

    if not stores:
        return (
            "🏬 玩家商店\n"
            "目前沒有營業中的個人商店。"
        )

    lines = ["🏬 玩家商店列表"]

    for item in stores[:25]:
        lines.append(
            f"#{item['id']}｜"
            f"{item['name']}｜"
            f"LV.{item['level']}｜"
            f"{item['category']}"
        )

    if len(stores) > 25:
        lines.append(
            "目前顯示前25家店鋪。"
        )

    return "\n".join(lines)


def product_catalog(guild_id, shop_id):
    products = storefront.list_products(
        guild_id,
        shop_id,
    )

    if not products:
        return (
            f"商店 #{shop_id} "
            "目前沒有上架商品。"
        )

    lines = [
        f"🏬 商店 #{shop_id} 商品列表"
    ]

    for product in products[:25]:
        stock = (
            "不限"
            if product["stock"] is None
            else str(product["stock"])
        )

        lines.append(
            f"#{product['id']}｜"
            f"{product['name']}｜"
            f"{product['price']:,}金幣｜"
            f"庫存：{stock}"
        )

    if len(products) > 25:
        lines.append(
            "目前顯示前25件商品。"
        )

    return "\n".join(lines)


class BaseModal(discord.ui.Modal):

    def __init__(
        self,
        menu,
        title,
    ):
        super().__init__(
            title=title,
            timeout=300,
        )

        self.menu = menu

    async def do_work(
        self,
        interaction,
        func,
    ):
        if interaction.user.id != self.menu.owner_id:
            await interaction.response.send_message(
                "這不是你的商城操作選單。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                func
            )

        except ERRORS as exc:
            result = f"⚠️ {exc}"

        except Exception:
            result = (
                "⚠️ 操作未完成，"
                "請管理員查看機器人錯誤紀錄。"
            )

            await interaction.followup.send(
                result,
                ephemeral=True,
            )
            raise

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


class ViewStoreModal(BaseModal):

    def __init__(self, menu):
        super().__init__(
            menu,
            "查看玩家商店",
        )

        self.shop_id = discord.ui.TextInput(
            label="商店編號",
            placeholder="例如：3",
            max_length=12,
        )

        self.add_item(
            self.shop_id
        )

    async def on_submit(
        self,
        interaction,
    ):
        def work():
            shop_id = require_positive(
                self.shop_id.value,
                "商店編號",
            )

            return product_catalog(
                interaction.guild_id,
                shop_id,
            )

        await self.do_work(
            interaction,
            work,
        )


class BuyStoreModal(BaseModal):

    def __init__(self, menu):
        super().__init__(
            menu,
            "購買玩家商店商品",
        )

        self.product_id = discord.ui.TextInput(
            label="商品編號",
            max_length=12,
        )

        self.quantity = discord.ui.TextInput(
            label="數量",
            default="1",
            max_length=8,
        )

        self.add_item(
            self.product_id
        )

        self.add_item(
            self.quantity
        )

    async def on_submit(
        self,
        interaction,
    ):
        def work():
            product_id = require_positive(
                self.product_id.value,
                "商品編號",
            )

            quantity = require_positive(
                self.quantity.value,
                "購買數量",
            )

            return storefront.purchase(
                Actor.from_interaction(
                    interaction
                ),
                self.menu.character_id,
                product_id,
                quantity,
                str(interaction.id),
            )

        await self.do_work(
            interaction,
            work,
        )


class AddStoreProductModal(BaseModal):

    def __init__(self, menu):
        super().__init__(
            menu,
            "玩家商店新增商品",
        )

        self.shop_id = discord.ui.TextInput(
            label="商店編號",
            max_length=12,
        )

        self.name = discord.ui.TextInput(
            label="商品名稱",
            max_length=100,
        )

        self.category = discord.ui.TextInput(
            label="商品分類",
            max_length=60,
        )

        self.price = discord.ui.TextInput(
            label="商品價格（金幣）",
            max_length=16,
        )

        self.stock = discord.ui.TextInput(
            label="庫存（留空代表不限）",
            required=False,
            max_length=12,
        )

        for field in (
            self.shop_id,
            self.name,
            self.category,
            self.price,
            self.stock,
        ):
            self.add_item(field)

    async def on_submit(
        self,
        interaction,
    ):
        def work():
            actor = Actor.from_interaction(
                interaction
            )

            if not owns_personal_shop(
                interaction.guild_id,
                interaction.user.id,
            ):
                raise PermissionDenied(
                    "只有擁有商店的店主可以新增商品。"
                )

            shop_id = require_positive(
                self.shop_id.value,
                "商店編號",
            )

            price = int(
                str(self.price.value)
            )

            stock_text = str(
                self.stock.value or ""
            ).strip()

            stock = (
                int(stock_text)
                if stock_text
                else None
            )

            product_id = storefront.create_product(
                actor,
                shop_id,
                str(self.name.value),
                str(self.category.value),
                price,
                stock,
            )

            return (
                "✅ 商品已上架！\n"
                f"商品編號：#{product_id}\n"
                "無須另外提交官方審核。"
            )

        await self.do_work(
            interaction,
            work,
        )


class EditStoreProductModal(BaseModal):

    def __init__(self, menu):
        super().__init__(
            menu,
            "玩家商店修改商品",
        )

        self.shop_id = discord.ui.TextInput(
            label="商店編號",
            max_length=12,
        )

        self.product_id = discord.ui.TextInput(
            label="商品編號",
            max_length=12,
        )

        self.price = discord.ui.TextInput(
            label="新價格（留空不更改）",
            required=False,
            max_length=16,
        )

        self.stock = discord.ui.TextInput(
            label="新庫存（留空不更改）",
            required=False,
            max_length=12,
        )

        self.status = discord.ui.TextInput(
            label="上下架：上架／下架／留空",
            required=False,
            max_length=8,
        )

        for field in (
            self.shop_id,
            self.product_id,
            self.price,
            self.stock,
            self.status,
        ):
            self.add_item(field)

    async def on_submit(
        self,
        interaction,
    ):
        def work():
            actor = Actor.from_interaction(
                interaction
            )

            if not owns_personal_shop(
                interaction.guild_id,
                interaction.user.id,
            ):
                raise PermissionDenied(
                    "只有擁有商店的店主可以修改商品。"
                )

            shop_id = require_positive(
                self.shop_id.value,
                "商店編號",
            )

            product_id = require_positive(
                self.product_id.value,
                "商品編號",
            )

            raw_price = str(
                self.price.value or ""
            ).strip()

            raw_stock = str(
                self.stock.value or ""
            ).strip()

            raw_status = str(
                self.status.value or ""
            ).strip()

            price = (
                int(raw_price)
                if raw_price
                else None
            )

            stock = (
                int(raw_stock)
                if raw_stock
                else None
            )

            if raw_status == "上架":
                active = True

            elif raw_status == "下架":
                active = False

            elif not raw_status:
                active = None

            else:
                raise ValueError(
                    "上下架欄位只能填"
                    "上架、下架或留空。"
                )

            return storefront.update_product(
                actor,
                shop_id,
                product_id,
                price=price,
                stock=stock,
                active=active,
            )

        await self.do_work(
            interaction,
            work,
        )


class UnifiedShopMenu(official_shop.ShopMenu):
    """擴充原有商城按鈕，不建立新指令。"""

    def __init__(
        self,
        owner_id,
        character_id,
        black,
        category,
        guild_id=None,
    ):
        super().__init__(
            owner_id,
            character_id,
            black,
            category,
            guild_id,
        )

        # 只有真正擁有商店的玩家，才顯示管理按鈕。
        if not owns_personal_shop(
            guild_id,
            owner_id,
        ):
            self.remove_item(
                self.add_store_product
            )

            self.remove_item(
                self.edit_store_product
            )

    @discord.ui.button(
        label="玩家商店",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def player_stores(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                store_catalog,
                interaction.guild_id,
            )

        except ERRORS as exc:
            result = f"⚠️ {exc}"

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )

    @discord.ui.button(
        label="查看店鋪商品",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def browse_store(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_modal(
            ViewStoreModal(self)
        )

    @discord.ui.button(
        label="購買玩家商品",
        style=discord.ButtonStyle.success,
        row=2,
    )
    async def buy_store(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_modal(
            BuyStoreModal(self)
        )

    @discord.ui.button(
        label="店主新增商品",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def add_store_product(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not owns_personal_shop(
            interaction.guild_id,
            interaction.user.id,
        ):
            await interaction.response.send_message(
                "只有擁有商店的店主可以管理商品。",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            AddStoreProductModal(self)
        )

    @discord.ui.button(
        label="店主修改商品",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def edit_store_product(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not owns_personal_shop(
            interaction.guild_id,
            interaction.user.id,
        ):
            await interaction.response.send_message(
                "只有擁有商店的店主可以管理商品。",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            EditStoreProductModal(self)
        )


class ShopIntegration(commands.Cog):

    def __init__(self, bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    """在原始商城模組載入後擴充選單。"""

    if bot.get_cog("Shop") is None:
        raise RuntimeError(
            "請先載入 modules.shop，"
            "再載入 modules.shop_integration。"
        )

    await asyncio.to_thread(
        storefront.initialize_storefront
    )

    # 讓 /商城 使用整合後的商城按鈕。
    official_shop.ShopMenu = UnifiedShopMenu

    await bot.add_cog(
        ShopIntegration(bot)
    )
