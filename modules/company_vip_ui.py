"""公司 VIP 管理介面。

整合入口：/公司 → VIP 管理

不新增獨立 Slash Command。
"""

from __future__ import annotations

import asyncio
import sqlite3

import discord

from core.database import get_connection
from modules.company_business import has_permission
from modules.company_vip import (
    check_free_item,
    get_vip_member,
    initialize_vip,
    list_vip_tiers,
    reserve_free_item,
    set_vip_tier,
)


def manageable_vip_businesses(guild_id, user_id):
    initialize_vip()

    with get_connection() as db:
        rows = db.execute("""
            SELECT business_id, name, kind, owner_id
            FROM rp_business_entities
            WHERE guild_id = ?
              AND status = 'active'
            ORDER BY name
        """, (str(guild_id),)).fetchall()

        businesses = []

        for row in rows:
            if (
                row["owner_id"] == str(user_id)
                or has_permission(
                    db,
                    str(guild_id),
                    row["business_id"],
                    str(user_id),
                    "vip",
                )
            ):
                businesses.append({
                    "business_id": row["business_id"],
                    "name": row["name"],
                    "kind": row["kind"],
                })

        return businesses


def vip_overview(guild_id, business_id):
    tiers = list_vip_tiers(guild_id, business_id)

    with get_connection() as db:
        entity = db.execute("""
            SELECT name
            FROM rp_business_entities
            WHERE guild_id = ?
              AND business_id = ?
        """, (
            str(guild_id),
            str(business_id),
        )).fetchone()

        count = db.execute("""
            SELECT COUNT(*) AS amount
            FROM rp_company_vip_members
            WHERE guild_id = ?
              AND business_id = ?
        """, (
            str(guild_id),
            str(business_id),
        )).fetchone()["amount"]

    title = entity["name"] if entity else business_id

    lines = [
        f"💎 **{title}｜VIP 管理**",
        f"公司編號：`{business_id}`",
        f"會員總數：{count:,} 人",
        "",
        "**目前等級設定**",
    ]

    if not tiers:
        lines.append("尚未設定 VIP 等級。")
    else:
        for tier in tiers:
            discount = tier["discount_percent"]

            free_item = (
                "｜每 30 天免費商品"
                if tier["free_item_enabled"]
                else ""
            )

            lines.append(
                f"LV.{tier['level']} {tier['title']}"
                f"｜累積 {tier['required_spending']:,} 金幣"
                f"｜折扣 {discount}%"
                f"{free_item}"
            )

    lines.extend([
        "",
        "VIP 會員不額外增加品牌熱度。",
        "折扣最高 50%；每級會員人數不限。",
    ])

    return "\n".join(lines)[:1900]


async def send_result(interaction, action):
    await interaction.response.defer(ephemeral=True)

    try:
        result = await asyncio.to_thread(action)
    except (
        ValueError,
        PermissionError,
        sqlite3.Error,
    ) as exc:
        result = f"⚠️ {exc}"
    except Exception:
        result = "⚠️ 操作失敗，請聯絡管理員確認紀錄。"

    await interaction.followup.send(
        str(result)[:1900],
        ephemeral=True,
    )


class VIPOwnedModal(discord.ui.Modal):
    def __init__(self, user_id, guild_id, business_id, title):
        super().__init__(title=title, timeout=600)

        self.user_id = str(user_id)
        self.guild_id = str(guild_id)
        self.business_id = str(business_id)

    async def authorized_submit(self, interaction, action):
        if (
            str(interaction.user.id) != self.user_id
            or str(interaction.guild_id) != self.guild_id
        ):
            return await interaction.response.send_message(
                "這不是你的操作表單。",
                ephemeral=True,
            )

        await send_result(interaction, action)


class SetVIPTierModal(VIPOwnedModal):
    def __init__(self, user_id, guild_id, business_id):
        super().__init__(
            user_id,
            guild_id,
            business_id,
            "設定 VIP 等級",
        )

        self.level = discord.ui.TextInput(
            label="VIP 等級（1～20）",
            placeholder="例如：1",
            max_length=2,
        )

        self.title_input = discord.ui.TextInput(
            label="VIP 稱號",
            placeholder="例如：銀卡會員",
            max_length=60,
        )

        self.spending = discord.ui.TextInput(
            label="累積消費門檻（金幣）",
            placeholder="例如：100000",
            max_length=15,
        )

        self.options_input = discord.ui.TextInput(
            label="折扣百分比,是否有免費商品",
            placeholder="例如：10,是",
            max_length=20,
        )

        self.benefits = discord.ui.TextInput(
            label="其他會員福利（選填）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )

        for item in (
            self.level,
            self.title_input,
            self.spending,
            self.options_input,
            self.benefits,
        ):
            self.add_item(item)

    async def on_submit(self, interaction):
        try:
            level = int(self.level.value)
            spending = int(self.spending.value)

            parts = [
                part.strip()
                for part in self.options_input.value.split(",")
            ]

            if len(parts) != 2:
                raise ValueError

            discount = int(parts[0])

            if parts[1] not in ("是", "否"):
                raise ValueError

            free_item = parts[1] == "是"

        except ValueError:
            return await interaction.response.send_message(
                "格式錯誤。請參考：10,是\n"
                "等級、金額與折扣必須是整數。",
                ephemeral=True,
            )

        await self.authorized_submit(
            interaction,
            lambda: set_vip_tier(
                self.guild_id,
                self.business_id,
                self.user_id,
                level,
                str(self.title_input.value),
                spending,
                discount,
                free_item,
                str(self.benefits.value or ""),
            ),
        )


class VIPMemberLookupModal(VIPOwnedModal):
    def __init__(self, user_id, guild_id, business_id):
        super().__init__(
            user_id,
            guild_id,
            business_id,
            "查詢 VIP 會員",
        )

        self.character_id = discord.ui.TextInput(
            label="會員角色編號",
            placeholder="例如：123",
            max_length=20,
        )

        self.add_item(self.character_id)

    async def on_submit(self, interaction):
        try:
            character_id = int(self.character_id.value)
        except ValueError:
            return await interaction.response.send_message(
                "角色編號必須是數字。",
                ephemeral=True,
            )

        def action():
            member = get_vip_member(
                self.guild_id,
                self.business_id,
                character_id,
            )

            if not member:
                return "目前沒有這名角色的 VIP 消費紀錄。"

            available, free_text = check_free_item(
                self.guild_id,
                self.business_id,
                character_id,
            )

            return (
                f"💎 **VIP 會員資料**\n"
                f"角色編號：#{character_id}\n"
                f"等級：LV.{member['level']}"
                f"｜{member['title']}\n"
                f"有效累積消費："
                f"{member['lifetime_spending']:,} 金幣\n"
                f"折扣：{member['discount_percent']}%\n"
                f"免費商品資格：{free_text}\n"
                f"其他福利：{member['benefits'] or '無'}"
            )

        await self.authorized_submit(
            interaction,
            action,
        )


class VIPFreeItemModal(VIPOwnedModal):
    def __init__(self, user_id, guild_id, business_id):
        super().__init__(
            user_id,
            guild_id,
            business_id,
            "登記 VIP 免費商品",
        )

        self.character_id = discord.ui.TextInput(
            label="領取者角色編號",
            max_length=20,
        )

        self.product = discord.ui.TextInput(
            label="商品編號",
            placeholder="填入商品管理中的正式編號",
            max_length=100,
        )

        self.confirm = discord.ui.TextInput(
            label="輸入「確認登記」",
            max_length=10,
        )

        for item in (
            self.character_id,
            self.product,
            self.confirm,
        ):
            self.add_item(item)

    async def on_submit(self, interaction):
        if self.confirm.value.strip() != "確認登記":
            return await interaction.response.send_message(
                "未確認登記，沒有變更任何資料。",
                ephemeral=True,
            )

        try:
            character_id = int(self.character_id.value)
        except ValueError:
            return await interaction.response.send_message(
                "角色編號必須是數字。",
                ephemeral=True,
            )

        await self.authorized_submit(
            interaction,
            lambda: reserve_free_item(
                self.guild_id,
                self.business_id,
                character_id,
                self.user_id,
                str(self.product.value).strip(),
            ),
        )


class VIPDetailView(discord.ui.View):
    def __init__(self, user_id, guild_id, business_id):
        super().__init__(timeout=600)

        self.user_id = str(user_id)
        self.guild_id = str(guild_id)
        self.business_id = str(business_id)

    async def interaction_check(self, interaction):
        if (
            str(interaction.user.id) != self.user_id
            or str(interaction.guild_id) != self.guild_id
        ):
            await interaction.response.send_message(
                "這不是你的 VIP 管理面板。",
                ephemeral=True,
            )
            return False

        allowed = await asyncio.to_thread(
            manageable_vip_businesses,
            self.guild_id,
            self.user_id,
        )

        if self.business_id not in {
            item["business_id"] for item in allowed
        }:
            await interaction.response.send_message(
                "你已失去這間公司的 VIP 管理權限。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="⚙️ 設定會員等級",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def set_tier(self, interaction, button):
        await interaction.response.send_modal(
            SetVIPTierModal(
                self.user_id,
                self.guild_id,
                self.business_id,
            )
        )

    @discord.ui.button(
        label="🔎 查詢會員",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def lookup(self, interaction, button):
        await interaction.response.send_modal(
            VIPMemberLookupModal(
                self.user_id,
                self.guild_id,
                self.business_id,
            )
        )

    @discord.ui.button(
        label="🎁 登記免費商品",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def free_item(self, interaction, button):
        await interaction.response.send_modal(
            VIPFreeItemModal(
                self.user_id,
                self.guild_id,
                self.business_id,
            )
        )

    @discord.ui.button(
        label="🔄 更新資料",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def refresh(self, interaction, button):
        result = await asyncio.to_thread(
            vip_overview,
            self.guild_id,
            self.business_id,
        )

        await interaction.response.edit_message(
            content=result,
            view=self,
        )

    @discord.ui.button(
        label="↩️ 返回公司列表",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def back(self, interaction, button):
        businesses = await asyncio.to_thread(
            manageable_vip_businesses,
            self.guild_id,
            self.user_id,
        )

        await interaction.response.edit_message(
            content="💎 **公司 VIP 管理中心**\n請選擇公司。",
            view=VIPBusinessSelectView(
                self.user_id,
                self.guild_id,
                businesses,
            ),
        )


class VIPBusinessSelect(discord.ui.Select):
    def __init__(self, businesses):
        options = [
            discord.SelectOption(
                label=item["name"][:100],
                value=item["business_id"],
                description=(
                    f"{item['kind']}｜{item['business_id']}"
                )[:100],
            )
            for item in businesses[:25]
        ]

        super().__init__(
            placeholder="選擇要管理 VIP 的公司或品牌",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        parent = self.view
        business_id = self.values[0]

        allowed = await asyncio.to_thread(
            manageable_vip_businesses,
            parent.guild_id,
            parent.user_id,
        )

        if business_id not in {
            item["business_id"] for item in allowed
        }:
            return await interaction.response.send_message(
                "你沒有這間公司的 VIP 管理權限。",
                ephemeral=True,
            )

        content = await asyncio.to_thread(
            vip_overview,
            parent.guild_id,
            business_id,
        )

        await interaction.response.edit_message(
            content=content,
            view=VIPDetailView(
                parent.user_id,
                parent.guild_id,
                business_id,
            ),
        )


class VIPBusinessSelectView(discord.ui.View):
    def __init__(self, user_id, guild_id, businesses):
        super().__init__(timeout=600)

        self.user_id = str(user_id)
        self.guild_id = str(guild_id)

        if businesses:
            self.add_item(
                VIPBusinessSelect(businesses)
            )

    async def interaction_check(self, interaction):
        if (
            str(interaction.user.id) != self.user_id
            or str(interaction.guild_id) != self.guild_id
        ):
            await interaction.response.send_message(
                "這不是你的公司介面。",
                ephemeral=True,
            )
            return False

        return True


class CompanyVIPButton(discord.ui.Button):
    """供既有 /公司 面板掛載的 VIP 入口按鈕。"""

    def __init__(self):
        super().__init__(
            label="💎 VIP 管理",
            style=discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction):
        businesses = await asyncio.to_thread(
            manageable_vip_businesses,
            str(interaction.guild_id),
            str(interaction.user.id),
        )

        if not businesses:
            return await interaction.response.send_message(
                "目前沒有可管理 VIP 的公司或品牌。",
                ephemeral=True,
            )

        await interaction.response.send_message(
            "💎 **公司 VIP 管理中心**\n請選擇公司。",
            view=VIPBusinessSelectView(
                str(interaction.user.id),
                str(interaction.guild_id),
                businesses,
            ),
            ephemeral=True,
        )


async def setup(bot):
    initialize_vip()
