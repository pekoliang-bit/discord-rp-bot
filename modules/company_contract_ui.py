"""Discord 公司員工合約介面。

由 /公司 既有介面建立 CompanyContractView。
不註冊新的 Slash Command。
"""

from __future__ import annotations

import asyncio
import logging

import discord

from core.company_public_ids import contract_code, public_company
from core.database import transaction
from core.company_contract_service import accept_contract
from core.company_contract_review import (
    list_employee_contracts,
    reject_contract,
)

LOG = logging.getLogger(__name__)


class ContractDecisionModal(discord.ui.Modal):

    def __init__(self, actor_id, guild_id, action):
        title = (
            "確認員工合約"
            if action == "accept"
            else "拒絕員工合約"
        )

        super().__init__(
            title=title,
            timeout=300,
        )

        self.actor_id = str(actor_id)
        self.guild_id = str(guild_id)
        self.action = action

        self.contract_id = discord.ui.TextInput(
            label="合約編號",
            placeholder="請填寫合約清單中的編號",
            max_length=20,
            required=True,
        )

        self.add_item(self.contract_id)

    async def on_submit(self, interaction):
        if (
            str(interaction.user.id) != self.actor_id
            or str(interaction.guild_id) != self.guild_id
        ):
            await interaction.response.send_message(
                "這不是你的合約表單。",
                ephemeral=True,
            )
            return

        try:
            cid = self.contract_id.value.strip()
            if not cid:
                raise ValueError("合約編號不能空白")

        except ValueError:
            await interaction.response.send_message(
                "請輸入有效的合約編號。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            service = (
                accept_contract
                if self.action == "accept"
                else reject_contract
            )

            result = await asyncio.to_thread(
                service,
                self.guild_id,
                cid,
                self.actor_id,
            )

        except (
            ValueError,
            PermissionError,
            LookupError,
            RuntimeError,
        ) as exc:
            result = f"⚠️ {exc}"

        except Exception:
            LOG.exception("公司合約處理失敗")
            result = (
                "⚠️ 操作狀態無法確認，"
                "請管理員核對合約紀錄。"
            )

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


class CompanyContractView(discord.ui.View):

    def __init__(self, guild_id, actor_id):
        super().__init__(timeout=300)

        self.guild_id = str(guild_id)
        self.actor_id = str(actor_id)

    async def interaction_check(self, interaction):
        allowed = (
            str(interaction.guild_id) == self.guild_id
            and str(interaction.user.id) == self.actor_id
        )

        if not allowed:
            await interaction.response.send_message(
                "這不是你的合約管理面板。",
                ephemeral=True,
            )

        return allowed

    @discord.ui.button(
        label="查看我的合約",
        style=discord.ButtonStyle.primary,
    )
    async def view_contracts(self, interaction, button):

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            rows = await asyncio.to_thread(
                list_employee_contracts,
                self.guild_id,
                self.actor_id,
            )

            if not rows:
                result = "目前沒有待確認或生效中的合約。"

            else:
                lines = ["📄 **我的員工合約**"]

                with transaction() as db:
                    visible = {int(row['id']): (contract_code(db, self.guild_id, row['id']),
                               public_company(db, self.guild_id, row['company_id'])) for row in rows}
                for row in rows:
                    status = {
                        "pending": "待確認",
                        "active": "已生效",
                    }.get(
                        row["status"],
                        row["status"],
                    )

                    lines.append(
                        f"\n**合約 #{visible[int(row['id'])][0]}**"
                        f"｜{status}\n"
                        f"公司：{visible[int(row['id'])][1]}"
                        f"｜角色 #{row['character_id']}\n"
                        f"薪資：{int(row['salary']):,} 金幣"
                        f"／{row['cycle_days']}天\n"
                        f"違約金：{int(row['penalty']):,} 金幣"
                    )

                result = "\n".join(lines)

        except Exception:
            LOG.exception("讀取合約失敗")
            result = "⚠️ 無法取得合約清單。"

        await interaction.followup.send(
            result[:1900],
            ephemeral=True,
        )

    @discord.ui.button(
        label="接受合約",
        style=discord.ButtonStyle.success,
    )
    async def accept(self, interaction, button):
        await interaction.response.send_modal(
            ContractDecisionModal(
                self.actor_id,
                self.guild_id,
                "accept",
            )
        )

    @discord.ui.button(
        label="拒絕合約",
        style=discord.ButtonStyle.danger,
    )
    async def reject(self, interaction, button):
        await interaction.response.send_modal(
            ContractDecisionModal(
                self.actor_id,
                self.guild_id,
                "reject",
            )
        )
