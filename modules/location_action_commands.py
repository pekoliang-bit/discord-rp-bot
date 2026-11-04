
"""Discord RP /行動：場所頻道辨識與行動預覽。

安全預覽版本：
- 自動辨識夜生活、生活休閒及培訓學習區。
- 支援打工、活動、課程三種類別。
- 群主可預覽待審核行動。
- 玩家只能預覽已核准行動。
- 尚未開啟實際交易、行動值扣除及獎勵發放。
- 不新增其他頂層 Slash 指令。
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from modules import (
    action_location_defaults,
    leisure_location_defaults,
    training_location_defaults,
)
from modules.action_reward_rules import validate_action


LOG = logging.getLogger(__name__)

PROVIDERS = (
    action_location_defaults,
    leisure_location_defaults,
    training_location_defaults,
)


def resolve_location(guild_id, channel_id):
    """根據伺服器及頻道 ID 尋找地點。"""
    for provider in PROVIDERS:
        location = provider.lookup_channel(
            guild_id,
            channel_id,
        )

        if location is not None:
            return location, provider

    return None, None


def describe_action(action):
    """產生行動效果的顯示文字。"""
    parts = []

    reward = int(action.get("reward_coins", 0))
    cost = int(action.get("cost_coins", 0))

    if reward:
        parts.append(f"金幣 +{reward:,}")

    if cost:
        parts.append(f"花費 {cost:,} 金幣")

    attribute = action.get("attribute")
    increase = int(action.get("attribute_increase", 0))

    if attribute and increase:
        parts.append(f"{attribute} +{increase}")

    for pool, amount in action.get(
        "point_rewards", {}
    ).items():
        if amount:
            parts.append(f"{pool} +{amount}")

    health = int(action.get("health_delta", 0))

    if health:
        parts.append(f"健康 {health:+d}")

    ap = int(action.get("action_points_cost", 0))
    parts.append(f"行動值 -{ap}")

    return "｜".join(parts)


class ActionChoice(discord.ui.Select):
    """行動選擇器，目前只顯示預覽。"""

    def __init__(self, location_name, category, actions):
        self.actions = actions[:25]
        self.location_name = location_name
        self.category = category

        options = []

        for index, action in enumerate(self.actions):
            options.append(
                discord.SelectOption(
                    label=action["name"][:100],
                    value=str(index),
                    description=describe_action(action)[:100],
                )
            )

        super().__init__(
            placeholder=f"選擇{category}項目（預覽模式）",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction):
        action = self.actions[int(self.values[0])]

        valid, reason = validate_action(action)

        if action.get("approved") is True:
            status = "已核准，尚未開放結算"
        else:
            status = "待審核"

        message = (
            f"**{self.location_name}｜{self.category}**\n"
            f"**{action['name']}**\n\n"
            f"{describe_action(action)}\n\n"
            f"審核狀態：{status}\n"
            f"規則檢查："
            f"{'通過' if valid else reason}\n\n"
            "目前為預覽模式，"
            "不會扣款、發薪、配點或扣除行動值。"
        )

        await interaction.response.send_message(
            message,
            ephemeral=True,
        )


class ActionPreview(discord.ui.View):
    """行動選單。"""

    def __init__(self, location, category, actions):
        super().__init__(timeout=180)

        self.add_item(
            ActionChoice(
                location["name"],
                category,
                actions,
            )
        )


class LocationActionCommands(commands.Cog):
    """正式 /行動 指令入口。"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="行動",
        description="在所在地點查看打工、活動及課程",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        類別="選擇行動種類",
    )
    @app_commands.choices(
        類別=[
            app_commands.Choice(
                name="打工",
                value="打工",
            ),
            app_commands.Choice(
                name="活動",
                value="活動",
            ),
            app_commands.Choice(
                name="課程",
                value="課程",
            ),
        ]
    )
    async def action(
        self,
        interaction: discord.Interaction,
        類別: str = "打工",
    ):
        guild = interaction.guild
        channel = interaction.channel

        if guild is None or channel is None:
            await interaction.response.send_message(
                "請在伺服器的地點頻道使用。",
                ephemeral=True,
            )
            return

        location, provider = resolve_location(
            guild.id,
            channel.id,
        )

        if location is None:
            await interaction.response.send_message(
                "此頻道尚未綁定行動地點。",
                ephemeral=True,
            )
            return

        actions = [
            action
            for action in location.get("actions", [])
            if action.get("category") == 類別
        ]

        approved = [
            action
            for action in actions
            if action.get("approved") is True
            and validate_action(action)[0]
        ]

        is_owner = (
            interaction.user.id == guild.owner_id
        )

        if not is_owner:
            if not approved:
                await interaction.response.send_message(
                    f"**{location['name']}｜{類別}**\n"
                    "目前沒有已審核開放的行動。",
                    ephemeral=True,
                )
                return

            actions = approved

        if not actions:
            await interaction.response.send_message(
                f"**{location['name']}**\n"
                f"目前沒有設定「{類別}」項目。",
                ephemeral=True,
            )
            return

        if is_owner:
            description = "群主待審核行動預覽"
        else:
            description = "已核准行動預覽"

        await interaction.response.send_message(
            f"**{location['name']}｜{類別}**\n"
            f"共 {len(actions)} 項。\n"
            f"{description}\n\n"
            "目前尚未開放實際交易與獎勵發放。",
            view=ActionPreview(
                location,
                類別,
                actions,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    """由主程式自動載入。"""
    if bot.tree.get_command("行動") is not None:
        LOG.warning(
            "已有 /行動 指令，"
            "略過重複註冊。"
        )
        return

    await bot.add_cog(
        LocationActionCommands(bot)
    )
