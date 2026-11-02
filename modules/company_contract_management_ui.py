
"""Discord /公司 員工合約管理介面。

不新增任何斜線指令。
"""

from __future__ import annotations

import asyncio
import logging

import discord

from core.company_contract_service import (
    offer_contract,
    accept_contract,
)
from core.company_contract_review import (
    reject_contract,
)
from core.company_contract_offer_read import (
    contract_list,
    validate_contract_offer_target,
)

LOG = logging.getLogger(__name__)


async def reply(interaction, message):
    if interaction.response.is_done():
        await interaction.followup.send(
            str(message)[:1900],
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            str(message)[:1900],
            ephemeral=True,
        )


class ContractOfferModal(discord.ui.Modal):
    def __init__(self, gid, uid):
        super().__init__(
            title="公司｜提出員工合約",
            timeout=300,
        )
        self.gid = str(gid)
        self.uid = str(uid)

        self.company = discord.ui.TextInput(
            label="公司編號",
            max_length=100,
        )
        self.character = discord.ui.TextInput(
            label="員工角色編號",
            max_length=20,
        )
        self.salary = discord.ui.TextInput(
            label="每期薪資（金幣）",
            max_length=20,
        )
        self.cycle = discord.ui.TextInput(
            label="發薪週期（7或14天）",
            max_length=2,
        )
        self.penalty = discord.ui.TextInput(
            label="提前解約違約金（金幣）",
            default="0",
            max_length=20,
        )

        for field in (
            self.company,
            self.character,
            self.salary,
            self.cycle,
            self.penalty,
        ):
            self.add_item(field)

    async def on_submit(self, interaction):
        if (
            str(interaction.guild_id) != self.gid
            or str(interaction.user.id) != self.uid
        ):
            return await reply(
                interaction,
                "這不是你的合約表單。",
            )

        try:
            company = self.company.value.strip()
            cid = int(self.character.value)
            salary = int(self.salary.value)
            cycle = int(self.cycle.value)
            penalty = int(self.penalty.value)

            if cid <= 0:
                raise ValueError("角色編號必須大於零")
            if salary < 0 or penalty < 0:
                raise ValueError("薪資和違約金不能是負數")
            if cycle not in (7, 14):
                raise ValueError("只能選7天或14天")

        except ValueError as exc:
            return await reply(
                interaction,
                f"⚠️ {exc}",
            )

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            employee_id = await asyncio.to_thread(
                validate_contract_offer_target,
                self.gid,
                company,
                cid,
                self.uid,
            )

            contract_id = await asyncio.to_thread(
                offer_contract,
                self.gid,
                company,
                cid,
                self.uid,
                salary,
                cycle,
                penalty,
            )

            message = (
                f"✅ 已提出合約 #{contract_id}\n"
                f"公司：{company}\n"
                f"角色：#{cid}\n"
                f"薪資：{salary:,} 金幣／{cycle}天\n"
                f"違約金：{penalty:,} 金幣\n"
                f"員工：<@{employee_id}>\n\n"
                "目前尚未生效，需由員工本人"
                "在 /公司 → 員工合約 中確認。"
            )

        except (
            ValueError,
            PermissionError,
            LookupError,
        ) as exc:
            message = f"⚠️ {exc}"
        except Exception:
            LOG.exception("提出員工合約失敗")
            message = "⚠️ 合約提交失敗，請檢查紀錄。"

        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class ContractDecisionModal(discord.ui.Modal):
    def __init__(self, gid, uid, action):
        super().__init__(
            title=(
                "員工｜接受合約"
                if action == "accept"
                else "員工｜拒絕合約"
            ),
            timeout=300,
        )
        self.gid = str(gid)
        self.uid = str(uid)
        self.action = action

        self.contract = discord.ui.TextInput(
            label="合約編號",
            max_length=20,
        )
        self.add_item(self.contract)

    async def on_submit(self, interaction):
        if (
            str(interaction.guild_id) != self.gid
            or str(interaction.user.id) != self.uid
        ):
            return await reply(
                interaction,
                "這不是你的合約。",
            )

        try:
            contract_id = int(self.contract.value)
            if contract_id <= 0:
                raise ValueError("合約編號不正確")
        except ValueError as exc:
            return await reply(
                interaction,
                f"⚠️ {exc}",
            )

        await interaction.response.defer(
            thinking=True,
            ephemeral=True,
        )

        try:
            action = (
                accept_contract
                if self.action == "accept"
                else reject_contract
            )

            result = await asyncio.to_thread(
                action,
                self.gid,
                contract_id,
                self.uid,
            )

        except (
            ValueError,
            PermissionError,
            LookupError,
            RuntimeError,
        ) as exc:
            result = f"⚠️ {exc}"
        except Exception:
            LOG.exception("合約確認失敗")
            result = "⚠️ 合約狀態不明，請核對紀錄。"

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


class CompanyContractView(discord.ui.View):
    def __init__(self, gid, uid, is_owner=False):
        super().__init__(timeout=300)

        self.gid = str(gid)
        self.uid = str(uid)

        if is_owner:
            self.add_item(
                ContractOfferButton()
            )

        self.add_item(ContractListButton())
        self.add_item(
            ContractDecisionButton(
                "accept", "✅ 接受合約",
                discord.ButtonStyle.success,
            )
        )
        self.add_item(
            ContractDecisionButton(
                "reject", "❌ 拒絕合約",
                discord.ButtonStyle.danger,
            )
        )

    async def interaction_check(self, interaction):
        allowed = (
            str(interaction.guild_id) == self.gid
            and str(interaction.user.id) == self.uid
        )
        if not allowed:
            await reply(
                interaction,
                "這不是你的合約管理面板。",
            )
        return allowed


class ContractOfferButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="📝 提出合約",
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction):
        view = self.view
        await interaction.response.send_modal(
            ContractOfferModal(
                view.gid,
                view.uid,
            )
        )


class ContractListButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="📋 查看合約",
            style=discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction):
        view = self.view

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            records = await asyncio.to_thread(
                contract_list,
                view.gid,
                view.uid,
            )

            lines = ["📄 **員工合約紀錄**"]

            for item in records:
                status = {
                    "pending": "待員工確認",
                    "active": "已生效",
                }.get(item["status"], item["status"])

                lines.append(
                    f"\n**#{item['id']}**｜{status}\n"
                    f"公司：{item['company_id']}｜"
                    f"角色 #{item['character_id']}\n"
                    f"薪資：{int(item['salary']):,} 金幣"
                    f"／{item['cycle_days']}天\n"
                    f"違約金：{int(item['penalty']):,} 金幣"
                )

            if not records:
                lines.append("目前沒有相關合約。")

            message = "\n".join(lines)

        except Exception:
            LOG.exception("公司合約清單查詢失敗")
            message = "⚠️ 無法讀取合約清單。"

        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )


class ContractDecisionButton(discord.ui.Button):
    def __init__(self, action, label, style):
        super().__init__(label=label, style=style)
        self.action = action

    async def callback(self, interaction):
        view = self.view
        await interaction.response.send_modal(
            ContractDecisionModal(
                view.gid,
                view.uid,
                self.action,
            )
        )


class CompanyContractEntry(discord.ui.Button):
    """嵌入現有 /公司 面板的入口。"""

    def __init__(self):
        super().__init__(
            label="📄 員工合約",
            style=discord.ButtonStyle.secondary,
            row=2,
        )

    async def callback(self, interaction):
        panel = self.view

        if (
            str(interaction.user.id) != panel.uid
            or str(interaction.guild_id) != panel.gid
        ):
            return await reply(
                interaction,
                "這不是你的公司面板。",
            )

        await interaction.response.send_message(
            "📄 **公司員工合約中心**\n"
            "公司持有人可提出合約；"
            "員工本人可查看、接受或拒絕。",
            view=CompanyContractView(
                panel.gid,
                panel.uid,
                panel.is_owner,
            ),
            ephemeral=True,
        )
