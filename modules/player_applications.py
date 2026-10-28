
"""Discord RP：統一玩家申請中心。

正式指令：/玩家申請

整合現有正式業務核心：
- 店鋪申請
- 貸款申請
- 一般信用卡申請
- 黑卡 VIP 申請

功能：
- 自動辨識目前使用角色
- 多角色選擇與分頁
- 角色所有權、正式啟用狀態驗證
- 不需要手動輸入角色編號
- 中文表單及互動選單
- 使用原本的申請資料庫與審核邏輯
- 禁止其他 Discord 使用者操作私人選單
- 防止一般的重複按鈕提交

未實作的檢舉及其他申請，不建立假入口。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)

from modules.character_switch import (
    get_current_character,
    list_available_characters,
)

from modules.player_shops import request_store
from modules.loans import new_application
from modules.credit_cards import apply_card
from modules.black_card import apply_black_card

LOG = logging.getLogger(__name__)

PAGE_SIZE = 25
TIMEOUT = 600

LOAN_TYPES = {
    "personal": ("個人貸款", 30),
    "business": ("事業貸款", 60),
    "asset": ("資產貸款", 90),
}


def actor_from(interaction):
    return Actor.from_interaction(interaction)


def validate_session(interaction, actor):
    """驗證互動者與原始操作人一致。"""

    current = actor_from(interaction)

    if (
        current.guild_id != actor.guild_id
        or current.user_id != actor.user_id
    ):
        raise PermissionDenied(
            "這不是你的申請介面。"
        )

    return current


def owned_active_character(actor, character_id):
    """每次正式申請前重新查驗角色。"""

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM characters
            WHERE guild_id = ?
              AND id = ?
              AND owner_id = ?
              AND status = 'active'
            """,
            (
                actor.guild_id,
                int(character_id),
                actor.user_id,
            ),
        ).fetchone()

    if row is None:
        raise PermissionDenied(
            "角色不存在、尚未啟用，"
            "或已不屬於你。"
        )

    return dict(row)


def resolve_current(actor):
    """取得目前角色，並再次檢查所有權。"""

    character = get_current_character(
        actor.guild_id,
        actor.user_id,
    )

    if character is None:
        return None

    return owned_active_character(
        actor,
        character["id"],
    )


def list_characters(actor):
    """不限制角色總數。"""

    return list_available_characters(
        actor.guild_id,
        actor.user_id,
    )


def page_info(items, page):
    last = max(
        0,
        (len(items) - 1) // PAGE_SIZE,
    )

    page = max(
        0,
        min(int(page), last),
    )

    start = page * PAGE_SIZE

    return (
        items[start:start + PAGE_SIZE],
        page,
        last,
    )


def application_home_text(character):
    return (
        "📨 **玩家申請中心**\n\n"
        f"目前角色：**{character['name']}**\n"
        f"角色編號：#{character['id']}\n"
        f"職業：{character['occupation'] or '未設定'}\n\n"
        "**可申請項目**\n"
        "🏪 個人店鋪\n"
        "🏦 官方銀行貸款\n"
        "💳 一般信用卡\n"
        "🖤 黑卡 VIP\n\n"
        "本次申請使用畫面顯示的角色，"
        "不必輸入角色編號。\n\n"
        "申請成功僅代表已送件，"
        "仍須等待官方審核。"
    )


async def send_error(interaction, exc):
    LOG.warning(
        "玩家申請操作未完成：%s",
        exc,
    )

    if isinstance(exc, sqlite3.Error):
        LOG.exception(
            "玩家申請資料庫錯誤"
        )
        message = (
            "⚠️ 資料庫操作失敗，"
            "請聯絡管理員。"
        )
    else:
        message = f"⚠️ {str(exc)[:1600]}"

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


class OwnedView(discord.ui.View):
    def __init__(self, actor):
        super().__init__(timeout=TIMEOUT)
        self.actor = actor

    async def interaction_check(self, interaction):
        try:
            validate_session(
                interaction,
                self.actor,
            )
            return True

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await send_error(
                interaction,
                exc,
            )
            return False


class SubmissionModal(discord.ui.Modal):
    """所有申請共用的身分驗證與提交防重複。"""

    def __init__(
        self,
        actor,
        character_id,
        title,
    ):
        super().__init__(title=title)

        self.actor = actor
        self.character_id = character_id
        self.submitted = False
        self.submit_lock = asyncio.Lock()

    async def execute(
        self,
        interaction,
        function,
    ):
        try:
            validate_session(
                interaction,
                self.actor,
            )

            async with self.submit_lock:
                if self.submitted:
                    raise InvalidState(
                        "這份表單已提交，"
                        "請勿重複送出。"
                    )

                await interaction.response.defer(
                    ephemeral=True,
                    thinking=True,
                )

                # 以下操作於資料庫交易前，
                # 重新確認角色仍為本人正式角色。
                def work():
                    owned_active_character(
                        self.actor,
                        self.character_id,
                    )

                    return function()

                result = await asyncio.to_thread(
                    work
                )

                self.submitted = True

            await interaction.followup.send(
                result,
                ephemeral=True,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            await send_error(
                interaction,
                exc,
            )


class ShopModal(SubmissionModal):
    def __init__(
        self,
        actor,
        character_id,
        level,
    ):
        super().__init__(
            actor,
            character_id,
            "個人店鋪｜設立申請",
        )

        self.level = level

        self.shop_name = discord.ui.TextInput(
            label="店鋪名稱",
            min_length=1,
            max_length=80,
            required=True,
        )

        self.category = discord.ui.TextInput(
            label="商品類型",
            min_length=1,
            max_length=80,
            required=True,
            placeholder="例如：服裝、飾品、道具",
        )

        self.description = discord.ui.TextInput(
            label="店鋪介紹",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )

        self.add_item(self.shop_name)
        self.add_item(self.category)
        self.add_item(self.description)

    async def on_submit(self, interaction):
        name = str(
            self.shop_name.value
        ).strip()

        category = str(
            self.category.value
        ).strip()

        description = str(
            self.description.value or ""
        ).strip()

        def work():
            result = request_store(
                self.actor,
                self.character_id,
                name,
                category,
                self.level,
                description,
            )

            return (
                "🏪 **個人店鋪申請已處理**\n\n"
                f"店鋪：{name}\n"
                f"等級：{self.level}\n"
                f"申請結果：{result}"
            )

        await self.execute(
            interaction,
            work,
        )


class LoanModal(SubmissionModal):
    def __init__(
        self,
        actor,
        character_id,
        loan_type,
    ):
        title = LOAN_TYPES[
            loan_type
        ][0]

        super().__init__(
            actor,
            character_id,
            f"{title}｜申請表",
        )

        self.loan_type = loan_type

        self.amount = discord.ui.TextInput(
            label="申請金額（金幣）",
            min_length=1,
            max_length=13,
            required=True,
            placeholder="請輸入正整數，例如100000",
        )

        self.purpose = discord.ui.TextInput(
            label="貸款用途",
            style=discord.TextStyle.paragraph,
            min_length=5,
            max_length=500,
            required=True,
        )

        self.add_item(self.amount)
        self.add_item(self.purpose)

    async def on_submit(self, interaction):
        try:
            amount = int(
                str(self.amount.value).strip()
            )

            if not 1 <= amount <= 10**12:
                raise ValueError(
                    "申請金額必須介於1至"
                    "1,000,000,000,000金幣。"
                )

        except ValueError as exc:
            await send_error(
                interaction,
                exc,
            )
            return

        purpose = str(
            self.purpose.value
        ).strip()

        def work():
            number = new_application(
                self.actor,
                self.character_id,
                self.loan_type,
                amount,
                purpose,
            )

            return (
                "🏦 **貸款申請已送出**\n\n"
                f"申請編號：#{number}\n"
                f"貸款類型："
                f"{LOAN_TYPES[self.loan_type][0]}\n"
                f"貸款金額：{amount:,} 金幣\n\n"
                "等待銀行審核。"
                "核准前不會撥款。"
            )

        await self.execute(
            interaction,
            work,
        )


class CreditModal(SubmissionModal):
    def __init__(
        self,
        actor,
        character_id,
    ):
        super().__init__(
            actor,
            character_id,
            "一般信用卡｜申請表",
        )

        self.limit = discord.ui.TextInput(
            label="希望申請的信用額度",
            placeholder="50000～3000000",
            min_length=1,
            max_length=7,
            required=True,
        )

        self.add_item(self.limit)

    async def on_submit(self, interaction):
        try:
            amount = int(
                str(self.limit.value).strip()
            )

            if not 50000 <= amount <= 3000000:
                raise ValueError(
                    "額度必須介於50,000至"
                    "3,000,000金幣。"
                )

        except ValueError as exc:
            await send_error(
                interaction,
                exc,
            )
            return

        def work():
            number = apply_card(
                self.actor,
                self.character_id,
                amount,
            )

            return (
                "💳 **信用卡申請已送出**\n\n"
                f"申請編號：#{number}\n"
                f"希望額度：{amount:,}金幣\n\n"
                "等待銀行審核。"
            )

        await self.execute(
            interaction,
            work,
        )


class ShopLevelSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        character_id,
    ):
        self.actor = actor
        self.character_id = character_id

        super().__init__(
            placeholder="選擇申請的店鋪等級",
            options=[
                discord.SelectOption(
                    label=f"等級 {i}",
                    value=str(i),
                )
                for i in range(1, 8)
            ],
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        await interaction.response.send_modal(
            ShopModal(
                self.actor,
                self.character_id,
                int(self.values[0]),
            )
        )


class ShopLevelView(OwnedView):
    def __init__(
        self,
        actor,
        character_id,
    ):
        super().__init__(actor)

        self.add_item(
            ShopLevelSelect(
                actor,
                character_id,
            )
        )


class LoanTypeSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        character_id,
    ):
        self.actor = actor
        self.character_id = character_id

        super().__init__(
            placeholder="選擇貸款類型",
            options=[
                discord.SelectOption(
                    label=f"{name}｜{days}天",
                    value=key,
                )
                for key, (name, days)
                in LOAN_TYPES.items()
            ],
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        await interaction.response.send_modal(
            LoanModal(
                self.actor,
                self.character_id,
                self.values[0],
            )
        )


class LoanTypeView(OwnedView):
    def __init__(
        self,
        actor,
        character_id,
    ):
        super().__init__(actor)

        self.add_item(
            LoanTypeSelect(
                actor,
                character_id,
            )
        )


class BlackConfirmView(OwnedView):
    def __init__(
        self,
        actor,
        character_id,
    ):
        super().__init__(actor)

        self.character_id = character_id
        self.submitted = False
        self.submit_lock = asyncio.Lock()

    @discord.ui.button(
        label="🖤 確認申請黑卡",
        style=discord.ButtonStyle.success,
    )
    async def confirm(
        self,
        interaction,
        button,
    ):
        await interaction.response.defer(
            ephemeral=True,
        )

        try:
            async with self.submit_lock:
                if self.submitted:
                    raise InvalidState(
                        "這份申請已提交。"
                    )

                def work():
                    owned_active_character(
                        self.actor,
                        self.character_id,
                    )

                    return apply_black_card(
                        self.actor,
                        self.character_id,
                    )

                number = await asyncio.to_thread(
                    work
                )

                self.submitted = True

            await interaction.edit_original_response(
                content=(
                    "🖤 **黑卡申請已送出**\n\n"
                    f"申請編號：#{number}\n"
                    "等待官方銀行審核。"
                ),
                view=None,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await send_error(
                interaction,
                exc,
            )

    @discord.ui.button(
        label="取消",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(
        self,
        interaction,
        button,
    ):
        await interaction.response.edit_message(
            content="已取消本次黑卡申請操作。",
            view=None,
        )




class EventApplicationView(discord.ui.View):
    """玩家活動企劃：不額外註冊斜線指令。"""
    def __init__(self, uid):
        super().__init__(timeout=600)
        self.uid = uid

    async def interaction_check(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("這不是你的申請面板。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="建立企劃", style=discord.ButtonStyle.success)
    async def new_project(self, interaction, button):
        from modules.event_project_manager import ProjectModal
        await interaction.response.send_modal(ProjectModal())

    @discord.ui.button(label="補充企劃", style=discord.ButtonStyle.primary)
    async def edit_project(self, interaction, button):
        await interaction.response.send_modal(EventProjectNumberModal("edit"))

    @discord.ui.button(label="送交審核", style=discord.ButtonStyle.primary)
    async def submit_project(self, interaction, button):
        await interaction.response.send_modal(EventProjectNumberModal("submit"))


class EventProjectNumberModal(discord.ui.Modal, title="活動企劃案件操作"):
    event_id = discord.ui.TextInput(label="活動企劃編號", max_length=12)

    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    async def on_submit(self, interaction):
        from modules.event_project_manager import (
            fetch_event, DetailModal, submit_for_review,
        )
        try:
            eid = int(str(self.event_id).strip())
            if self.mode == "edit":
                with get_connection() as db:
                    event = fetch_event(db, interaction.guild_id, eid)
                if (str(event["creator_id"]) != str(interaction.user.id)
                    or event["status"] not in ("draft", "rejected")):
                    raise PermissionError("只能補充自己的草稿或退件企劃。")
                await interaction.response.send_modal(DetailModal(eid))
            else:
                submit_for_review(interaction.guild_id, interaction.user.id, eid)
                await interaction.response.send_message(
                    f"✅ 活動企劃 #{eid} 已送審。", ephemeral=True
                )
        except (ValueError, PermissionError, sqlite3.Error) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)


class EventApplicationButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎪 玩家活動企劃", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction):
        await interaction.response.send_message(
            "🎪 **玩家活動企劃申請**\n建立草稿 → 補充設定 → 送交官方審核。",
            view=EventApplicationView(interaction.user.id), ephemeral=True,
        )


class ApplicationHome(OwnedView):
    def __init__(
        self,
        actor,
        character_id,
    ):
        super().__init__(actor)

        self.character_id = character_id
        self.add_item(EventApplicationButton())

    @discord.ui.button(
        label="🏪 開設個人店鋪",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def shop(
        self,
        interaction,
        button,
    ):
        try:
            owned_active_character(
                self.actor,
                self.character_id,
            )

            await interaction.response.send_message(
                (
                    "🏪 **店鋪申請**\n\n"
                    "請先選擇店鋪等級。\n"
                    "押金、容量及其他限制"
                    "以伺服器商店設定為準。"
                ),
                view=ShopLevelView(
                    self.actor,
                    self.character_id,
                ),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await send_error(interaction, exc)

    @discord.ui.button(
        label="🏦 申請貸款",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def loan(
        self,
        interaction,
        button,
    ):
        try:
            owned_active_character(
                self.actor,
                self.character_id,
            )

            await interaction.response.send_message(
                (
                    "🏦 **貸款申請**\n\n"
                    "請選擇貸款種類。\n"
                    "系統會依該種類的"
                    "正式貸款規則處理。"
                ),
                view=LoanTypeView(
                    self.actor,
                    self.character_id,
                ),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await send_error(interaction, exc)

    @discord.ui.button(
        label="💳 申請信用卡",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def credit(
        self,
        interaction,
        button,
    ):
        try:
            owned_active_character(
                self.actor,
                self.character_id,
            )

            await interaction.response.send_modal(
                CreditModal(
                    self.actor,
                    self.character_id,
                )
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await send_error(interaction, exc)

    @discord.ui.button(
        label="🖤 申請黑卡 VIP",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def black(
        self,
        interaction,
        button,
    ):
        try:
            owned_active_character(
                self.actor,
                self.character_id,
            )

            await interaction.response.send_message(
                (
                    "🖤 **黑卡 VIP 升級申請**\n\n"
                    "申請條件依正式黑卡系統：\n"
                    "• 角色年滿20歲\n"
                    "• 個人銀行存款"
                    "至少100,000,000金幣\n"
                    "• 持有有效一般信用卡\n\n"
                    "按下確認後正式提出申請，"
                    "不會自動核准。"
                ),
                view=BlackConfirmView(
                    self.actor,
                    self.character_id,
                ),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await send_error(interaction, exc)

    @discord.ui.button(
        label="🎭 切換申請角色",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def switch(
        self,
        interaction,
        button,
    ):
        try:
            characters = list_characters(
                self.actor
            )

            await interaction.response.send_message(
                "請選擇要使用的正式角色。",
                view=CharacterPicker(
                    self.actor,
                    characters,
                ),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await send_error(interaction, exc)


class CharacterSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        characters,
        page,
    ):
        self.actor = actor

        items, _, _ = page_info(
            characters,
            page,
        )

        options = [
            discord.SelectOption(
                label=str(
                    row["name"] or "未命名角色"
                )[:100],
                description=(
                    f"角色 #{row['id']}｜"
                    f"{row['occupation'] or '未設定職業'}"
                )[:100],
                value=str(row["id"]),
            )
            for row in items
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="沒有正式角色",
                    value="empty",
                )
            ]

        super().__init__(
            placeholder="選擇申請使用的角色",
            options=options,
            disabled=not bool(items),
            row=0,
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        try:
            actor = validate_session(
                interaction,
                self.actor,
            )

            # 只選擇本次申請的角色，不修改其他模組的角色狀態。
            character = await asyncio.to_thread(
                owned_active_character,
                actor,
                int(self.values[0]),
            )

            await interaction.response.edit_message(
                content=application_home_text(
                    character
                ),
                view=ApplicationHome(
                    actor,
                    character["id"],
                ),
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await send_error(interaction, exc)


class CharacterPicker(OwnedView):
    def __init__(
        self,
        actor,
        characters,
        page=0,
    ):
        super().__init__(actor)

        self.characters = characters

        _, page, last = page_info(
            characters,
            page,
        )

        self.page = page
        self.last = last

        self.add_item(
            CharacterSelect(
                actor,
                characters,
                page,
            )
        )

        self.previous.disabled = page == 0
        self.next.disabled = page >= last

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous(
        self,
        interaction,
        button,
    ):
        next_page = self.page - 1

        await interaction.response.edit_message(
            content=(
                "🎭 **選擇申請角色**\n"
                f"第{next_page + 1}/{self.last + 1}頁"
            ),
            view=CharacterPicker(
                self.actor,
                self.characters,
                next_page,
            ),
        )

    @discord.ui.button(
        label="下一頁 ➡️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next(
        self,
        interaction,
        button,
    ):
        next_page = self.page + 1

        await interaction.response.edit_message(
            content=(
                "🎭 **選擇申請角色**\n"
                f"第{next_page + 1}/{self.last + 1}頁"
            ),
            view=CharacterPicker(
                self.actor,
                self.characters,
                next_page,
            ),
        )


class PlayerApplications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="申請",
        description="玩家申請、活動企劃、店鋪與金融申請",
    )
    @app_commands.guild_only()
    async def applications(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = actor_from(
                interaction
            )

            try:
                character = await asyncio.to_thread(
                    resolve_current,
                    actor,
                )

            except InvalidState:
                character = None

            if character is not None:
                await interaction.response.send_message(
                    application_home_text(
                        character
                    ),
                    view=ApplicationHome(
                        actor,
                        character["id"],
                    ),
                    ephemeral=True,
                )
                return

            characters = await asyncio.to_thread(
                list_characters,
                actor,
            )

            if not characters:
                await interaction.response.send_message(
                    (
                        "目前沒有可以使用的正式角色。\n"
                        "請先完成 /創角、"
                        "雙階段審核及角色啟用。\n"
                        "活動企劃不需要正式角色也能提出。"
                    ),
                    view=EventApplicationView(interaction.user.id),
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                (
                    "🎭 **選擇申請角色**\n\n"
                    "你有多名正式角色，"
                    "請先選擇要使用的角色。"
                ),
                view=CharacterPicker(
                    actor,
                    characters,
                ),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await send_error(
                interaction,
                exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        PlayerApplications(bot)
    )
