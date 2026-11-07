
"""Discord RP /行動：六大分區場所預覽。

- 根據頻道 ID 自動辨識場所
- 玩家只能查看已核准行動
- 伺服器擁有者可預覽待審核行動
- 銀行使用 /銀行，不開放 /行動
- 商城使用 /商城，不開放 /行動
- 玩家商店預設不開放 /行動
- 普通行動不產生正式影視、公司或金融紀錄
- 不扣款、不發薪、不發點數（安全預覽模式）
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
    business_location_defaults,
)
from modules import medical_service_rules
from modules.action_reward_rules import validate_action
from core.database import get_connection
from core.action_execution import execute_action, ActionExecutionError


LOG = logging.getLogger(__name__)

VALID_CATEGORIES = ("打工", "活動", "課程")

PROVIDERS = (
    action_location_defaults,
    leisure_location_defaults,
    training_location_defaults,
    arts_location_defaults,
    film_location_defaults,
    business_location_defaults,
)

PAGE_SIZE = 25
VIEW_TIMEOUT = 180


def resolve_location(
    guild_id: int | str,
    channel_id: int | str,
) -> tuple[dict | None, Any | None]:
    """依照伺服器及頻道辨識地點。"""
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
                "地點查詢錯誤：guild=%s channel=%s",
                guild_id,
                channel_id,
            )

    return None, None


def describe_action(action: dict) -> str:
    parts = []

    reward = int(action.get("reward_coins", 0))
    cost = int(action.get("cost_coins", 0))

    if reward:
        parts.append(f"金幣 +{reward:,}")

    if cost:
        parts.append(f"花費 {cost:,} 金幣")

    attribute = action.get("attribute")
    increase = int(
        action.get("attribute_increase", 0)
    )

    if attribute and increase:
        skill_name = action.get("skill_name")
        name = skill_name or attribute
        parts.append(f"{name} +{increase}")

    pools = action.get("point_rewards") or {}

    if isinstance(pools, dict):
        for pool, amount in pools.items():
            if int(amount) > 0:
                parts.append(
                    f"{pool} +{int(amount)}"
                )

    health = int(action.get("health_delta", 0))

    if health:
        parts.append(f"健康 {health:+d}")

    ap = int(action.get("action_points_cost", 0))
    parts.append(f"行動值 -{ap}")

    return "｜".join(parts)


def action_is_valid(action: dict) -> bool:
    """排除設定異常或冒充正式通告的行動。"""
    if action.get("formal_notice") is True:
        return False

    valid, _ = validate_action(action)
    return valid


def get_category_actions(
    location: dict,
    category: str,
    *,
    owner_preview: bool,
) -> list[dict]:
    if category not in VALID_CATEGORIES:
        return []

    if location.get("action_enabled") is False:
        return []

    actions = [
        action
        for action in location.get("actions", [])
        if action.get("category") == category
    ]

    if owner_preview:
        return actions

    return [
        action
        for action in actions
        if action.get("approved") is True
        and action_is_valid(action)
    ]


def get_action_status(action: dict) -> str:
    if not action_is_valid(action):
        return "設定異常"

    if action.get("approved") is True:
        return "已核准（預覽模式）"

    return "待審核"


class ActionChoice(discord.ui.Select):

    def __init__(
        self,
        actions: list[dict],
        page: int,
    ):
        self.actions = actions

        start = page * PAGE_SIZE
        end = start + PAGE_SIZE

        options = []

        for index in range(
            start,
            min(end, len(actions)),
        ):
            action = actions[index]

            options.append(
                discord.SelectOption(
                    label=str(
                        action.get("name", "未命名行動")
                    )[:100],
                    value=str(index),
                    description=(
                        describe_action(action) or "查看行動"
                    )[:100],
                )
            )

        super().__init__(
            placeholder="選擇行動（預覽）",
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
                "行動選單狀態異常。",
                ephemeral=True,
            )
            return

        index = int(self.values[0])

        if not 0 <= index < len(self.actions):
            await interaction.response.send_message(
                "找不到這項行動。",
                ephemeral=True,
            )
            return

        action = self.actions[index]

        valid, reason = validate_action(action)

        message = (
            f"**{view.location['name']}｜"
            f"{view.category}**\n\n"
            f"**{action.get('name', '未命名行動')}**\n\n"
            f"{describe_action(action)}\n\n"
            f"狀態：{get_action_status(action)}\n"
            f"規則檢查："
            f"{'通過' if valid else reason}"
        )

        notes = action.get("notes")
        if notes:
            message += f"\n備註：{notes}"

        if view.provider is film_location_defaults:
            message += (
                "\n\n普通行動不產生正式通告、"
                "出演資歷、卡司、片酬或作品紀錄。"
            )

        if view.provider is business_location_defaults:
            message += (
                "\n\n普通行動不產生正式僱傭關係、"
                "銀行交易、公司股權、"
                "商城訂單或代言合約。"
            )

        if action.get('approved') is True and valid :
            message += "\n\n選擇自己的角色後，可確認執行（扣除付費行動值；不扣體力）。"
            with get_connection() as db:
                rows=db.execute("""SELECT id,name FROM characters WHERE guild_id=? AND owner_id=?
                    AND status='active' ORDER BY id LIMIT 25""",
                    (str(interaction.guild_id),str(interaction.user.id))).fetchall()
            if rows:
                confirm=ActionExecuteView(
                    guild_id=interaction.guild_id, channel_id=interaction.channel_id,
                    user_id=interaction.user.id, action_name=str(action.get('name','')),
                    category=view.category, characters=rows,
                )
                await interaction.response.send_message(message,view=confirm,ephemeral=True)
                return
            message += "\n目前沒有可執行的啟用角色。"
        else:
            message += "\n\n此行動尚未核准，僅供預覽，不會結算。"
        await interaction.response.send_message(message,ephemeral=True)


class ActionCharacterChoice(discord.ui.Select):
    def __init__(self, characters):
        super().__init__(placeholder="選擇要執行行動的角色", min_values=1, max_values=1,
            options=[discord.SelectOption(label=str(r['name'])[:100],value=str(r['id'])) for r in characters])

    async def callback(self, interaction: discord.Interaction):
        view=self.view
        if not isinstance(view, ActionExecuteView):
            return
        view.character_id=int(self.values[0])
        await interaction.response.edit_message(content="已選擇角色，請按「確認執行」完成結算。",view=view)


class ActionExecuteView(discord.ui.View):
    def __init__(self, *, guild_id, channel_id, user_id, action_name, category, characters):
        super().__init__(timeout=120)
        self.guild_id=int(guild_id)
        self.channel_id=int(channel_id)
        self.user_id=int(user_id)
        self.action_name=action_name
        self.category=category
        self.character_id=None
        self.add_item(ActionCharacterChoice(characters))

    async def interaction_check(self, interaction:discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這不是你的行動確認畫面。",ephemeral=True)
            return False
        return True

    @discord.ui.button(label="確認執行",style=discord.ButtonStyle.success,row=1)
    async def confirm(self, interaction:discord.Interaction, button:discord.ui.Button):
        if self.character_id is None:
            await interaction.response.send_message("請先選擇角色。",ephemeral=True)
            return
        if interaction.guild_id != self.guild_id or interaction.channel_id != self.channel_id:
            await interaction.response.send_message("請回原本場所頻道操作。",ephemeral=True)
            return
        location, _provider=resolve_location(self.guild_id,self.channel_id)
        if not location or location.get('action_enabled') is False:
            await interaction.response.send_message("場所已被停用。",ephemeral=True)
            return
        matching=[a for a in get_category_actions(location,self.category,owner_preview=False)
                  if str(a.get('name','')) == self.action_name]
        if len(matching)!=1:
            await interaction.response.send_message("行動已變更或不再開放，請重新使用 /行動。",ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result=execute_action(self.guild_id,self.user_id,self.character_id,
                str(location.get('key') or self.channel_id),matching[0],f"discord-action:{interaction.id}")
        except (ActionExecutionError,PermissionError,ValueError) as exc:
            await interaction.followup.send(f"無法執行：{exc}",ephemeral=True)
            return
        except Exception:
            LOG.exception('正式場所行動交易失敗')
            await interaction.followup.send("交易未完成，請稍後再試。",ephemeral=True)
            return
        self.stop()
        for item in self.children:
            item.disabled=True
        await interaction.followup.send(
            f"✅ {result['action_name']} 完成。\n金幣收入：{result['reward_coins']:,}\n"
            f"金幣花費：{result['cost_coins']:,}\n消耗行動值：{result['action_points_spent']}",ephemeral=True)


class ActionPreview(discord.ui.View):

    def __init__(
        self,
        *,
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

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id == self.user_id:
            return True

        await interaction.response.send_message(
            "這不是你的行動選單，請自行使用 /行動。",
            ephemeral=True,
        )
        return False

    def refresh_items(self):
        self.clear_items()

        self.add_item(
            ActionChoice(
                self.actions,
                self.current_page,
            )
        )

        if self.total_pages <= 1:
            return

        previous = discord.ui.Button(
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

        previous.callback = self.previous_page
        next_button.callback = self.next_page

        self.add_item(previous)
        self.add_item(next_button)

    def build_message(self) -> str:
        mode = (
            "群主待審核預覽"
            if self.owner_preview
            else "已核准行動預覽"
        )

        return (
            f"**{self.location['name']}｜"
            f"{self.category}**\n"
            f"共 {len(self.actions)} 項\n"
            f"第 {self.current_page + 1}"
            f"／{self.total_pages} 頁\n"
            f"{mode}\n\n"
            "已核准行動可由本人角色確認執行；待審核內容僅能預覽。"
        )

    async def previous_page(
        self,
        interaction: discord.Interaction,
    ):
        if self.current_page > 0:
            self.current_page -= 1

        self.refresh_items()

        await interaction.response.edit_message(
            content=self.build_message(),
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
            content=self.build_message(),
            view=self,
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

        self.stop()


class LocationActionCommands(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="行動",
        description="查看所在地點的打工、活動、課程或醫療服務",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        類別="選擇行動類別",
    )
    @app_commands.choices(
        類別=[
            app_commands.Choice(
                name="打工", value="打工"
            ),
            app_commands.Choice(
                name="活動", value="活動"
            ),
            app_commands.Choice(
                name="課程", value="課程"
            ),
            app_commands.Choice(
                name="醫療服務", value="醫療服務"
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
                "此頻道沒有綁定 /行動 場所。",
                ephemeral=True,
            )
            return

        # 醫院僅提供群主私密預覽；尚未核准不提供治療或整形交易。
        if location.get("key") == "hospital":
            if 類別 != "醫療服務":
                await interaction.response.send_message(
                    "此處是醫院，請選擇 `/行動` 的「醫療服務」類別。",
                    ephemeral=True,
                )
                return
            is_owner = interaction.user.id == guild.owner_id
            services = medical_service_rules.available_services(owner_preview=is_owner)
            if not services:
                await interaction.response.send_message(
                    "醫療服務目前尚未核准上線。整形價格、成功率與點數均需群主確認。",
                    ephemeral=True,
                )
                return
            lines = [medical_service_rules.format_service(s) for s in services]
            await interaction.response.send_message(
                "**醫院｜醫療服務（群主草案預覽，不扣款、不擲骰、不加點）**\n\n"
                + "\n".join(lines)
                + "\n\n本內容僅供規格審核，所有結果皆須角色授權。",
                ephemeral=True,
            )
            return

        # 銀行、商城與其他專用場所禁止使用 /行動。
        if location.get("action_enabled") is False:
            commands_allowed = location.get(
                "allowed_commands", []
            )

            if commands_allowed:
                command_name = commands_allowed[0]
                message = (
                    f"**{location['name']}** "
                    "不提供一般行動。\n"
                    f"請使用 `/{command_name}`。"
                )
            else:
                message = (
                    "這個場所不開放 /行動。"
                )

            await interaction.response.send_message(
                message,
                ephemeral=True,
            )
            return

        owner_preview = (
            interaction.user.id == guild.owner_id
        )

        actions = get_category_actions(
            location,
            類別,
            owner_preview=owner_preview,
        )

        if not actions:
            await interaction.response.send_message(
                f"**{location['name']}｜{類別}**\n"
                "目前沒有可查看的行動。",
                ephemeral=True,
            )
            return

        view = ActionPreview(
            location=location,
            provider=provider,
            category=類別,
            actions=actions,
            user_id=interaction.user.id,
            owner_preview=owner_preview,
        )

        await interaction.response.send_message(
            view.build_message(),
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
            "/行動 執行錯誤",
            exc_info=(
                type(error),
                error,
                error.__traceback__,
            ),
        )

        try:
            message = "行動系統暫時無法顯示。"

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
            LOG.exception("無法回覆 /行動 錯誤")


async def setup(bot: commands.Bot):
    """由主程式的擴充載入器呼叫。"""

    if bot.tree.get_command("行動") is not None:
        LOG.warning(
            "已有 /行動 指令，略過重複註冊。"
        )
        return

    if bot.get_cog("LocationActionCommands"):
        return

    await bot.add_cog(
        LocationActionCommands(bot)
    )

    LOG.info("已載入六大分區 /行動；已核准項目可正式結算")
