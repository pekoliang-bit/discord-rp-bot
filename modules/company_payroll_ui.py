
"""公司薪資週期 Discord 管理介面。

入口：/公司 → 金庫管理 → 薪資週期
不新增 Slash Command。
"""

from __future__ import annotations

import asyncio
import logging

import discord

from core.company_payroll_service import (
    configure_payroll_cycle,
)
from core.company_payroll_read import (
    payroll_cycle_summary,
)

LOG = logging.getLogger(__name__)


class PayrollCycleModal(discord.ui.Modal):
    def __init__(self, guild_id, business_id, user_id):
        super().__init__(
            title="設定公司薪資週期",
            timeout=300,
        )

        self.guild_id = str(guild_id)
        self.business_id = str(business_id)
        self.user_id = str(user_id)

        self.cycle = discord.ui.TextInput(
            label="發薪週期：7 或 14 天",
            placeholder="請輸入 7 或 14",
            max_length=2,
            required=True,
        )

        self.confirm = discord.ui.TextInput(
            label="輸入「確認設定」",
            max_length=8,
            required=True,
        )

        self.add_item(self.cycle)
        self.add_item(self.confirm)

    async def on_submit(self, interaction):
        if (
            str(interaction.guild_id) != self.guild_id
            or str(interaction.user.id) != self.user_id
        ):
            return await interaction.response.send_message(
                "這不是你的薪資設定表單。",
                ephemeral=True,
            )

        if self.confirm.value.strip() != "確認設定":
            return await interaction.response.send_message(
                "已取消，薪資週期沒有變更。",
                ephemeral=True,
            )

        try:
            days = int(self.cycle.value.strip())

            if days not in (7, 14):
                raise ValueError(
                    "發薪週期只能是7天或14天。"
                )

        except ValueError as exc:
            return await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            result = await asyncio.to_thread(
                configure_payroll_cycle,
                self.guild_id,
                self.business_id,
                self.user_id,
                days,
            )

        except (PermissionError, ValueError) as exc:
            result = f"⚠️ {exc}"

        except Exception:
            LOG.exception("公司薪資設定失敗")
            result = (
                "⚠️ 薪資設定未確認完成，"
                "請管理員檢查紀錄。"
            )

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


class PayrollCycleButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="📅 薪資週期",
            style=discord.ButtonStyle.secondary,
            row=2,
        )

    async def callback(self, interaction):
        view = self.view

        if (
            str(interaction.guild_id) != view.guild_id
            or str(interaction.user.id) != view.user_id
        ):
            return await interaction.response.send_message(
                "這不是你的公司管理面板。",
                ephemeral=True,
            )

        # 不依賴面板建立時的持有人資料。
        # 每次按下都重新核對。
        from modules.company_treasury_ui import (
            manageable_finance_businesses,
        )

        businesses = await asyncio.to_thread(
            manageable_finance_businesses,
            view.guild_id,
            view.user_id,
        )

        current = next(
            (
                item for item in businesses
                if item["business_id"]
                == view.business_id
            ),
            None,
        )

        if (
            current is None
            or not current["is_owner"]
            or current["kind"] != "company"
        ):
            return await interaction.response.send_message(
                "只有正式公司的現任持有人"
                "能設定薪資週期。",
                ephemeral=True,
            )

        await interaction.response.send_modal(
            PayrollCycleModal(
                view.guild_id,
                view.business_id,
                view.user_id,
            )
        )


class PayrollCycleSummaryButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="📋 薪資設定",
            style=discord.ButtonStyle.secondary,
            row=2,
        )

    async def callback(self, interaction):
        view = self.view

        if (
            str(interaction.guild_id) != view.guild_id
            or str(interaction.user.id) != view.user_id
        ):
            return await interaction.response.send_message(
                "這不是你的管理面板。",
                ephemeral=True,
            )

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            text = await asyncio.to_thread(
                payroll_cycle_summary,
                view.guild_id,
                view.business_id,
                view.user_id,
            )

        except (PermissionError, ValueError) as exc:
            text = f"⚠️ {exc}"

        except Exception:
            LOG.exception("薪資週期查詢失敗")
            text = "⚠️ 暫時無法查看薪資設定。"

        await interaction.followup.send(
            str(text)[:1900],
            ephemeral=True,
        )
