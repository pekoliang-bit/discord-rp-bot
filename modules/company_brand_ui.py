"""品牌管理互動面板。

從 /公司 進入。
不另外建立獨立的 /品牌 指令。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import discord

from core.database import get_connection
from modules.world_terms import term

from modules.company_brand_progress import (
    BRAND_LEVELS,
    UPGRADE_REQUIREMENTS,
    brand_statistics,
)

from modules.company_sales_bridge import (
    bind_shop,
    list_brand_shops,
)


PAGE_SIZE = 25


def accessible_brands(guild_id, user_id):
    """查詢持有人及有品牌管理權限的品牌。"""

    guild_id = str(guild_id)
    user_id = str(user_id)

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT
                business_id,
                name,
                brand_tier,
                owner_id
            FROM rp_business_entities
            WHERE guild_id = ?
              AND kind = 'brand'
              AND status = 'active'
            ORDER BY name, business_id
            """,
            (guild_id,),
        ).fetchall()

        allowed = []

        for row in rows:

            if str(row["owner_id"]) == user_id:
                allowed.append(dict(row))
                continue

            managers = db.execute(
                """
                SELECT permissions
                FROM rp_business_managers
                WHERE guild_id = ?
                  AND business_id = ?
                  AND user_id = ?
                  AND status = 'active'
                """,
                (
                    guild_id,
                    row["business_id"],
                    user_id,
                ),
            ).fetchall()

            for manager in managers:

                try:
                    permissions = json.loads(
                        manager["permissions"]
                    )
                except (ValueError, TypeError):
                    permissions = []

                if (
                    isinstance(permissions, list)
                    and "brand" in permissions
                ) or (
                    isinstance(permissions, dict)
                    and permissions.get("brand") is True
                ):
                    allowed.append(dict(row))
                    break

        return allowed


def brand_details(guild_id, brand_id, user_id):

    brands = accessible_brands(
        guild_id,
        user_id,
    )

    permitted = {
        brand["business_id"]
        for brand in brands
    }

    if brand_id not in permitted:
        raise PermissionError(
            "你沒有這個品牌的管理權限。"
        )

    with get_connection() as db:

        brand = db.execute(
            """
            SELECT *
            FROM rp_business_entities
            WHERE guild_id = ?
              AND business_id = ?
              AND kind = 'brand'
              AND status = 'active'
            """,
            (
                str(guild_id),
                brand_id,
            ),
        ).fetchone()

        if not brand:
            raise ValueError("品牌不存在。")

        level = brand["brand_tier"]

        if level not in BRAND_LEVELS:
            raise ValueError("品牌等級資料不正確。")

        index = BRAND_LEVELS.index(level)

        if index < len(BRAND_LEVELS) - 1:
            next_level = BRAND_LEVELS[index + 1]
        else:
            next_level = None

        target_heat = (
            UPGRADE_REQUIREMENTS[next_level]["heat"]
            if next_level
            else UPGRADE_REQUIREMENTS["高奢"]["heat"]
        )

        statistics = brand_statistics(
            db,
            guild_id,
            brand_id,
            target_heat,
        )

        lines = [
            f"🏷️ **{brand['name']}**",
            f"品牌編號：`{brand_id}`",
            f"目前等級：**{level}**",
            f"主要產業：{brand['industry']}",
            f"所屬地區：{brand['region']}",
            "",
            "💰 **最近30天營業情況**",
            f"總銷售：{statistics['gross']:,} 金幣",
            f"退款：{statistics['refunds']:,} 金幣",
            f"淨營業額：{statistics['revenue']:,} 金幣",
            "",
            "🔥 **最近30天品牌熱度**",
        ]

        heat_names = {
            "consumption": "消費",
            "promotion": "宣傳",
            "cooperation": "商業合作",
            "representative": "代表曝光",
        }

        for source, name in heat_names.items():

            raw = statistics["raw_heat"].get(source, 0)

            counted = statistics[
                "effective_heat"
            ].get(source, 0)

            lines.append(
                f"{name}：{raw:,} "
                f"（計入 {counted:,}）"
            )

        lines.append(
            f"**有效熱度："
            f"{statistics['total_heat']:,}**"
        )

        if next_level:

            requirement = UPGRADE_REQUIREMENTS[next_level]

            remaining_heat = max(
                0,
                requirement["heat"]
                - statistics["total_heat"],
            )

            remaining_revenue = max(
                0,
                requirement["revenue"]
                - statistics["revenue"],
            )

            lines.extend([
                "",
                f"📈 **下一級：{next_level}**",
                f"目標熱度：{requirement['heat']:,}",
                f"還需熱度：{remaining_heat:,}",
                f"目標淨營業額："
                f"{requirement['revenue']:,} 金幣",
                f"還需營業額："
                f"{remaining_revenue:,} 金幣",
                "",
                "符合全部條件後，"
                "每日台灣時間00:00檢查免費升級。",
            ])

        else:
            lines.extend([
                "",
                "👑 已達品牌最高等級。",
            ])

        return "\n".join(lines)[:1900]


# ============================================================
# 店鋪綁定表單
# ============================================================

class BindShopModal(discord.ui.Modal):

    def __init__(self, guild_id, user_id, brand_id):

        super().__init__(
            title="綁定品牌旗下店鋪",
            timeout=600,
        )

        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.brand_id = brand_id

        self.shop_id = discord.ui.TextInput(
            label="個人店鋪編號",
            placeholder="例如：12",
            max_length=20,
        )

        self.add_item(self.shop_id)

    async def on_submit(self, interaction):

        if (
            str(interaction.user.id) != self.user_id
            or str(interaction.guild_id) != self.guild_id
        ):
            return await interaction.response.send_message(
                "這不是你的操作表單。",
                ephemeral=True,
            )

        try:
            shop_id = int(self.shop_id.value)

            if shop_id <= 0:
                raise ValueError

        except ValueError:
            return await interaction.response.send_message(
                "店鋪編號必須是正整數。",
                ephemeral=True,
            )

        await interaction.response.defer(
            ephemeral=True,
        )

        try:
            result = await asyncio.to_thread(
                bind_shop,
                self.guild_id,
                shop_id,
                self.brand_id,
                self.user_id,
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            result = f"⚠️ {exc}"

        await interaction.followup.send(
            result,
            ephemeral=True,
        )


# ============================================================
# 品牌操作面板
# ============================================================

class BrandDetailView(discord.ui.View):

    def __init__(
        self,
        guild_id,
        user_id,
        brand_id,
    ):

        super().__init__(timeout=600)

        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.brand_id = brand_id

    async def interaction_check(self, interaction):

        if (
            str(interaction.user.id) != self.user_id
            or str(interaction.guild_id) != self.guild_id
        ):
            await interaction.response.send_message(
                "這不是你的品牌管理面板。",
                ephemeral=True,
            )

            return False

        return True

    @discord.ui.button(
        label="🔄 更新品牌資料",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def refresh(self, interaction, button):

        try:
            result = await asyncio.to_thread(
                brand_details,
                self.guild_id,
                self.brand_id,
                self.user_id,
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            return await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

        await interaction.response.edit_message(
            content=result,
            view=self,
        )

    @discord.ui.button(
        label="🏪 旗下店鋪",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def shops(self, interaction, button):

        try:
            # 先核實當前使用者仍有管理權限。
            await asyncio.to_thread(
                brand_details,
                self.guild_id,
                self.brand_id,
                self.user_id,
            )

            shops = await asyncio.to_thread(
                list_brand_shops,
                self.guild_id,
                self.brand_id,
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            return await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

        if not shops:
            message = "目前沒有綁定的店鋪。"

        else:
            lines = ["🏪 **旗下店鋪**"]

            for shop in shops[:30]:
                lines.append(
                    f"#{shop['shop_id']} "
                    f"{shop['name']} "
                    f"｜{shop['category']}"
                    f"｜{shop['status']}"
                )

            message = "\n".join(lines)

        await interaction.response.send_message(
            message[:1900],
            ephemeral=True,
        )

    @discord.ui.button(
        label="🔗 綁定店鋪",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def bind(self, interaction, button):

        # 最終綁定仍由資料庫驗證持有人。
        await interaction.response.send_modal(
            BindShopModal(
                self.guild_id,
                self.user_id,
                self.brand_id,
            )
        )

    @discord.ui.button(
        label="↩️ 返回品牌選單",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(self, interaction, button):

        await interaction.response.edit_message(
            content="請選擇要管理的品牌。",
            view=BrandManagementView(
                self.guild_id,
                self.user_id,
            ),
        )


# ============================================================
# 品牌選擇
# ============================================================

class BrandSelect(discord.ui.Select):

    def __init__(self, parent):

        self.parent_view = parent

        brands = parent.brands

        start = parent.page * PAGE_SIZE
        end = start + PAGE_SIZE

        choices = brands[start:end]

        options = [
            discord.SelectOption(
                label=brand["name"][:100],
                value=brand["business_id"],
                description=(
                    f"{brand['brand_tier']}｜"
                    f"{brand['business_id']}"
                )[:100],
            )
            for brand in choices
        ]

        super().__init__(
            placeholder="選擇公司旗下品牌",
            options=options,
            row=0,
        )

    async def callback(self, interaction):

        brand_id = self.values[0]

        try:
            result = await asyncio.to_thread(
                brand_details,
                self.parent_view.guild_id,
                brand_id,
                self.parent_view.user_id,
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            return await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

        await interaction.response.edit_message(
            content=result,
            view=BrandDetailView(
                self.parent_view.guild_id,
                self.parent_view.user_id,
                brand_id,
            ),
        )


class BrandManagementView(discord.ui.View):

    def __init__(
        self,
        guild_id,
        user_id,
        page=0,
    ):

        super().__init__(timeout=600)

        self.guild_id = str(guild_id)
        self.user_id = str(user_id)

        self.brands = accessible_brands(
            self.guild_id,
            self.user_id,
        )

        max_page = max(
            0,
            (len(self.brands) - 1) // PAGE_SIZE,
        )

        self.page = max(
            0,
            min(page, max_page),
        )

        if self.brands:
            self.add_item(
                BrandSelect(self)
            )

    async def interaction_check(self, interaction):

        if (
            str(interaction.user.id) != self.user_id
            or str(interaction.guild_id) != self.guild_id
        ):
            await interaction.response.send_message(
                "這不是你的品牌選單。",
                ephemeral=True,
            )

            return False

        return True

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous(self, interaction, button):

        await interaction.response.edit_message(
            content="請選擇要管理的品牌。",
            view=BrandManagementView(
                self.guild_id,
                self.user_id,
                self.page - 1,
            ),
        )

    @discord.ui.button(
        label="➡️ 下一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next_page(self, interaction, button):

        await interaction.response.edit_message(
            content="請選擇要管理的品牌。",
            view=BrandManagementView(
                self.guild_id,
                self.user_id,
                self.page + 1,
            ),
        )


# ============================================================
# 供 /公司 面板使用的按鈕
# ============================================================

class BrandManagementButton(discord.ui.Button):

    def __init__(self):

        super().__init__(
            label="🏷️ 品牌管理",
            style=discord.ButtonStyle.primary,
            row=3,
        )

    async def callback(self, interaction):

        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)

        try:
            view = BrandManagementView(
                guild_id,
                user_id,
            )

        except sqlite3.Error:
            return await interaction.response.send_message(
                "品牌資料暫時無法讀取。",
                ephemeral=True,
            )

        if not view.brands:
            return await interaction.response.send_message(
                f"你目前沒有持有或獲授權管理的{term(guild_id, 'brand')}。",
                ephemeral=True,
            )

        await interaction.response.send_message(
            f"🏷️ **{term(guild_id, 'brand')}管理中心**\n"
            f"請選擇要管理的{term(guild_id, 'brand')}。",
            view=view,
            ephemeral=True,
        )


async def setup(bot):
    # 本模組的按鈕由 /公司 面板呼叫。
    pass
