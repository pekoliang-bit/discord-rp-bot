
"""官方商城管理選單。

此模組不建立斜線指令。
由 official_center.py 的 /官方 面板開啟。

集中：
- 官方新增、修改商品
- B02、B04 批量匯入
- 商品使用次數設定、查詢
- 商品次數批量設定

匯入功能仍沿用原模組的權限與驗證。
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

LOG = logging.getLogger(__name__)


def permitted(interaction):
    from core.permissions import Actor

    if interaction.guild is None:
        return False

    if interaction.user.id == interaction.guild.owner_id:
        return True

    return Actor.from_interaction(interaction).is_admin


class ShopSelect(discord.ui.Select):
    def __init__(self, owner_id, guild_id, is_owner):
        self.owner_id = owner_id
        self.guild_id = guild_id

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
                emoji="🔍",
            ),
            discord.SelectOption(
                label="批量設定使用次數",
                value="batch",
                emoji="📦",
            ),
        ]

        if is_owner:
            options.extend([
                discord.SelectOption(
                    label="匯入 B02 美妝保養",
                    value="b02",
                    emoji="💄",
                ),
                discord.SelectOption(
                    label="匯入 B04 數位科技",
                    value="b04",
                    emoji="📱",
                ),
            ])

        super().__init__(
            placeholder="選擇商城管理功能",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or interaction.guild_id != self.guild_id
            or not permitted(interaction)
        ):
            await interaction.response.send_message(
                "⛔ 沒有權限操作此選單。",
                ephemeral=True,
            )
            return

        action = self.values[0]

        is_owner = (
            interaction.user.id
            == interaction.guild.owner_id
        )

        if action in ("b02", "b04") and not is_owner:
            await interaction.response.send_message(
                "⛔ 商品批量上架僅限群主。",
                ephemeral=True,
            )
            return

        try:
            if action in ("add", "edit"):
                from modules.official_center import (
                    OfficialProductModal,
                )

                modal = OfficialProductModal(action)

            elif action == "b02":
                from modules.shop_b02 import B02Modal
                modal = B02Modal()

            elif action == "b04":
                from modules.shop_b04 import B04Modal
                modal = B04Modal()

            elif action == "batch":
                from modules.item_batch import BatchModal
                modal = BatchModal()

            elif action == "uses":
                modal = ItemUsesModal("set")

            elif action == "inspect":
                modal = ItemUsesModal("inspect")

            else:
                raise ValueError("未知的商城管理功能。")

        except (ImportError, AttributeError, ValueError) as exc:
            LOG.exception("商城管理表單無法開啟")

            await interaction.response.send_message(
                f"⚠️ 功能暫時無法開啟：{exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(modal)


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
            placeholder="請輸入商城顯示的數字編號",
            required=True,
            max_length=12,
        )
        self.add_item(self.product_id)

        if action == "set":
            self.uses = discord.ui.TextInput(
                label="每件商品可使用次數",
                placeholder="B02：2～7／B04：3～6",
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
            product_id = int(self.product_id.value)

            if product_id <= 0:
                raise ValueError("商品編號必須大於零。")

            if self.action == "set":
                uses = int(self.uses.value)

                if not 1 <= uses <= 100:
                    raise ValueError("使用次數不合法。")

        except ValueError as exc:
            await interaction.response.send_message(
                f"⚠️ 輸入錯誤：{exc}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            from modules import item_admin
            import asyncio

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

        except (
            ValueError,
            RuntimeError,
            Exception,
        ) as exc:
            LOG.exception("商城使用次數操作失敗")
            result = f"⚠️ 操作失敗：{exc}"

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


class ShopManageView(discord.ui.View):
    def __init__(self, interaction):
        super().__init__(timeout=600)

        self.owner_id = interaction.user.id
        self.guild_id = interaction.guild_id

        self.add_item(
            ShopSelect(
                self.owner_id,
                self.guild_id,
                interaction.user.id
                == interaction.guild.owner_id,
            )
        )

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.owner_id
            or interaction.guild_id != self.guild_id
            or not permitted(interaction)
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的商城管理選單。",
                ephemeral=True,
            )
            return False

        return True


class ShopManageButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🛍️ 商城管理",
            style=discord.ButtonStyle.success,
            row=3,
        )

    async def callback(self, interaction):
        if not permitted(interaction):
            await interaction.response.send_message(
                "⛔ 你沒有商城管理權限。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            (
                "🛍️ **官方商城管理中心**\n\n"
                "請選擇商品管理功能。\n"
                "包含商品新增、修改、"
                "使用次數及批量匯入。\n\n"
                "B02、B04 批量上架僅限群主。\n"
                "私人商店商品不使用此入口。"
            ),
            view=ShopManageView(interaction),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    LOG.info(
        "官方商城管理面板元件已載入。"
    )
