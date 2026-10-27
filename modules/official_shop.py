

"""Discord RP｜官方商城統一管理中心。

檔案：modules/official_shop.py

功能：
- B01～B10 官方商店管理
- 新增、修改、上下架商品
- 商品使用次數設定及查詢
- 商品使用次數批量匯入
- B02、B04 文字批量匯入
- B03、B05～B10 一鍵上架
- 各商店商品登記進度查詢
- 商店位置與 Discord 頻道連結管理
- 查看目前商店販售位置
- 返回 /官方 管理功能中心
- 群主最高權限
- 管理員商品管理權限
- 私人管理面板防護

不建立第二個 /商城 或 /官方。
不刪除商品、背包、角色或銀行資料。
"""

from __future__ import annotations

import asyncio
import importlib
import logging

import discord
from discord.ext import commands

from core.permissions import Actor


LOG = logging.getLogger(__name__)

TIMEOUT = 900


# ==========================================
# 官方商店
# ==========================================

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

AUTO_STORES = {
    "B03",
    "B05",
    "B06",
    "B07",
    "B08",
    "B09",
    "B10",
}

TEXT_STORES = {
    "B02",
    "B04",
}


# ==========================================
# 權限
# ==========================================

def is_guild_owner(interaction):
    return bool(
        interaction.guild is not None
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
            "此操作僅限伺服器群主。"
        )


def require_permitted(interaction):
    if not permitted(interaction):
        raise PermissionError(
            "沒有官方商城管理權限。"
        )


# ==========================================
# 商店位置查詢
# ==========================================

def get_store_placement(guild_id, code):
    """讀取商店目前的位置設定。"""

    if not code or code not in STORES:
        return "mall", ""

    from modules.shop_location import get_location

    return get_location(
        guild_id,
        code,
    )


def placement_text(guild_id, code):
    """管理面板顯示目前販售位置。"""

    if code not in STORES:
        return "尚未選擇商店。"

    try:
        placement, url = get_store_placement(
            guild_id,
            code,
        )

    except Exception:
        LOG.exception(
            "商店位置查詢失敗：%s",
            code,
        )

        return "⚠️ 無法讀取販售位置。"

    if placement == "channel":
        return (
            "📍 **目前販售位置：其他頻道**\n"
            f"🔗 商店連結：{url or '尚未設定'}"
        )

    return (
        "📍 **目前販售位置：官方商城內**\n"
        "玩家可從 `/商城` 進入。"
    )


# ==========================================
# 商城管理首頁
# ==========================================

def panel_text(store="", guild_id=None):
    lines = [
        "🛍️ **官方商城管理中心**",
        "",
        "請選擇商品管理功能，"
        "或從下方選擇 B01～B10 商店。",
        "",
        "📦 固定商品：可直接批量上架",
        "📋 B02、B04：使用文字匯入",
        "⚙️ 可設定商品使用次數",
        "📍 可設定商店販售位置與連結",
        "🔎 可查詢商品登記進度",
    ]

    if store and store in STORES:
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━",
            f"**目前選擇：{store}**",
            f"{STORES[store]}",
            "",
        ])

        if guild_id is not None:
            lines.append(
                placement_text(
                    guild_id,
                    store,
                )
            )

        if store == "B01":
            lines.extend([
                "",
                "👗 B01 沿用既有服飾商品。",
                "不提供重複批量上架。",
            ])

        elif store in TEXT_STORES:
            lines.extend([
                "",
                "📋 本商店使用文字批量匯入。",
            ])

        elif store == "B09":
            lines.extend([
                "",
                "🎁 特殊道具可另由"
                "兌換審核系統處理。",
            ])

        elif store == "B10":
            lines.extend([
                "",
                "🖤 此商店限定有效黑卡會員購買。",
            ])

        else:
            lines.extend([
                "",
                "📦 可使用批量上架功能。",
            ])

    else:
        lines.extend([
            "",
            "請從商店下拉選單選擇 B01～B10。",
        ])

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━",
        "👑 批量上架及販售位置設定僅限群主。",
        "🛡️ 授權管理員可以管理一般商品。",
    ])

    return "\n".join(lines)[:1900]


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
    if code not in STORES:
        raise ValueError(
            "商店編號不正確。"
        )

    if code == "B01":
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
                (
                    str(guild_id),
                ),
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
# 商品使用次數表單
# ==========================================

class ItemUsesModal(discord.ui.Modal):

    def __init__(self, action):
        super().__init__(
            title=(
                "設定商品使用次數"
                if action == "set"
                else "查詢商品使用次數"
            ),
            timeout=300,
        )

        self.action = action

        self.product_id = discord.ui.TextInput(
            label="商品編號",
            placeholder="輸入商城商品數字編號",
            required=True,
            max_length=12,
        )

        self.add_item(
            self.product_id
        )

        if action == "set":
            self.uses = discord.ui.TextInput(
                label="每件可使用次數",
                placeholder="例如：4",
                required=True,
                max_length=3,
            )

            self.add_item(
                self.uses
            )

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
                    "未知的商品管理操作。"
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
            content=panel_text(
                code,
                interaction.guild_id,
            ),
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
            timeout=TIMEOUT
        )

        self.owner_id = (
            interaction.user.id
        )

        self.guild_id = (
            interaction.guild_id
        )

        self.store = (
            store
            if store in STORES
            else ""
        )

        self.add_item(
            ManageSelect()
        )

        self.add_item(
            StoreSelect(
                self.store
            )
        )

        # ----------------------------------
        # 商店販售位置設定
        # ----------------------------------

        from modules.shop_location import (
            LocationButton,
        )

        self.add_item(
            LocationButton(
                self.store
            )
        )

        # ----------------------------------
        # 其他按鈕狀態
        # ----------------------------------

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

        # 販售位置按鈕只有選店後可用，
        # 實際權限由 shop_location 再驗證。

    async def interaction_check(
        self,
        interaction,
    ):
        if (
            interaction.user.id
            != self.owner_id
            or interaction.guild_id
            != self.guild_id
            or not permitted(interaction)
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的商城管理面板。",
                ephemeral=True,
            )
            return False

        return True

    # ======================================
    # 批量上架
    # ======================================

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
            require_owner(
                interaction
            )

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
            panel_text(
                self.store,
                interaction.guild_id,
            )
            + "\n\n"
            + str(result)[:700]
        )

        await interaction.edit_original_response(
            content=content[:1900],
            view=ShopManageView(
                interaction,
                self.store,
            ),
        )

    # ======================================
    # 查詢商品進度
    # ======================================

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
                panel_text(
                    self.store,
                    interaction.guild_id,
                )
                + "\n\n"
                + str(result)[:700]
            )[:1900],
            view=ShopManageView(
                interaction,
                self.store,
            ),
        )

    # ======================================
    # B02／B04 商品文字匯入
    # ======================================

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
            require_owner(
                interaction
            )

            if self.store == "B02":
                from modules.shop_b02 import B02Modal
                modal = B02Modal()

            elif self.store == "B04":
                from modules.shop_b04 import B04Modal
                modal = B04Modal()

            else:
                raise ValueError(
                    "只有 B02、B04 可以文字匯入。"
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

    # ======================================
    # 重新整理管理面板
    # ======================================

    @discord.ui.button(
        label="🔄 重新整理",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def refresh(
        self,
        interaction,
        button,
    ):
        await interaction.response.edit_message(
            content=panel_text(
                self.store,
                interaction.guild_id,
            ),
            view=ShopManageView(
                interaction,
                self.store,
            ),
        )

    # ======================================
    # 返回官方管理功能中心
    # ======================================

    @discord.ui.button(
        label="⬅️ 返回管理中心",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def back(
        self,
        interaction,
        button,
    ):
        from modules.official_center import (
            AdminToolsView,
            management_text,
        )

        view = AdminToolsView(
            interaction
        )

        await interaction.response.edit_message(
            content=management_text(view),
            view=view,
        )


# ==========================================
# 官方首頁入口按鈕
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
            content=panel_text(
                guild_id=interaction.guild_id,
            ),
            view=ShopManageView(
                interaction
            ),
        )


# ==========================================
# 模組初始化
# ==========================================

async def setup(bot: commands.Bot):
    LOG.info(
        "官方商城管理中心已載入："
        "B01～B10、批量上架、商品次數、"
        "商店位置與頻道連結管理。"
    )
