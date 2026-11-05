
"""Discord RP /行動：場所頻道辨識與行動預覽。

目前支援四個分區：
1. 夜生活娛樂區（3 個地點）
2. 生活休閒區（7 個地點）
3. 培訓學習區（5 個地點）
4. 藝文特區（4 個地點）

合計：19 個地點、158 項待審核行動。

功能：
- 根據伺服器 ID、頻道 ID 自動辨識地點。
- 支援打工、活動、課程。
- 玩家只能查看已核准的行動。
- 伺服器擁有者可預覽待審核行動。
- 驗證金幣、行動值、屬性及點數規則。
- 防止其他使用者操作原開啟者的選單。
- 支援超過 25 項行動時分頁。
- 不新增其他頂層 Slash 指令。

安全限制：
- 不直接執行任何行動。
- 不扣除金幣或行動值。
- 不發放金幣、屬性及點數。
- 不修改角色資料。
- 不自動核准待審核行動。

此模組目前為預覽版本，正式結算將於後續整合。
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
)

from modules.action_reward_rules import validate_action


LOG = logging.getLogger(__name__)

VALID_CATEGORIES = (
    "打工",
    "活動",
    "課程",
)

PROVIDERS = (
    action_location_defaults,
    leisure_location_defaults,
    training_location_defaults,
    arts_location_defaults,
)

PAGE_SIZE = 25
VIEW_TIMEOUT = 180


def resolve_location(
    guild_id: int | str,
    channel_id: int | str,
) -> tuple[dict | None, Any | None]:
    """依據伺服器及頻道 ID 尋找對應地點。"""

    for provider in PROVIDERS:
        try:
            location = provider.lookup_channel(
                guild_id,
                channel_id,
            )

            if location is not None:
                return location, provider

        except Exception:
            LOG.exception(
                "查詢行動地點失敗："
                "guild=%s channel=%s provider=%s",
                guild_id,
                channel_id,
                provider.__name__,
            )

    return None, None


def describe_action(action: dict) -> str:
    """將行動效果轉換為玩家可閱讀的文字。"""

    parts = []

    reward = int(action.get("reward_coins", 0))
    cost = int(action.get("cost_coins", 0))

    if reward > 0:
        parts.append(
            f"金幣 +{reward:,}"
        )

    if cost > 0:
        parts.append(
            f"花費 {cost:,} 金幣"
        )

    attribute = action.get("attribute")
    increase = int(
        action.get("attribute_increase", 0)
    )

    if attribute and increase > 0:
        skill_name = action.get("skill_name")

        if skill_name:
            parts.append(
                f"{skill_name} +{increase}"
            )
        else:
            parts.append(
                f"{attribute} +{increase}"
            )

    point_rewards = action.get(
        "point_rewards", {}
    )

    if isinstance(point_rewards, dict):
        for pool, amount in point_rewards.items():
            if int(amount) > 0:
                parts.append(
                    f"{pool} +{int(amount)}"
                )

    health = int(
        action.get("health_delta", 0)
    )

    if health != 0:
        parts.append(
            f"健康 {health:+d}"
        )

    action_points = int(
        action.get("action_points_cost", 0)
    )

    parts.append(
        f"行動值 -{action_points}"
    )

    return "｜".join(parts)


def get_action_status(action: dict) -> str:
    """取得行動目前的核准與驗證狀態。"""

    valid, reason = validate_action(action)

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
    """取得指定地點與分類的行動。"""

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

        valid, _ = validate_action(action)

        if valid:
            result.append(action)

    return result


class ActionChoice(discord.ui.Select):
    """行動選擇器，僅供預覽。"""

    def __init__(
        self,
        location_name: str,
        category: str,
        actions: list[dict],
        page: int = 0,
    ):
        self.location_name = location_name
        self.category = category
        self.actions = actions
        self.page = page

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
                        action.get("name", "未命名行動")
                    )[:100],
                    value=str(index),
                    description=description[:100],
                )
            )

        super().__init__(
            placeholder=(
                f"選擇{category}項目"
                "（預覽模式）"
            )[:150],
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        view = self.view

        if not isinstance(
            view,
            ActionPreview,
        ):
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
                "找不到這項行動，請重新選擇。",
                ephemeral=True,
            )
            return

        action = self.actions[index]

        valid, reason = validate_action(action)

        status = get_action_status(action)

        message = (
            f"**{self.location_name}｜"
            f"{self.category}**\n\n"
            f"**{action.get('name', '未命名行動')}**\n\n"
            f"{describe_action(action)}\n\n"
            f"審核狀態：{status}\n"
            f"規則檢查："
            f"{'通過' if valid else reason}"
        )

        notes = action.get("notes")

        if notes:
            message += (
                f"\n備註：{notes}"
            )

        message += (
            "\n\n目前為安全預覽模式。"
            "\n不會扣除金幣、行動值，"
            "也不會發放任何獎勵。"
        )

        await interaction.response.send_message(
            message,
            ephemeral=True,
        )


class ActionPreview(discord.ui.View):
    """支援選單分頁及使用者限制。"""

    def __init__(
        self,
        location: dict,
        category: str,
        actions: list[dict],
        user_id: int,
    ):
        super().__init__(
            timeout=VIEW_TIMEOUT
        )

        self.location = location
        self.category = category
        self.actions = actions
        self.user_id = user_id

        self.current_page = 0

        self.total_pages = max(
            1,
            (
                len(actions) + PAGE_SIZE - 1
            ) // PAGE_SIZE,
        )

        self.refresh_items()

    async def check_user(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        """只有原本開啟選單的人可以操作。"""

        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "這不是你的行動選單，"
                "請自行使用 /行動。",
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
        """重建目前頁面的選單與分頁按鈕。"""

        self.clear_items()

        self.add_item(
            ActionChoice(
                location_name=self.location["name"],
                category=self.category,
                actions=self.actions,
                page=self.current_page,
            )
        )

        if self.total_pages > 1:
            previous_button = discord.ui.Button(
                label="上一頁",
                emoji="◀️",
                style=discord.ButtonStyle.secondary,
                disabled=self.current_page <= 0,
                row=1,
            )

            next_button = discord.ui.Button(
                label="下一頁",
                emoji="▶️",
                style=discord.ButtonStyle.secondary,
                disabled=(
                    self.current_page
                    >= self.total_pages - 1
                ),
                row=1,
            )

            previous_button.callback = (
                self.previous_page
            )

            next_button.callback = (
                self.next_page
            )

            self.add_item(previous_button)
            self.add_item(next_button)

    async def previous_page(
        self,
        interaction: discord.Interaction,
    ):
        if not await self.check_user(interaction):
            return

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
        if not await self.check_user(interaction):
            return

        if self.current_page < self.total_pages - 1:
            self.current_page += 1

        self.refresh_items()

        await interaction.response.edit_message(
            content=self.build_page_message(),
            view=self,
        )

    def build_page_message(self) -> str:
        """產生目前分頁的標題與說明。"""

        return (
            f"**{self.location['name']}｜"
            f"{self.category}**\n"
            f"共 {len(self.actions)} 項行動。\n"
            f"第 {self.current_page + 1}"
            f"／{self.total_pages} 頁\n\n"
            "目前為行動預覽模式，"
            "尚未開放實際結算。"
        )

    async def on_timeout(self):
        """選單超時後停止接受互動。"""

        for item in self.children:
            item.disabled = True

        self.stop()


class LocationActionCommands(commands.Cog):
    """Discord /行動 斜線指令。"""

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    @app_commands.command(
        name="行動",
        description=(
            "在所在地點查看打工、"
            "活動及課程"
        ),
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
                "請在伺服器的地點頻道使用 /行動。",
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
                "此頻道尚未綁定行動地點。\n"
                "請確認頻道設定，"
                "或聯絡伺服器管理員。",
                ephemeral=True,
            )
            return

        is_owner = (
            interaction.user.id
            == guild.owner_id
        )

        actions = get_category_actions(
            location=location,
            category=類別,
            owner_preview=is_owner,
        )

        if not actions:
            if is_owner:
                message = (
                    f"**{location['name']}**\n"
                    f"目前沒有設定「{類別}」項目。"
                )
            else:
                message = (
                    f"**{location['name']}｜{類別}**\n"
                    "目前沒有已審核開放的行動。"
                )

            await interaction.response.send_message(
                message,
                ephemeral=True,
            )
            return

        view = ActionPreview(
            location=location,
            category=類別,
            actions=actions,
            user_id=interaction.user.id,
        )

        if is_owner:
            description = (
                "群主預覽："
                "包含尚未核准的行動。"
            )
        else:
            description = (
                "玩家預覽："
                "只顯示已核准的行動。"
            )

        message = (
            f"**{location['name']}｜{類別}**\n"
            f"共 {len(actions)} 項。\n"
            f"{description}\n\n"
            "目前尚未開放實際交易、"
            "行動值扣除及獎勵發放。"
        )

        try:
            await interaction.response.send_message(
                message,
                view=view,
                ephemeral=True,
            )

        except discord.HTTPException:
            LOG.exception(
                "/行動 預覽傳送失敗："
                "guild=%s channel=%s user=%s",
                guild.id,
                channel.id,
                interaction.user.id,
            )

    @action.error
    async def action_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        """統一處理指令執行例外。"""

        LOG.error(
            "/行動 執行失敗",
            exc_info=(
                type(error),
                error,
                error.__traceback__,
            ),
        )

        message = (
            "行動系統暫時無法顯示，"
            "請稍後重新執行。"
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
            LOG.exception(
                "無法回覆 /行動 錯誤訊息"
            )


async def setup(bot: commands.Bot):
    """由主程式自動載入擴充模組。"""

    if bot.tree.get_command("行動") is not None:
        LOG.warning(
            "已有 /行動 指令，"
            "略過重複註冊。"
        )
        return

    if bot.get_cog("LocationActionCommands"):
        LOG.warning(
            "LocationActionCommands 已載入，"
            "略過重複載入。"
        )
        return

    await bot.add_cog(
        LocationActionCommands(bot)
    )

    LOG.info(
        "已載入 /行動 預覽系統："
        "夜生活、生活休閒、"
        "培訓學習、藝文特區"
    )
