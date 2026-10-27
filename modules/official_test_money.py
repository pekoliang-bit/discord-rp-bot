
"""Discord RP｜官方金幣管理。

功能：
- 發放角色金幣
- 扣除角色金幣
- 設定角色錢包餘額
- 查詢角色錢包餘額
- 保留銀行交易紀錄
- 防止重複交易

不建立額外斜線指令。
入口由 /官方 面板提供。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord.ext import commands

from core.database import (
    account_balance,
    get_or_create_account,
    transaction,
)

from core.economy import _payment, MAX_AMOUNT
from core.permissions import Actor, PermissionDenied


LOG = logging.getLogger(__name__)


# ==========================================
# 官方權限
# ==========================================

def _authorized(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False

    if interaction.user is None:
        return False

    # 群主擁有完整操作權限。
    if interaction.user.id == interaction.guild.owner_id:
        return True

    # 其他管理員沿用權限核心。
    actor = Actor.from_interaction(interaction)

    return bool(actor.is_admin)


def _require_authorized(interaction):
    if not _authorized(interaction):
        raise PermissionDenied(
            "僅限群主或具備管理權限的人員操作。"
        )


# ==========================================
# 金幣管理核心
# ==========================================

def _money_operation(
    guild_id,
    character_id,
    mode,
    amount,
    request_key,
    operator_id,
):
    if character_id <= 0:
        raise ValueError(
            "角色編號必須大於零。"
        )

    if mode not in (
        "grant",
        "deduct",
        "set",
        "query",
    ):
        raise ValueError(
            "操作類型錯誤。"
        )

    if mode != "query":
        if not 0 <= amount <= MAX_AMOUNT:
            raise ValueError(
                f"金額必須介於 0～{MAX_AMOUNT:,}。"
            )

    if mode in ("grant", "deduct"):
        if amount == 0:
            raise ValueError(
                "發放或扣除金額必須大於零。"
            )

    with transaction() as conn:

        # 確認角色存在。
        character = conn.execute(
            """
            SELECT id, name, status
            FROM characters
            WHERE guild_id=?
              AND id=?
            """,
            (
                str(guild_id),
                character_id,
            ),
        ).fetchone()

        if (
            character is None
            or character["status"] == "deleted"
        ):
            raise ValueError(
                "找不到有效的角色。"
            )

        # 取得角色錢包。
        wallet = get_or_create_account(
            conn,
            guild_id,
            "character",
            str(character_id),
            "wallet",
        )

        balance = int(
            account_balance(
                conn,
                guild_id,
                wallet,
            )
        )

        # ==================================
        # 查詢餘額
        # ==================================

        if mode == "query":
            return (
                f"💰 **{character['name']}**"
                f"（#{character_id}）\n"
                f"目前錢包餘額："
                f"**{balance:,} 金幣**"
            )

        # ==================================
        # 計算實際調整金額
        # ==================================

        if mode == "set":
            delta = amount - balance

            if delta == 0:
                return (
                    "✅ 錢包已經是 "
                    f"{balance:,} 金幣，"
                    "不需要再次調整。"
                )

            direction = (
                "grant"
                if delta > 0
                else "deduct"
            )

            transfer_amount = abs(delta)

        else:
            direction = mode
            transfer_amount = amount

        if transfer_amount > MAX_AMOUNT:
            raise ValueError(
                "本次實際調整金額超過單筆交易上限。"
            )

        # 官方專用系統帳戶。
        treasury = get_or_create_account(
            conn,
            guild_id,
            "system",
            "official_test_money",
            "treasury",
        )

        if (
            direction == "deduct"
            and balance < transfer_amount
        ):
            raise ValueError(
                "錢包餘額不足："
                f"目前 {balance:,} 金幣，"
                f"欲扣除 {transfer_amount:,} 金幣。"
            )

        if direction == "grant":
            sender = treasury
            receiver = wallet

        else:
            sender = wallet
            receiver = treasury

        # ==================================
        # 使用銀行交易核心
        # ==================================

        result = _payment(
            conn,
            guild_id,
            f"official-money:{request_key}",
            sender,
            receiver,
            transfer_amount,
            (
                "官方金幣管理｜"
                f"操作者 {operator_id}｜"
                f"角色 #{character_id}｜"
                f"{mode}"
            ),
        )

        # 重新讀取交易後的餘額。
        current = int(
            account_balance(
                conn,
                guild_id,
                wallet,
            )
        )

        verb = (
            "增加"
            if direction == "grant"
            else "扣除"
        )

        duplicated = (
            "（此交易已處理過，未重複轉帳）"
            if result.duplicated
            else ""
        )

        return (
            f"✅ **{character['name']}**"
            f"（#{character_id}）"
            f"{duplicated}\n"
            f"{verb}："
            f"{transfer_amount:,} 金幣\n"
            f"目前錢包："
            f"**{current:,} 金幣**\n"
            f"帳本交易："
            f"#{result.transaction_id}"
        )


# ==========================================
# 面板文字
# ==========================================

def _home_text():
    return (
        "💰 **官方金幣管理**\n\n"
        "可以直接管理角色錢包。\n\n"
        "💸 發放：增加角色金幣\n"
        "➖ 扣除：扣除角色金幣\n"
        "🎯 設定餘額：指定錢包最終金額\n"
        "🔎 查詢：查看角色目前金幣\n\n"
        "📒 所有金額調整皆透過銀行帳本。\n\n"
        "⚠️ 這是真實角色金幣，"
        "不是獨立測試貨幣。"
    )


# ==========================================
# 金幣管理表單
# ==========================================

class OfficialMoneyModal(
    discord.ui.Modal,
    title="官方金幣管理",
):
    def __init__(self, mode):
        super().__init__()

        self.mode = mode

        self.character_id = discord.ui.TextInput(
            label="角色編號",
            placeholder="例如：12",
            required=True,
            max_length=12,
        )

        self.add_item(self.character_id)

        if mode != "query":
            self.amount = discord.ui.TextInput(
                label=(
                    "設定錢包最終餘額"
                    if mode == "set"
                    else "金幣數量"
                ),
                placeholder="例如：1000000",
                required=True,
                max_length=16,
            )

            self.add_item(self.amount)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        try:
            _require_authorized(interaction)

            character_id = int(
                str(self.character_id.value).strip()
            )

            amount = (
                int(str(self.amount.value).strip())
                if self.mode != "query"
                else 0
            )

            if amount < 0:
                raise ValueError(
                    "金額不得為負數。"
                )

        except (
            ValueError,
            PermissionDenied,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                _money_operation,
                interaction.guild_id,
                character_id,
                self.mode,
                amount,
                str(interaction.id),
                interaction.user.id,
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            result = f"⚠️ {exc}"

        except Exception:
            LOG.exception(
                "官方金幣管理操作失敗"
            )

            result = (
                "⚠️ 系統錯誤，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


# ==========================================
# 金幣管理面板
# ==========================================

class OfficialMoneyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=900)

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):
        if not _authorized(interaction):
            await interaction.response.send_message(
                "⛔ 你沒有官方金幣管理權限。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="💸 發放",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def grant(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            OfficialMoneyModal("grant")
        )

    @discord.ui.button(
        label="➖ 扣除",
        style=discord.ButtonStyle.danger,
        row=0,
    )
    async def deduct(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            OfficialMoneyModal("deduct")
        )

    @discord.ui.button(
        label="🎯 設定餘額",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def set_balance(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            OfficialMoneyModal("set")
        )

    @discord.ui.button(
        label="🔎 查詢",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def query(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            OfficialMoneyModal("query")
        )

    @discord.ui.button(
        label="↩️ 返回官方",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction,
        button,
    ):
        from modules.official_center import OfficialHome

        await interaction.response.edit_message(
            content="🏛️ **官方人員管理中心**",
            view=OfficialHome(interaction),
        )


# ==========================================
# 官方首頁入口按鈕
# ==========================================

class MoneyManageButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="💰 金幣管理",
            style=discord.ButtonStyle.success,
            row=3,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if not _authorized(interaction):
            await interaction.response.send_message(
                "⛔ 你沒有官方金幣管理權限。",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=_home_text(),
            view=OfficialMoneyView(),
        )


# ==========================================
# 模組初始化
# ==========================================

async def setup(bot: commands.Bot):
    # 本模組不註冊新的斜線指令。
    LOG.info(
        "官方金幣管理模組已載入。"
    )
