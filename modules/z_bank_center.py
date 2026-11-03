"""Discord RP：統一互動式銀行中心。

正式入口：/銀行

功能：
- 自動使用目前啟用的角色
- 現金與活期餘額
- 存款、提款、轉帳
- 14天／30天定存
- 定存提前解約
- 貸款查詢與還款
- 信用卡查詢與還款
- 黑卡 VIP 查詢

所有金錢交易沿用既有核心函式。
不直接 UPDATE 餘額。
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from core.economy import get_balance
from core.permissions import Actor

from modules.bank_shortcuts import (
    resolve_character,
)
from modules.economy import (
    bank_move,
    free_transfer,
    create_term,
    settle_term,
    rate_for,
)
from modules.loans import (
    loan_summary,
    repay_loan,
)
from modules.credit_cards import (
    card_summary,
    manual_pay,
)
from modules.black_card import vip_status

LOG = logging.getLogger(__name__)

MAX_AMOUNT = 10**15


def amount_value(raw):
    value = int(str(raw).strip())

    if not 1 <= value <= MAX_AMOUNT:
        raise ValueError(
            "金額必須介於 1 至 "
            "1,000,000,000,000,000。"
        )

    return value


def positive_id(raw):
    value = int(str(raw).strip())

    if value <= 0:
        raise ValueError(
            "編號必須為正整數。"
        )

    return value


def verify(interaction, actor, character_id):
    actual = Actor.from_interaction(
        interaction
    )

    if (
        actual.guild_id != actor.guild_id
        or actual.user_id != actor.user_id
    ):
        raise ValueError(
            "這不是你的銀行介面。"
        )

    character = resolve_character(
        actual,
        character_id,
    )

    return actual, character


def bank_text(actor, character):
    character_id = int(
        character["id"]
    )

    cash = get_balance(
        actor.guild_id,
        "character",
        character_id,
        "wallet",
    )

    savings = get_balance(
        actor.guild_id,
        "character",
        character_id,
        "bank",
    )

    with get_connection() as conn:
        deposits = conn.execute(
            """
            SELECT id, principal, matures_at
            FROM bank_deposits
            WHERE guild_id = ?
              AND character_id = ?
              AND status = 'active'
            ORDER BY id DESC
            LIMIT 10
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchall()

    rate = rate_for(
        actor.guild_id
    )

    rate_text = (
        f"{rate / 100:.2f}%"
        if rate is not None
        else "尚未設定"
    )

    lines = [
        "🏦 **銀行管理中心**",
        "",
        f"🎭 目前角色：{character['name']}",
        f"角色編號：#{character_id}",
        "",
        f"💵 現金：{cash:,}",
        f"🏛️ 活期存款：{savings:,}",
        f"📈 官方年利率：{rate_text}",
        "",
        "**📁 進行中的定期存款**",
    ]

    if deposits:
        for item in deposits:
            lines.append(
                f"#{item['id']}｜"
                f"{item['principal']:,} 金幣｜"
                f"{str(item['matures_at'])[:10]}到期"
            )
    else:
        lines.append("目前沒有定存。")

    lines += [
        "",
        "請使用下方按鈕操作。",
        "每筆交易都會由原有銀行核心驗證。",
    ]

    return "\n".join(lines)[:1900]


async def show_error(interaction, exc):
    LOG.error(
        "銀行中心操作失敗",
        exc_info=(
            type(exc),
            exc,
            exc.__traceback__,
        ),
    )

    if isinstance(
        exc,
        (ValueError, TypeError),
    ):
        message = str(exc)
    else:
        # 既有銀行業務錯誤通常帶有
        # 可以直接顯示給玩家的說明。
        name = type(exc).__name__

        if name in (
            "EconomyError",
            "InvalidState",
            "PermissionDenied",
        ):
            message = str(exc)
        else:
            message = (
                f"銀行發生 {name} 錯誤，"
                "請管理員查看 Railway 日誌。"
            )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                f"⚠️ {message[:1700]}",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"⚠️ {message[:1700]}",
                ephemeral=True,
            )
    except discord.HTTPException:
        LOG.exception(
            "銀行錯誤訊息無法送出"
        )


class BankModal(discord.ui.Modal):
    def __init__(
        self,
        actor,
        character_id,
        action,
    ):
        titles = {
            "deposit": "銀行｜存款",
            "withdraw": "銀行｜提款",
            "transfer": "銀行｜角色轉帳",
            "term": "銀行｜建立定存",
            "close_term": "銀行｜提前解約",
            "loan": "銀行｜貸款還款",
            "card": "銀行｜信用卡繳款",
        }

        super().__init__(
            title=titles[action]
        )

        self.actor = actor
        self.character_id = character_id
        self.action = action
        self.inputs = {}

        if action == "transfer":
            self.add_input(
                "receiver",
                "收款角色編號",
            )

        if action == "close_term":
            self.add_input(
                "term_id",
                "定存編號",
            )

        if action == "loan":
            self.add_input(
                "loan_id",
                "貸款編號",
            )

        if action == "card":
            self.add_input(
                "card_id",
                "信用卡編號",
            )

        if action not in (
            "close_term",
        ):
            self.add_input(
                "amount",
                "金額（金幣）",
            )

        if action == "term":
            self.add_input(
                "days",
                "期限（填 14 或 30）",
                default="14",
            )

    def add_input(
        self,
        key,
        label,
        default=None,
    ):
        field = discord.ui.TextInput(
            label=label,
            default=default,
            required=True,
            max_length=30,
        )

        self.inputs[key] = field
        self.add_item(field)

    def value(self, key):
        return str(
            self.inputs[key].value
        ).strip()

    def operate(self, actor):
        action = self.action
        character_id = self.character_id

        if action == "close_term":
            term_id = positive_id(
                self.value("term_id")
            )

            with get_connection() as conn:
                item = conn.execute(
                    """
                    SELECT character_id
                    FROM bank_deposits
                    WHERE guild_id = ?
                      AND id = ?
                    """,
                    (
                        actor.guild_id,
                        term_id,
                    ),
                ).fetchone()

            if (
                item is None
                or int(item["character_id"])
                != character_id
            ):
                raise ValueError(
                    "找不到這名角色的定存。"
                )

            completed = settle_term(
                actor.guild_id,
                term_id,
                early=True,
                actor=actor,
            )

            return (
                "✅ 定存已解約並完成結算。"
                if completed
                else "這筆定存已結清。"
            )

        amount = amount_value(
            self.value("amount")
        )

        if action == "deposit":
            result = bank_move(
                actor,
                character_id,
                amount,
                "deposit",
            )

            return (
                f"✅ 成功存入 {amount:,} 金幣\n"
                f"活期餘額："
                f"{result.receiver_balance:,}"
            )

        if action == "withdraw":
            result = bank_move(
                actor,
                character_id,
                amount,
                "withdraw",
            )

            return (
                f"✅ 成功提款 {amount:,} 金幣\n"
                f"活期餘額："
                f"{result.sender_balance:,}"
            )

        if action == "transfer":
            receiver = positive_id(
                self.value("receiver")
            )

            result = free_transfer(
                actor,
                character_id,
                receiver,
                amount,
            )

            return (
                "✅ 轉帳成功\n"
                f"#{character_id} → #{receiver}\n"
                f"金額：{amount:,}\n"
                "手續費：0\n"
                f"交易編號：{result.transaction_id}"
            )

        if action == "term":
            days = int(
                self.value("days")
            )

            if days not in (14, 30):
                raise ValueError(
                    "定存期限只能填 14 或 30。"
                )

            term_id = create_term(
                actor,
                character_id,
                amount,
                days,
            )

            return (
                f"✅ 已建立定存 #{term_id}\n"
                f"本金：{amount:,}\n"
                f"期限：{days}天\n"
                "到期由原有銀行系統結算。"
            )

        if action == "loan":
            loan_id = positive_id(
                self.value("loan_id")
            )

            result = repay_loan(
                actor,
                loan_id,
                amount,
                uuid4().hex,
            )

            return (
                "✅ 貸款還款完成\n"
                f"貸款：#{loan_id}\n"
                f"本次還款：{result['paid']:,}\n"
                f"剩餘應還：{result['remaining']:,}"
            )

        if action == "card":
            card_id = positive_id(
                self.value("card_id")
            )

            remaining = manual_pay(
                actor,
                card_id,
                amount,
                uuid4().hex,
            )

            return (
                "✅ 信用卡繳款完成\n"
                f"卡片：#{card_id}\n"
                f"本次繳款：{amount:,}\n"
                f"剩餘本期應繳：{remaining:,}"
            )

        raise ValueError(
            "未知的銀行操作。"
        )

    async def on_submit(self, interaction):
        try:
            actor, _ = verify(
                interaction,
                self.actor,
                self.character_id,
            )

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            result = await asyncio.to_thread(
                self.operate,
                actor,
            )

            await interaction.followup.send(
                result,
                ephemeral=True,
            )

        except Exception as exc:
            await show_error(
                interaction,
                exc,
            )

    async def on_error(
        self,
        interaction,
        error,
    ):
        await show_error(
            interaction,
            error,
        )


class BankView(discord.ui.View):
    def __init__(
        self,
        actor,
        character_id,
        page=0,
    ):
        super().__init__(
            timeout=900
        )

        self.actor = actor
        self.character_id = character_id
        self.page = page

        if page == 0:
            self.add_action(
                "💰 存款",
                "deposit",
                0,
                discord.ButtonStyle.success,
            )
            self.add_action(
                "💵 提款",
                "withdraw",
                0,
            )
            self.add_action(
                "💸 轉帳",
                "transfer",
                0,
            )
            self.add_action(
                "📈 定存事務", "term_menu", 1,
            )
        else:
            self.add_action(
                "💳 信用卡繳款",
                "card",
                0,
            )
            self.add_action(
                "🏦 貸款還款",
                "loan",
                0,
            )

    def add_action(
        self,
        label,
        action,
        row,
        style=discord.ButtonStyle.primary,
    ):
        button = discord.ui.Button(
            label=label,
            style=style,
            row=row,
        )

        async def callback(interaction):
            if action == "term_menu":
                await interaction.response.send_message(
                    "📈 **定存事務**\n請選擇建立定存或定存解約。",
                    view=TermBusinessView(self.actor, self.character_id),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_modal(
                    BankModal(self.actor, self.character_id, action)
                )

        button.callback = callback
        self.add_item(button)

    async def interaction_check(
        self,
        interaction,
    ):
        try:
            verify(
                interaction,
                self.actor,
                self.character_id,
            )
            return True

        except Exception as exc:
            await show_error(
                interaction,
                exc,
            )
            return False

    async def on_error(
        self,
        interaction,
        error,
        item,
    ):
        await show_error(
            interaction,
            error,
        )

    @discord.ui.button(
        label="🏠 帳戶首頁",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def home(
        self,
        interaction,
        button,
    ):
        try:
            actor, character = verify(
                interaction,
                self.actor,
                self.character_id,
            )

            await interaction.response.edit_message(
                content=bank_text(
                    actor,
                    character,
                ),
                view=BankView(
                    actor,
                    self.character_id,
                    0,
                ),
            )
        except Exception as exc:
            await show_error(
                interaction,
                exc,
            )

    @discord.ui.button(
        label="📋 申辦進度",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def finance(
        self,
        interaction,
        button,
    ):
        try:
            actor, character = verify(
                interaction,
                self.actor,
                self.character_id,
            )

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            def work():
                return (
                    "🏦 **金融服務中心**\n"
                    f"角色：{character['name']}\n\n"
                    "**貸款紀錄**\n"
                    + loan_summary(
                        actor,
                        self.character_id,
                    )[:650]
                    + "\n\n**信用卡紀錄**\n"
                    + card_summary(
                        actor,
                        self.character_id,
                    )[:650]
                    + "\n\n"
                    + vip_status(
                        actor,
                        self.character_id,
                    )[:350]
                )[:1900]

            content = await asyncio.to_thread(
                work
            )

            from modules.company_bank_applications import format_company_application_status
            try:
                content += "\n\n" + (await asyncio.to_thread(
                    format_company_application_status, actor.guild_id, interaction.user.id
                ))[:600]
            except Exception:
                LOG.exception("company bank application status unavailable")
            await interaction.followup.send(
                content[:1900],
                view=FinanceRepaymentView(
                    actor,
                    self.character_id,
                ),
                ephemeral=True,
            )

        except Exception as exc:
            await show_error(
                interaction,
                exc,
            )

    @discord.ui.button(
        label="🔄 重新整理",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def refresh(
        self,
        interaction,
        button,
    ):
        try:
            actor, character = verify(
                interaction,
                self.actor,
                self.character_id,
            )

            await interaction.response.edit_message(
                content=bank_text(
                    actor,
                    character,
                ),
                view=BankView(
                    actor,
                    self.character_id,
                    0,
                ),
            )
        except Exception as exc:
            await show_error(
                interaction,
                exc,
            )


class BankCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="銀行",
        description="互動式銀行：存提款、轉帳、定存、貸款與信用卡",
    )
    @app_commands.guild_only()
    async def bank(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            character = await asyncio.to_thread(
                resolve_character,
                actor,
            )

            content = await asyncio.to_thread(
                bank_text,
                actor,
                character,
            )

            await interaction.followup.send(
                content,
                view=BankView(
                    actor,
                    int(character["id"]),
                ),
                ephemeral=True,
            )
            try:
                from modules.task_event_bridge import record_successful_command_interaction
                record_successful_command_interaction(interaction, '銀行')
            except Exception:
                pass  # 教學紀錄不得影響原有功能

        except Exception as exc:
            await show_error(
                interaction,
                exc,
            )

# ==================================================
# 銀行第二階段：
# 貸款申請、信用卡申請、黑卡申請、交易紀錄
# ==================================================

from core.economy import get_transaction_history

from modules.player_applications import (
    LoanTypeView,
    CreditModal,
    BlackConfirmView,
)


class FinanceRepaymentView(discord.ui.View):
    def __init__(self, actor, character_id):
        super().__init__(timeout=900)
        self.actor = actor
        self.character_id = character_id
        for label, action in (
            ("💳 信用卡繳款", "card"),
            ("🏦 貸款還款", "loan"),
        ):
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            async def handle(interaction, action=action):
                try:
                    verify(interaction, self.actor, self.character_id)
                    await interaction.response.send_modal(
                        BankModal(self.actor, self.character_id, action)
                    )
                except Exception as exc:
                    await show_error(interaction, exc)
            button.callback = handle
            self.add_item(button)


class TermBusinessView(discord.ui.View):
    """定存建立及解約的共同入口。"""
    def __init__(self, actor, character_id):
        super().__init__(timeout=900)
        self.actor = actor
        self.character_id = character_id

    async def interaction_check(self, interaction):
        try:
            verify(interaction, self.actor, self.character_id)
            return True
        except Exception as exc:
            await show_error(interaction, exc)
            return False

    @discord.ui.button(label="📈 建立定存", style=discord.ButtonStyle.success)
    async def create_term(self, interaction, button):
        await interaction.response.send_modal(
            BankModal(self.actor, self.character_id, "term")
        )

    @discord.ui.button(label="📤 定存解約", style=discord.ButtonStyle.danger)
    async def close_term(self, interaction, button):
        await interaction.response.send_modal(
            BankModal(self.actor, self.character_id, "close_term")
        )


class BankViewV2(BankView):
    """在原有銀行面板上增加第二階段入口。"""

    def __init__(
        self,
        actor,
        character_id,
        page=0,
    ):
        super().__init__(
            actor,
            character_id,
            page,
        )

        if page == 0:
            self.add_extra_button(
                "📜 交易紀錄", self.open_history, row=1,
            )
            self.add_extra_button(
                "🏢 公司帳戶", self.open_company_banking, row=1,
            )
    def add_extra_button(
        self,
        label,
        handler,
        *,
        row,
        style=discord.ButtonStyle.primary,
    ):
        button = discord.ui.Button(
            label=label,
            style=style,
            row=row,
        )

        button.callback = handler
        self.add_item(button)

    async def open_company_banking(self, interaction: discord.Interaction):
        from modules.company_bank_portal import open_company_banking
        try:
            verify(interaction, self.actor, self.character_id)
        except Exception as exc:
            return await show_error(interaction, exc)
        await open_company_banking(interaction)

    async def open_loan_application(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor, character = verify(
                interaction,
                self.actor,
                self.character_id,
            )

            await interaction.response.send_message(
                (
                    "🏦 **貸款申請中心**\n\n"
                    f"申請角色：{character['name']}\n"
                    "請選擇貸款類型。\n\n"
                    "申請會沿用原有貸款規則，"
                    "不會直接核准或發放款項。"
                ),
                view=LoanTypeView(
                    actor,
                    self.character_id,
                ),
                ephemeral=True,
            )

        except Exception as exc:
            await show_error(
                interaction,
                exc,
            )

    async def open_credit_application(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor, _ = verify(
                interaction,
                self.actor,
                self.character_id,
            )

            await interaction.response.send_modal(
                CreditModal(
                    actor,
                    self.character_id,
                )
            )

        except Exception as exc:
            await show_error(
                interaction,
                exc,
            )

    async def open_black_application(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor, character = verify(
                interaction,
                self.actor,
                self.character_id,
            )

            await interaction.response.send_message(
                (
                    "🖤 **黑卡 VIP 申請**\n\n"
                    f"申請角色：{character['name']}\n\n"
                    "原有申請條件：\n"
                    "• 年滿 20 歲\n"
                    "• 個人銀行存款至少"
                    " 100,000,000 金幣\n"
                    "• 持有有效的一般信用卡\n\n"
                    "送出後仍需管理員審核。"
                ),
                view=BlackConfirmView(
                    actor,
                    self.character_id,
                ),
                ephemeral=True,
            )

        except Exception as exc:
            await show_error(
                interaction,
                exc,
            )

    async def open_history(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor, character = verify(
                interaction,
                self.actor,
                self.character_id,
            )

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            def fetch_history():
                wallet = get_transaction_history(
                    actor.guild_id,
                    "character",
                    self.character_id,
                    "wallet",
                    limit=10,
                )

                savings = get_transaction_history(
                    actor.guild_id,
                    "character",
                    self.character_id,
                    "bank",
                    limit=10,
                )

                return wallet, savings

            wallet, savings = await asyncio.to_thread(
                fetch_history
            )

            lines = [
                "📜 **個人交易紀錄**",
                f"角色：{character['name']}",
                "",
                "💵 **現金帳戶**",
            ]

            if wallet:
                for record in wallet:
                    amount = record["amount"]

                    sign = (
                        "+"
                        if amount >= 0
                        else ""
                    )

                    lines.append(
                        f"#{record['transaction_id']} "
                        f"{sign}{amount:,}\n"
                        f"  {str(record['description'])[:50]}"
                    )
            else:
                lines.append(
                    "目前沒有交易。"
                )

            lines += [
                "",
                "🏛️ **銀行活期帳戶**",
            ]

            if savings:
                for record in savings:
                    amount = record["amount"]

                    sign = (
                        "+"
                        if amount >= 0
                        else ""
                    )

                    lines.append(
                        f"#{record['transaction_id']} "
                        f"{sign}{amount:,}\n"
                        f"  {str(record['description'])[:50]}"
                    )
            else:
                lines.append(
                    "目前沒有交易。"
                )

            await interaction.followup.send(
                "\n".join(lines)[:1900],
                ephemeral=True,
            )

        except Exception as exc:
            await show_error(
                interaction,
                exc,
            )


# 保留原本所有 BankView 呼叫方式。
# 從這裡開始，新建立的銀行面板
# 會自動使用擴充後的版本。
BankView = BankViewV2


async def setup(bot: commands.Bot):
    # 原 /銀行 由 modules.economy 註冊。
    # 只移除指令入口，不卸載原金融核心。
    previous = bot.tree.get_command(
        "銀行"
    )

    if previous is not None:
        if not isinstance(
            previous,
            app_commands.Command,
        ):
            raise RuntimeError(
                "/銀行 不是一般斜線指令，"
                "停止覆蓋以避免破壞原功能。"
            )

        bot.tree.remove_command(
            "銀行",
            type=discord.AppCommandType.chat_input,
        )

    await bot.add_cog(
        BankCenter(bot)
    )

    LOG.info(
        "已啟用新版 /銀行 互動面板，"
        "沿用原金融交易核心。"
    )
