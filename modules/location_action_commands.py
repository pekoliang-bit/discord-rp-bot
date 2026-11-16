"""Discord RP /行動：六大分區場所選單與核准行動執行。

- 根據頻道 ID 自動辨識場所
- 玩家只能查看已核准行動
- 伺服器擁有者可預覽待審核行動
- 銀行使用 /銀行，不開放 /行動
- 商城使用 /商城，不開放 /行動
- 玩家商店預設不開放 /行動
- 普通行動不產生正式影視、公司或金融紀錄
- 已核准行動可由本人確認後原子結算；未核准行動只能預覽
"""

from __future__ import annotations

import logging
import asyncio
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
from core.action_execution import execute_action, ActionExecutionError, _fingerprint


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
    """合併預設場所與已審核的頻道行動，按頻道直接供 /行動 使用。"""
    location = None
    provider_used = None
    for provider in PROVIDERS:
        try:
            found = provider.lookup_channel(guild_id, channel_id)
            if found is not None:
                location, provider_used = found, provider
                break
        except Exception:
            LOG.exception("地點查詢錯誤：guild=%s channel=%s", guild_id, channel_id)

    from core.channel_action_registry import list_channel_actions
    try:
        extra, name = list_channel_actions(guild_id, channel_id)
    except Exception:
        LOG.exception("新增場所行動查詢失敗")
        extra, name = [], None
    if not extra:
        return location, provider_used
    if location is None:
        location = {'key': str(channel_id), 'channel_id': str(channel_id),
                    'name': name or '新增場所', 'action_enabled': True,
                    'actions': extra}
    else:
        location = dict(location)
        location['actions'] = list(location.get('actions', [])) + extra
    return location, provider_used


def describe_action(action: dict) -> str:
    parts = []

    reward = int(action.get("reward_coins", 0))
    cost = int(action.get("cost_coins", 0))

    if reward:
        parts.append(f"金幣 +{reward:,}")

    if action.get('stat_rewards') and action.get('category') in ('活動','課程'):
        from core.action_application_rules import preview_cost
        price = preview_cost(action['category'], cost, action['stat_rewards'])
        parts.append(f"花費 {price['min']:,}～{price['max']:,} 金幣")
    elif cost:
        parts.append(f"花費 {cost:,} 金幣")

    attribute = action.get("attribute")
    increase = int(
        action.get("attribute_increase", 0)
    )

    if attribute and increase:
        skill_name = action.get("skill_name")
        name = skill_name or attribute
        parts.append(f"{name} +{increase}")

    for spec in action.get("stat_rewards", []):
        if spec.get('mode') == 'random':
            parts.append(f"{spec['name']} +1～15（隨機）")
        else:
            parts.append(f"{spec['name']} +{spec['amount']}")
    if action.get("stat_rewards") and action.get("category") in ("活動", "課程"):
        parts.append("按實際點數浮動收費")

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
        return "已核准（可確認執行）"

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
                    label=(
                        f"{action.get('category', '行動')}｜{action.get('name', '未命名行動')}"
                    )[:100],
                    value=str(index),
                    description=(
                        describe_action(action) or "查看行動"
                    )[:100],
                )
            )

        super().__init__(
            placeholder="選擇行動",
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
            f"{action.get('category', '行動')}**\n\n"
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
            message += "\n\n選擇自己的角色後，可確認執行（依可用餘額扣除免費／付費行動值；不扣體力）。"
            if action.get('stat_rewards') and action.get('category') in ('活動', '課程'):
                message += "\n⚠️ 本次實際金幣收費將依隨機增加的點數計算，可能高於基本價格；金幣不足將取消整筆交易。"
            with get_connection() as db:
                rows=db.execute("""SELECT id,name FROM characters WHERE guild_id=? AND owner_id=?
                    AND status='active' ORDER BY id LIMIT 25""",
                    (str(interaction.guild_id),str(interaction.user.id))).fetchall()
            if rows:
                confirm=ActionExecuteView(
                    guild_id=interaction.guild_id, channel_id=interaction.channel_id,
                    user_id=interaction.user.id, action_name=str(action.get('name','')),
                    category=str(action.get('category', '')), characters=rows,
                    action_fingerprint=_fingerprint(str(view.location.get('key') or interaction.channel_id),action),
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
    def __init__(self, *, guild_id, channel_id, user_id, action_name, category, characters, action_fingerprint):
        super().__init__(timeout=120)
        self.guild_id=int(guild_id)
        self.channel_id=int(channel_id)
        self.user_id=int(user_id)
        self.action_name=action_name
        self.action_fingerprint=action_fingerprint
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
        matching=[a for category in VALID_CATEGORIES
                  for a in get_category_actions(location,category,owner_preview=False)
                  if str(a.get('name','')) == self.action_name and a.get('category') == self.category]
        if len(matching)!=1:
            await interaction.response.send_message("行動已變更或不再開放，請重新使用 /行動。",ephemeral=True)
            return
        if _fingerprint(str(location.get('key') or self.channel_id), matching[0]) != self.action_fingerprint:
            await interaction.response.send_message("行動的費用或獎勵已變更，請重新選擇。",ephemeral=True)
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
        # Animal encounters are a *chance event after a completed action*,
        # never a manually selectable action. Idempotent by the action request ID.
        encounter_note=''
        encounter_view=None
        if not result.get('duplicated'):
            try:
                from core.pet_market_v2 import action_encounter, encounter_detail
                from modules.pet_center import EncounterResultView
                eid=await asyncio.to_thread(action_encounter,self.guild_id,self.character_id,
                    self.user_id,f"discord-action:{interaction.id}",location_key=str(location.get('key') or self.channel_id))
                if eid is not None:
                    encounter=await asyncio.to_thread(encounter_detail,self.guild_id,eid)
                    if encounter:
                        encounter_note='\n🐾 **額外隨機事件：遇見動物！** 請選擇如何處理。'
                        encounter_view=EncounterResultView(interaction,self.character_id,encounter)
            except Exception:
                LOG.exception('行動已完成，寵物額外事件判定失敗')
        self.stop()
        for item in self.children:
            item.disabled=True
        await interaction.followup.send(
            f"✅ {result['action_name']} 完成。\n金幣收入：{result['reward_coins']:,}\n"
            f"金幣花費：{result['cost_coins']:,}\n本次數值：{result.get('rolled_stats', [])}\n消耗行動值：{result['action_points_spent']}"
            f"（免費 {result['free_action_points_spent']}／付費 {result['paid_action_points_spent']}）"
            +( '\n🎂 這次已扣除生日地點預付次數，金幣免重複付款。' if result.get('birthday_credit_used') else '' )
            +encounter_note,
            view=encounter_view,ephemeral=True)


class BirthdayLocationOfferView(discord.ui.View):
    """生日地點優惠：本人角色＋已核准的固定價格服務，確認後才扣金幣。"""
    def __init__(self,interaction,location,actions,characters):
        super().__init__(timeout=180)
        from core.birthday_location_v3 import offer_for
        self.guild_id=int(interaction.guild_id)
        self.user_id=int(interaction.user.id)
        self.location=dict(location)
        self.actions=[a for a in actions if offer_for(interaction.guild_id,location,a)]
        self.characters=[dict(c) for c in characters]
        self.selected_action=None
        self.selected_character=None
        if self.actions:
            options=[]
            for idx,a in enumerate(self.actions[:25]):
                offer=offer_for(self.guild_id,location,a)
                options.append(discord.SelectOption(
                    label=f'{a["name"]}｜{offer["title"]}'[:100],
                    description=f'一次預付 {offer["total_price"]:,} 金幣／共 {offer["pay_count"]+offer["bonus_count"]} 次'[:100],
                    value=str(idx)))
            pick=discord.ui.Select(placeholder='挑選生日組合／課程',options=options,row=0)
            async def choose_action(i):
                self.selected_action=int(pick.values[0])
                await i.response.edit_message(content=self.description(),view=self)
            pick.callback=choose_action;self.add_item(pick)
        if self.characters:
            pick_char=discord.ui.Select(placeholder='選擇本人角色',
                options=[discord.SelectOption(label=str(c['name'])[:100],value=str(c['id'])) for c in self.characters[:25]],row=1)
            async def choose_character(i):
                self.selected_character=int(pick_char.values[0])
                await i.response.edit_message(content=self.description(),view=self)
            pick_char.callback=choose_character;self.add_item(pick_char)

    def description(self):
        from core.birthday_location_v3 import offer_for
        lines=[f'🎂 **{self.location.get("name","場所")}｜週年生日優惠**',
               '每個地點每個系統週年限一次，生日起72小時內購買組合。',
               '一次預付固定堂數／份數，贈送的免費次數可之後分次使用。']
        if self.selected_action is not None:
            offer=offer_for(self.guild_id,self.location,self.actions[self.selected_action])
            if offer:lines.append(f'已選：{offer["action_name"]}，預付 {offer["total_price"]:,} 金幣，可使用 {offer["pay_count"]+offer["bonus_count"]} 次。')
        if self.selected_character is not None:
            lines.append(f'角色 #{self.selected_character}')
        lines.append('選擇完後，按「確認購買生日組合」才正式扣金幣。')
        return '\n'.join(lines)

    async def interaction_check(self,i):
        if int(i.user.id)!=self.user_id or int(i.guild_id)!=self.guild_id:
            await i.response.send_message('只能操作自己的生日優惠。',ephemeral=True)
            return False
        return True

    @discord.ui.button(label='🎂 確認購買生日組合',style=discord.ButtonStyle.success,row=2)
    async def confirm(self,i,button):
        if self.selected_action is None or self.selected_character is None:
            return await i.response.send_message('請先選擇組合商品及角色。',ephemeral=True)
        current,_=resolve_location(self.guild_id,self.location.get('channel_id') or i.channel_id)
        if not current or str(current.get('key'))!=str(self.location.get('key')):
            return await i.response.send_message('地點資料已變更，請重新使用 /行動。',ephemeral=True)
        matched=[a for c in VALID_CATEGORIES for a in get_category_actions(current,c,owner_preview=False)
                 if _fingerprint(str(current.get('key')),a)==_fingerprint(str(self.location.get('key')),self.actions[self.selected_action])]
        if len(matched)!=1:
            return await i.response.send_message('課程內容已變更，請重新確認。',ephemeral=True)
        await i.response.defer(ephemeral=True)
        try:
            from core.birthday_location_v3 import buy_bundle
            result=await asyncio.to_thread(buy_bundle,self.guild_id,self.selected_character,
                self.user_id,current,matched[0],f'discord:birthday-location:{i.id}')
        except (ValueError,LookupError,PermissionError) as exc:
            return await i.followup.send(f'⚠️ {exc}',ephemeral=True)
        except Exception:
            LOG.exception('生日地點組合扣款失敗')
            return await i.followup.send('交易失敗，未完成購買。',ephemeral=True)
        self.stop()
        for child in self.children:child.disabled=True
        await i.followup.send(
            f'✅ 生日優惠購買完成！\n預付：{result["paid"]:,} 金幣\n可使用：{result["sessions"]} 次。'
            '\n之後在同一地點使用相同已核准行動，系統會自動優先扣除預付次數。',ephemeral=True)


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

        try:
            from core.birthday_location_v3 import offer_for
            if any(offer_for(str(self.location.get('guild_id') or ''),self.location,a) for a in self.actions):
                offer_button=discord.ui.Button(label='🎂 地點生日優惠',style=discord.ButtonStyle.success,row=2)
                offer_button.callback=self.open_birthday
                self.add_item(offer_button)
        except Exception:
            LOG.exception('生日地點優惠預覽初始化失敗')
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
            else "已核准行動"
        )

        return (
            f"**{self.location['name']}｜"
            "可用行動**\n"
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

    async def open_birthday(self,interaction:discord.Interaction):
        from core.birthday_location_v3 import offer_for,eligible
        with get_connection() as db:
            rows=db.execute('''SELECT id,name FROM characters WHERE guild_id=? AND owner_id=? AND status='active'
                ORDER BY id LIMIT 25''',(str(interaction.guild_id),str(interaction.user.id))).fetchall()
            eligible_rows=[dict(r) for r in rows if eligible(db,interaction.guild_id,r['id'],self.location.get('key'))]
        if not eligible_rows:
            return await interaction.response.send_message('目前沒有符合此地點生日優惠資格的角色，或本週年已領過。',ephemeral=True)
        actions=[a for a in self.actions if offer_for(interaction.guild_id,self.location,a)]
        if not actions:
            return await interaction.response.send_message('目前此地點沒有符合優惠的已核准固定價服務。',ephemeral=True)
        view=BirthdayLocationOfferView(interaction,self.location,actions,eligible_rows)
        await interaction.response.send_message(view.description(),view=view,ephemeral=True)

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
    async def action(
        self,
        interaction: discord.Interaction,
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

        actions = [
            action
            for category in VALID_CATEGORIES
            for action in get_category_actions(
                location, category, owner_preview=owner_preview,
            )
        ]

        if not actions:
            await interaction.response.send_message(
                f"**{location['name']}｜可用行動**\n"
                "目前沒有可查看的行動。",
                ephemeral=True,
            )
            return

        view = ActionPreview(
            location=dict(location,guild_id=str(interaction.guild_id)),
            provider=provider,
            category="全部",
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
