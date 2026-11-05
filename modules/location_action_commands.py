
"""Discord RP /行動：五大分區場所行動預覽入口。

目前支援：
1. 夜生活娛樂區
2. 生活休閒區
3. 培訓學習區
4. 藝文特區
5. 影視娛樂區

功能：
- 按伺服器及頻道 ID 辨識場所。
- 支援打工、活動、課程。
- 伺服器擁有者可預覽待審核行動。
- 玩家只看得到已核准且驗證通過的行動。
- 選單只允許原使用者操作。
- 支援每頁最多 25 項行動。
- 防止普通行動冒充正式影視通告。
- 不新增其他頂層 Slash 指令。

安全預覽模式：
- 不扣款、不發薪。
- 不扣除行動值。
- 不發放屬性或點數。
- 不修改作品履歷。
- 不自動核准任何行動。

正式交易及群主自訂設定需另外接入。
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from modules import (
    action_location_defaults,
    leisure_location_defaults,
    training_location_defaults,
    arts_location_defaults,
    film_location_defaults,
)
from modules.action_reward_rules import validate_action


LOG = logging.getLogger(__name__)

VALID_CATEGORIES = ("打工", "活動", "課程")

PROVIDERS = (
    action_location_defaults,
    leisure_location_defaults,
    training_location_defaults,
    arts_location_defaults,
    film_location_defaults,
)

PAGE_SIZE = 25
VIEW_TIMEOUT = 180


def resolve_location(
    guild_id: int | str,
    channel_id: int | str,
) -> tuple[dict | None, Any | None]:
    """依伺服器與頻道查詢場所。"""

    for provider in PROVIDERS:
        try:
            location = provider.lookup_channel(
                guild_id,
                channel_id,
            )
        except Exception:
            LOG.exception(
                "場所查詢失敗：guild=%s channel=%s provider=%s",
                guild_id,
                channel_id,
                provider.__name__,
            )
            continue

        if location is not None:
            return location, provider

    return None, None


def describe_action(action: dict) -> str:
    """顯示金幣、屬性、點數及行動值。"""

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
        skill_name = action.get("skill_name")

        if skill_name:
            parts.append(f"{skill_name} +{increase}")
        else:
            parts.append(f"{attribute} +{increase}")

    point_rewards = action.get("point_rewards") or {}

    if isinstance(point_rewards, dict):
        for pool, amount in point_rewards.items():
            if int(amount) > 0:
                parts.append(f"{pool} +{int(amount)}")

    health = int(action.get("health_delta", 0))

    if health:
        parts.append(f"健康 {health:+d}")

    ap = int(action.get("action_points_cost", 0))
    parts.append(f"行動值 -{ap}")

    return "｜".join(parts) or "無額外獎勵"


def is_formal_notice_action(action: dict) -> bool:
    """防止場所普通行動被標記為正式通告。"""

    return action.get("formal_notice") is True


def get_action_status(action: dict) -> str:
    valid, reason = validate_action(action)

    if is_formal_notice_action(action):
        return "設定異常：普通行動不可作為正式通告"

    if not valid:
        return f"設定異常：{reason}"

    if action.get("approved") is True:
        return "已核准（尚未開放結算）"

    return "待審核"


def get_category_actions(
    location: dict,
    category: str,
    owner_preview: bool = False,
) -> list[dict]:
    """依身分過濾可預覽的行動。"""

    if category not in VALID_CATEGORIES:
        return []

    actions = [
        action
        for action in location.get("actions", [])
        if action.get("category") == category
    ]

    if owner_preview:
        return actions

    result = []

    for action in actions:
        if action.get("approved") is not True:
            continue

        if is_formal_notice_action(action):
            continue

        valid, _ = validate_action(action)

        if valid:
            result.append(action)

    return result


class ActionChoice(discord.ui.Select):
    """行動選項：只查看資料，不執行。"""

    def __init__(
        self,
        location_name: str,
        category: str,
        actions: list[dict],
        page: int,
    ):
        self.location_name = location_name
        self.category = category
        self.actions = actions

        start = page * PAGE_SIZE
        end = start + PAGE_SIZE

        page_actions = actions[start:end]

        options = []

        for index, action in enumerate(
            page_actions,
            start=start,
        ):
            description = describe_action(action)

            options.append(
                discord.SelectOption(
                    label=str(
                        action.get("name") or "未命名行動"
                    )[:100],
                    value=str(index),
                    description=description[:100],
                )
            )

        super().__init__(
            placeholder=f"選擇{category}項目（預覽）",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        view = self.view

        if not isinstance(view, ActionPreview):
            await interaction.response.send_message(
                "選單狀態異常，請重新使用 /行動。",
                ephemeral=True,
            )
            return

        if not await view.check_user(interaction):
            return

        index = int(self.values[0])

        if index < 0 or index >= len(self.actions):
            await interaction.response.send_message(
                "找不到此行動。",
                ephemeral=True,
            )
            return

        action = self.actions[index]
        valid, reason = validate_action(action)

        message = (
            f"**{self.location_name}｜{self.category}**\n\n"
            f"**{action.get('name', '未命名行動')}**\n\n"
            f"{describe_action(action)}\n\n"
            f"審核狀態：{get_action_status(action)}\n"
            f"規則檢查："
            f"{'通過' if valid else reason}"
        )

        if action.get("notes"):
            message += f"\n備註：{action['notes']}"

        if view.provider is film_location_defaults:
            message += (
                "\n\n**影視通告隔離規則**"
                "\n此行動不代表正式作品錄取，"
                "不產生正式片酬、作品履歷、"
                "演出資歷或卡司紀錄。"
            )

        message += (
            "\n\n目前為預覽模式，"
            "不會扣款、發薪、配點或扣除行動值。"
        )

        await interaction.response.send_message(
            message,
            ephemeral=True,
        )


class ActionPreview(discord.ui.View):
    """行動分頁選單。"""

    def __init__(
        self,
        location: dict,
        provider: Any,
        category: str,
        actions: list[dict],
        user_id: int,
        owner_preview: bool,
    ):
        super().__init__(timeout=VIEW_TIMEOUT)

        self.location = location
        self.provider = provider
        self.category = category
        self.actions = actions
        self.user_id = user_id
        self.owner_preview = owner_preview

        self.current_page = 0
        self.total_pages = max(
            1,
            (len(actions) + PAGE_SIZE - 1) // PAGE_SIZE,
        )

        self.refresh_items()

    async def check_user(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "這不是你的行動選單，請自行使用 /行動。",
                ephemeral=True,
            )
            return False

        return True

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        return await self.check_user(interaction)

    def refresh_items(self):
        self.clear_items()

        self.add_item(
            ActionChoice(
                location_name=self.location["name"],
                category=self.category,
                actions=self.actions,
                page=self.current_page,
            )
        )

        if self.total_pages <= 1:
            return

        previous_button = discord.ui.Button(
            label="上一頁",
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            disabled=self.current_page == 0,
            row=1,
        )

        next_button = discord.ui.Button(
            label="下一頁",
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
            disabled=(
                self.current_page >= self.total_pages - 1
            ),
            row=1,
        )

        previous_button.callback = self.previous_page
        next_button.callback = self.next_page

        self.add_item(previous_button)
        self.add_item(next_button)

    def build_page_message(self) -> str:
        mode = (
            "群主待審核預覽"
            if self.owner_preview
            else "已核准行動預覽"
        )

        message = (
            f"**{self.location['name']}｜{self.category}**\n"
            f"共 {len(self.actions)} 項\n"
            f"第 {self.current_page + 1}"
            f"／{self.total_pages} 頁\n"
            f"{mode}\n\n"
            "目前尚未開放實際結算。"
        )

        if self.provider is film_location_defaults:
            message += (
                "\n普通行動與正式影視通告分開結算。"
            )

        return message

    async def previous_page(
        self,
        interaction: discord.Interaction,
    ):
        if self.current_page > 0:
            self.current_page -= 1

        self.refresh_items()

        await interaction.response.edit_message(
            content=self.build_page_message(),
            view=self,
        )

    async def next_page(
        self,
        interaction: discord.Interaction,
    ):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1

        self.refresh_items()

        await interaction.response.edit_message(
            content=self.build_page_message(),
            view=self,
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

        self.stop()


class LocationActionCommands(commands.Cog):
    """統一 /行動 指令。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="行動",
        description="在目前地點查看打工、活動及課程",
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
                "請在伺服器內的地點頻道使用 /行動。",
                ephemeral=True,
            )
            return

        if 類別 not in VALID_CATEGORIES:
            await interaction.response.send_message(
                "未知的行動類別。",
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

        is_owner = interaction.user.id == guild.owner_id

        actions = get_category_actions(
            location,
            類別,
            owner_preview=is_owner,
        )

        if not actions:
            message = (
                f"**{location['name']}｜{類別}**\n"
                + (
                    "目前沒有設定這個類別的行動。"
                    if is_owner
                    else "目前沒有已審核開放的行動。"
                )
            )

            await interaction.response.send_message(
                message,
                ephemeral=True,
            )
            return

        view = ActionPreview(
            location=location,
            provider=provider,
            category=類別,
            actions=actions,
            user_id=interaction.user.id,
            owner_preview=is_owner,
        )

        await interaction.response.send_message(
            view.build_page_message(),
            view=view,
            ephemeral=True,
        )

    @action.error
    async def action_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        LOG.error(
            "/行動 指令錯誤",
            exc_info=(
                type(error),
                error,
                error.__traceback__,
            ),
        )

        message = (
            "行動系統暫時無法顯示，"
            "請確認場所設定。"
        )

        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    message,
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    message,
                    ephemeral=True,
                )
        except discord.HTTPException:
            LOG.exception("無法傳送 /行動 錯誤訊息")


async def setup(bot: commands.Bot):
    """載入 Discord Cog。"""

    if bot.tree.get_command("行動") is not None:
        LOG.warning(
            "已有 /行動 指令，略過重複註冊。"
        )
        return

    if bot.get_cog("LocationActionCommands") is not None:
        LOG.warning(
            "LocationActionCommands 已存在。"
        )
        return

    await bot.add_cog(LocationActionCommands(bot))

    LOG.info(
        "已載入 /行動 預覽："
        "夜生活、生活休閒、培訓學習、"
        "藝文特區、影視娛樂區"
    )
