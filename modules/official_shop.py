
"""Discord RP｜官方商城統一管理中心。

檔案：modules/official_shop.py

功能：
- 官方商品新增、修改、上下架
- 商品使用次數設定與查詢
- 商品使用次數批量匯入
- B02～B10 商店分類管理
- B02、B04 文字匯入
- B03、B05～B10 一鍵上架
- 各商店上架進度查詢
- 群主專屬批量上架權限
- 管理面板原訊息切換

不建立第二個 /商城 或 /官方 指令。
不清空任何角色、銀行、商城、背包資料。
"""

from __future__ import annotations

import asyncio
import importlib
import logging

import discord
from discord.ext import commands

from core.permissions import Actor


LOG = logging.getLogger(__name__)


STORES = {
    "B01": "👗 服飾精品店",
    "B02": "💄 美妝保養店",
    "B03": "💎 珠寶鐘錶店",
    "B04": "📱 數位科技店",
    "B05": "🎭 演藝用品店",
    "B06": "📚 書籍文具店",
    "B07": "🏠 家居生活店",
    "B08": "🎮 娛樂收藏店",
    "B09": "🎁 特殊道具店",
    "B10": "🖤 黑卡隱藏商店",
}

# 使用固定商品資料的一鍵上架模組。
AUTO_STORES = {
    "B03",
    "B05",
    "B06",
    "B07",
    "B08",
    "B09",
    "B10",
}

# 使用貼上商品資料的匯入模組。
TEXT_STORES = {
    "B02",
    "B04",
}


# ==========================================
# 權限
# ==========================================

def is_guild_owner(interaction):
    return bool(
        interaction.guild
        and interaction.user.id
        == interaction.guild.owner_id
    )


def permitted(interaction):
    if interaction.guild is None:
        return False

    if is_guild_owner(interaction):
        return True

    actor = Actor.from_interaction(
        interaction
    )

    return bool(actor.is_admin)


def require_owner(interaction):
    if not is_guild_owner(interaction):
        raise PermissionError(
            "批量上架商品僅限伺服器群主。"
        )


def panel_text(store=""):
    lines = [
        "🛍️ **官方商城管理中心**",
        "",
        "請先選擇管理功能或商店。",
        "",
        "📦 固定商品：可直接批量上架",
        "📋 B02、B04：使用文字匯入",
        "🔎 可查詢各商店登記進度",
        "",
    ]

    if store:
        lines.append(
            f"**目前選擇：{STORES[store]}**"
        )

        if store == "B01":
            lines.append(
                "B01 沿用既有服飾商品，"
                "不提供重複批量上架。"
            )

        elif store in TEXT_STORES:
            lines.append(
                "這間商店使用文字批量匯入。"
            )

        elif store == "B09":
            lines.append(
                "⚠️ 特殊憑證兌換功能尚未開放。"
            )

        elif store == "B10":
            lines.append(
                "🖤 商品限定有效黑卡會員購買。"
            )

        else:
            lines.append(
                "可使用下方按鈕批量上架或查詢。"
            )

    else:
        lines.append(
            "請從下拉選單選擇 B01～B10。"
        )

    lines.extend([
        "",
        "⚠️ 批量上架僅限群主。",
        "一般管理員可使用授權的商品編輯功能。",
    ])

    return "\n".join(lines)


# ==========================================
# 商店模組工具
# ==========================================

def get_store_module(code):
    if code not in STORES:
        raise ValueError(
            "無效的商店編號。"
        )

    return importlib.import_module(
        f"modules.shop_{code.lower()}"
    )


def auto_publish(guild_id, code):
    if code not in AUTO_STORES:
        raise ValueError(
            "這間商店不支援一鍵上架。"
        )

    module = get_store_module(code)

    init = getattr(
        module,
        "init_db",
        None,
    )

    if callable(init):
        init()

    publish = getattr(
        module,
        "import_catalog",
        None,
    )

    if not callable(publish):
        raise RuntimeError(
            f"{code} 尚未提供 import_catalog()。"
        )

    return publish(guild_id)


def query_store(guild_id, code):
    if code == "B01":
        # 直接查詢原商城資料，避免重複上架。
        from core.database import get_connection

        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM shop_products
                WHERE guild_id=?
                  AND active=1
                  AND (
                    category LIKE 'B01%'
                    OR category LIKE '%服飾%'
                  )
                """,
                (str(guild_id),),
            ).fetchone()

        return (
            "👗 B01 服飾精品店\n"
            f"目前啟用商品：{int(row[0])} 件"
        )

    module = get_store_module(code)

    init = getattr(
        module,
        "init_db",
        None,
    )

    if callable(init):
        init()

    # 不同商店原本使用不同函式名稱。
    for name in (
        "query_catalog",
        "catalog_status",
        "status",
    ):
        query = getattr(
            module,
            name,
            None,
        )

        if callable(query):
            return query(guild_id)

    raise RuntimeError(
        f"{code} 尚未提供上架查詢功能。"
    )


# ==========================================
# 商品次數管理表單
# ==========================================

class ItemUsesModal(discord.ui.Modal):

    def __init__(self, action):
        super().__init__(
            title=(
                "設定商品使用次數"
                if action == "set"
                else "查詢商品使用次數"
            )
        )

        self.action = action

        self.product_id = discord.ui.TextInput(
            label="商品編號",
            placeholder="輸入商城商品數字編號",
            required=True,
            max_length=12,
        )

        self.add_item(self.product_id)

        if action == "set":
            self.uses = discord.ui.TextInput(
                label="每件可使用次數",
                placeholder="例如：4",
                required=True,
                max_length=3,
            )

            self.add_item(self.uses)

    async def on_submit(self, interaction):
        if not permitted(interaction):
            await interaction.response.send_message(
                "⛔ 沒有商品管理權限。",
                ephemeral=True,
            )
            return

        try:
            product_id = int(
                self.product_id.value
            )

            if product_id <= 0:
                raise ValueError(
                    "商品編號必須大於零。"
                )

            if self.action == "set":
                uses = int(
                    self.uses.value
                )

                if not 1 <= uses <= 100:
                    raise ValueError(
                        "使用次數必須介於 1～100。"
                    )

        except ValueError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            from modules import item_admin

            if self.action == "set":
                result = await asyncio.to_thread(
                    item_admin.configure_product,
                    interaction.guild_id,
                    product_id,
                    uses,
                )

            else:
                result = await asyncio.to_thread(
                    item_admin.inspect_product,
                    interaction.guild_id,
                    product_id,
                )

        except Exception as exc:
            LOG.exception(
                "商品使用次數操作失敗"
            )

            result = f"⚠️ 操作失敗：{exc}"

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


# ==========================================
# 商品管理功能選單
# ==========================================

class ManageSelect(discord.ui.Select):

    def __init__(self):
        options = [
            discord.SelectOption(
                label="新增官方商品",
                value="add",
                emoji="➕",
            ),
            discord.SelectOption(
                label="修改／上下架商品",
                value="edit",
                emoji="📝",
            ),
            discord.SelectOption(
                label="設定商品使用次數",
                value="uses",
                emoji="⚙️",
            ),
            discord.SelectOption(
                label="查詢商品使用次數",
                value="inspect",
                emoji="🔎",
            ),
            discord.SelectOption(
                label="批量設定使用次數",
                value="batch",
                emoji="📦",
            ),
        ]

        super().__init__(
            placeholder="選擇商品管理功能",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction):
        if not permitted(interaction):
            await interaction.response.send_message(
                "⛔ 沒有商城管理權限。",
                ephemeral=True,
            )
            return

        action = self.values[0]

        try:
            if action in ("add", "edit"):
                from modules.official_center import (
                    OfficialProductModal,
                )

                modal = OfficialProductModal(action)

            elif action == "uses":
                modal = ItemUsesModal("set")

            elif action == "inspect":
                modal = ItemUsesModal("inspect")

            elif action == "batch":
                from modules.item_batch import BatchModal
                modal = BatchModal()

            else:
                raise ValueError(
                    "未知的管理操作。"
                )

            await interaction.response.send_modal(
                modal
            )

        except Exception as exc:
            LOG.exception(
                "商城管理表單無法開啟"
            )

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"⚠️ {exc}",
                    ephemeral=True,
                )


# ==========================================
# 商店選擇器
# ==========================================

class StoreSelect(discord.ui.Select):

    def __init__(self, current=""):
        options = [
            discord.SelectOption(
                label=label,
                value=code,
                default=(code == current),
            )
            for code, label in STORES.items()
        ]

        super().__init__(
            placeholder="選擇 B01～B10 商店",
            options=options,
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction):
        if not permitted(interaction):
            await interaction.response.send_message(
                "⛔ 沒有商城管理權限。",
                ephemeral=True,
            )
            return

        code = self.values[0]

        await interaction.response.edit_message(
            content=panel_text(code),
            view=ShopManageView(
                interaction,
                code,
            ),
        )


# ==========================================
# 商城管理主面板
# ==========================================

class ShopManageView(discord.ui.View):

    def __init__(self, interaction, store=""):
        super().__init__(
            timeout=900
        )

        self.owner_id = interaction.user.id
        self.guild_id = interaction.guild_id

        self.store = (
            store
            if store in STORES
            else ""
        )

        self.add_item(
            ManageSelect()
        )

        self.add_item(
            StoreSelect(self.store)
        )

        # 批量上架只給群主使用。
        self.publish_button.disabled = (
            not is_guild_owner(interaction)
            or self.store not in AUTO_STORES
        )

        self.query_button.disabled = (
            not bool(self.store)
        )

        self.text_import.disabled = (
            not is_guild_owner(interaction)
            or self.store not in TEXT_STORES
        )

    async def interaction_check(
        self,
        interaction,
    ):
        if (
            interaction.user.id != self.owner_id
            or interaction.guild_id != self.guild_id
            or not permitted(interaction)
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的商城管理面板。",
                ephemeral=True,
            )
            return False

        return True

    # --------------------------------------
    # 固定商品一鍵上架
    # --------------------------------------

    @discord.ui.button(
        label="📦 批量上架",
        style=discord.ButtonStyle.success,
        row=2,
    )
    async def publish_button(
        self,
        interaction,
        button,
    ):
        try:
            require_owner(interaction)

            if self.store not in AUTO_STORES:
                raise ValueError(
                    "請先選擇支援一鍵上架的商店。"
                )

        except (
            ValueError,
            PermissionError,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            result = await asyncio.to_thread(
                auto_publish,
                interaction.guild_id,
                self.store,
            )

        except Exception as exc:
            LOG.exception(
                "商城一鍵上架失敗：%s",
                self.store,
            )

            result = (
                f"⚠️ {self.store} 上架失敗：{exc}"
            )

        content = (
            panel_text(self.store)
            + "\n\n"
            + str(result)[:850]
        )

        await interaction.edit_original_response(
            content=content,
            view=ShopManageView(
                interaction,
                self.store,
            ),
        )

    # --------------------------------------
    # 查詢上架進度
    # --------------------------------------

    @discord.ui.button(
        label="🔎 查詢進度",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def query_button(
        self,
        interaction,
        button,
    ):
        if not self.store:
            await interaction.response.send_message(
                "⚠️ 請先選擇商店。",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            result = await asyncio.to_thread(
                query_store,
                interaction.guild_id,
                self.store,
            )

        except Exception as exc:
            LOG.exception(
                "商城進度查詢失敗：%s",
                self.store,
            )

            result = (
                f"⚠️ 查詢失敗：{exc}"
            )

        await interaction.edit_original_response(
            content=(
                panel_text(self.store)
                + "\n\n"
                + str(result)[:850]
            ),
            view=ShopManageView(
                interaction,
                self.store,
            ),
        )

    # --------------------------------------
    # B02／B04 文字匯入
    # --------------------------------------

    @discord.ui.button(
        label="📋 文字匯入",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def text_import(
        self,
        interaction,
        button,
    ):
        try:
            require_owner(interaction)

            if self.store == "B02":
                from modules.shop_b02 import B02Modal
                modal = B02Modal()

            elif self.store == "B04":
                from modules.shop_b04 import B04Modal
                modal = B04Modal()

            else:
                raise ValueError(
                    "只有 B02、B04 使用文字匯入。"
                )

            await interaction.response.send_modal(
                modal
            )

        except Exception as exc:
            LOG.exception(
                "商品文字匯入表單無法開啟"
            )

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"⚠️ {exc}",
                    ephemeral=True,
                )

    # --------------------------------------
    # 返回官方中心
    # --------------------------------------

    @discord.ui.button(
        label="↩️ 返回官方",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def back(
        self,
        interaction,
        button,
    ):
        from modules.official_center import OfficialHome

        await interaction.response.edit_message(
            content="🏛️ **官方人員管理中心**",
            view=OfficialHome(interaction),
        )


# ==========================================
# /官方 首頁入口按鈕
# ==========================================

class ShopManageButton(discord.ui.Button):

    def __init__(self):
        super().__init__(
            label="🛍️ 商城管理",
            style=discord.ButtonStyle.success,
            row=3,
        )

    async def callback(
        self,
        interaction,
    ):
        if not permitted(interaction):
            await interaction.response.send_message(
                "⛔ 沒有商城管理權限。",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=panel_text(),
            view=ShopManageView(interaction),
        )


# ==========================================
# 模組初始化
# ==========================================

async def setup(
    bot: commands.Bot,
):
    LOG.info(
        "B01～B10 官方商城統一管理面板已載入。"
    )
