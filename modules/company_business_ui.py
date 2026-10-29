"""公司／品牌／集團申請介面，供原本的玩家申請中心呼叫。"""

from __future__ import annotations

import asyncio

import discord

from modules.world_terms import organization_terms

from modules.company_business import (
    COMPANY_CAPITAL,
    BRAND_CAPITAL,
    apply_business,
    accept_brand_rep,
)


class BrandRepresentativeAcceptModal(
    discord.ui.Modal,
    title="接受品牌代表指定",
):
    def __init__(self, actor, character_id):
        super().__init__()

        self.actor = actor
        self.character_id = character_id

        self.application_id = discord.ui.TextInput(
            label="品牌申請編號",
            max_length=20,
        )
        self.add_item(self.application_id)

    async def on_submit(self, interaction):
        if (
            str(interaction.user.id) != str(self.actor.user_id)
            or str(interaction.guild_id) != str(self.actor.guild_id)
        ):
            return await interaction.response.send_message(
                "這不是你的確認表單。",
                ephemeral=True,
            )

        try:
            result = await asyncio.to_thread(
                accept_brand_rep,
                self.actor.guild_id,
                int(self.application_id.value),
                self.actor.user_id,
                self.character_id,
            )
        except (ValueError, PermissionError) as exc:
            return await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

        await interaction.response.send_message(
            result,
            ephemeral=True,
        )


class BusinessApplyModal(discord.ui.Modal):
    def __init__(self, actor, character_id, kind, category):
        titles = {
            "company": "創立公司",
            "brand": "創立品牌",
            "group": "直接申請集團",
        }

        labels = organization_terms(actor.guild_id)
        names = {"company": labels["company"], "brand": labels["brand"], "group": labels["group"]}
        title = {"company": "創立", "brand": "創立", "group": "申請"}[kind] + names[kind]
        super().__init__(title=title[:45])

        self.actor = actor
        self.character_id = character_id
        self.kind = kind
        self.category = category

        self.business_name = discord.ui.TextInput(
            label="名稱",
            max_length=100,
        )

        self.industry = discord.ui.TextInput(
            label="經營產業／主要品類",
            max_length=100,
        )

        self.description = discord.ui.TextInput(
            label="經營內容及介紹",
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )

        self.region = discord.ui.TextInput(
            label="官方地區名稱",
            max_length=100,
        )

        for field in (
            self.business_name,
            self.industry,
            self.description,
            self.region,
        ):
            self.add_item(field)

        self.extra = None

        if kind == "brand":
            self.extra = discord.ui.TextInput(
                label="品牌附加：代表編號,母公司編號,出資公司編號",
                required=False,
                max_length=180,
                placeholder="例如 123,COM-000009,COM-000009（可留空）",
            )
            self.add_item(self.extra)

    async def on_submit(self, interaction):
        if (
            str(interaction.user.id) != str(self.actor.user_id)
            or str(interaction.guild_id) != str(self.actor.guild_id)
        ):
            return await interaction.response.send_message(
                "這不是你的申請。",
                ephemeral=True,
            )

        # 玩家只能選擇官方已建立的地區類別。
        region_name = str(self.region.value).strip()

        if (
            not interaction.guild
            or not any(
                category.name == region_name
                for category in interaction.guild.categories
            )
        ):
            return await interaction.response.send_message(
                "找不到這個官方地區類別，請填入已存在的類別名稱。",
                ephemeral=True,
            )

        representative_id = None
        parent_company = None
        funding_source = "character"

        if self.extra and self.extra.value:
            items = [
                item.strip()
                for item in str(self.extra.value).split(",")
            ]

            if len(items) > 3:
                return await interaction.response.send_message(
                    "附加資料最多三段，以逗號隔開。",
                    ephemeral=True,
                )

            items += [""] * (3 - len(items))

            try:
                representative_id = (
                    int(items[0]) if items[0] else None
                )
            except ValueError:
                return await interaction.response.send_message(
                    "代表角色編號需為數字。",
                    ephemeral=True,
                )

            parent_company = items[1] or None
            funding_source = items[2] or "character"

        await interaction.response.defer(ephemeral=True)

        try:
            application_number, capital = await asyncio.to_thread(
                apply_business,
                self.actor.guild_id,
                self.actor.user_id,
                self.character_id,
                self.kind,
                str(self.business_name.value),
                self.category,
                str(self.industry.value),
                str(self.description.value),
                region_name,
                parent_company,
                representative_id,
                funding_source,
            )

        except (ValueError, PermissionError) as exc:
            return await interaction.followup.send(
                f"⚠️ {exc}",
                ephemeral=True,
            )

        await interaction.followup.send(
            f"✅ 商業申請 #{application_number} 已保存，等待官方審核。\n"
            f"核准後才會扣除 {capital:,} 金幣並核發正式編號。\n"
            f"目前尚未扣款或成立{organization_terms(self.actor.guild_id)['company']}。",
            ephemeral=True,
        )


class BusinessCategorySelect(discord.ui.Select):
    def __init__(self, actor, character_id, kind):
        self.actor = actor
        self.character_id = character_id
        self.kind = kind

        if kind == "company":
            choices = COMPANY_CAPITAL
        elif kind == "brand":
            choices = BRAND_CAPITAL
        else:
            choices = {"直接申請集團": 10_000_000}

        labels = organization_terms(actor.guild_id)
        options = [
            discord.SelectOption(
                label=(f"直接申請{labels['group']}" if kind == "group" else name)[:100],
                description=f"啟動資金 {capital:,} {labels['currency']}",
                value=name,
            )
            for name, capital in choices.items()
        ]

        super().__init__(
            placeholder="選擇類型／初始等級",
            options=options,
        )

    async def callback(self, interaction):
        if (
            str(interaction.user.id) != str(self.actor.user_id)
            or str(interaction.guild_id) != str(self.actor.guild_id)
        ):
            return await interaction.response.send_message(
                "這不是你的申請。",
                ephemeral=True,
            )

        await interaction.response.send_modal(
            BusinessApplyModal(
                self.actor,
                self.character_id,
                self.kind,
                self.values[0],
            )
        )


class BusinessCategoryView(discord.ui.View):
    def __init__(self, actor, character_id, kind):
        super().__init__(timeout=600)

        self.add_item(
            BusinessCategorySelect(
                actor,
                character_id,
                kind,
            )
        )


class BusinessKindView(discord.ui.View):
    def __init__(self, actor, character_id):
        super().__init__(timeout=600)

        self.actor = actor
        self.character_id = character_id
        labels = organization_terms(actor.guild_id)
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.label == "創立公司":
                    item.label = f"創立{labels['company']}"[:80]
                elif item.label == "創立品牌":
                    item.label = f"創立{labels['brand']}"[:80]
                elif item.label == "直接申請集團":
                    item.label = f"直接申請{labels['group']}"[:80]

    async def interaction_check(self, interaction):
        if (
            str(interaction.user.id) != str(self.actor.user_id)
            or str(interaction.guild_id) != str(self.actor.guild_id)
        ):
            await interaction.response.send_message(
                "這不是你的申請選單。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="創立公司",
        style=discord.ButtonStyle.primary,
    )
    async def company(self, interaction, button):
        await interaction.response.send_message(
            f"選擇{organization_terms(self.actor.guild_id)['company']}類型",
            view=BusinessCategoryView(
                self.actor,
                self.character_id,
                "company",
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="創立品牌",
        style=discord.ButtonStyle.primary,
    )
    async def brand(self, interaction, button):
        await interaction.response.send_message(
            f"選擇{organization_terms(self.actor.guild_id)['brand']}初始等級",
            view=BusinessCategoryView(
                self.actor,
                self.character_id,
                "brand",
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="直接申請集團",
        style=discord.ButtonStyle.secondary,
    )
    async def group(self, interaction, button):
        await interaction.response.send_message(
            f"確認{organization_terms(self.actor.guild_id)['group']}啟動資金",
            view=BusinessCategoryView(
                self.actor,
                self.character_id,
                "group",
            ),
            ephemeral=True,
        )


async def setup(bot):
    # 由原本的玩家申請中心呼叫，不另行建立衝突指令。
    pass
