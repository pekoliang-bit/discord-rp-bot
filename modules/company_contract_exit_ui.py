
"""公司合約解約 Discord 介面。"""

from __future__ import annotations

import asyncio
import logging
import discord

from core.company_contract_exit_service import (
    request_exit,
    approve_mutual_exit,
    finalize_exit,
)

LOG = logging.getLogger(__name__)


class ExitModal(discord.ui.Modal):
    def __init__(self, gid, uid, action):
        titles = {
            "request": "提出合約解約",
            "approve": "公司同意協議解約",
            "finalize": "完成合約解約",
        }
        super().__init__(
            title=titles[action],
            timeout=300,
        )
        self.gid = str(gid)
        self.uid = str(uid)
        self.action = action

        self.number = discord.ui.TextInput(
            label=(
                "合約編號" if action == "request"
                else "解約案件編號"
            ),
            max_length=20,
        )
        self.add_item(self.number)

        if action == "request":
            self.reason = discord.ui.TextInput(
                label="原因：一般／協議／自建公司",
                placeholder="一般",
                max_length=10,
            )
            self.add_item(self.reason)

    async def on_submit(self, interaction):
        if (
            str(interaction.guild_id) != self.gid
            or str(interaction.user.id) != self.uid
        ):
            return await interaction.response.send_message(
                "這不是你的解約表單。",
                ephemeral=True,
            )

        try:
            number = int(self.number.value)
            if number <= 0:
                raise ValueError("編號必須大於零")

            reason = None
            if self.action == "request":
                reason = {
                    "一般": "ordinary",
                    "協議": "mutual",
                    "自建公司": "self_company",
                }.get(self.reason.value.strip())

                if reason is None:
                    raise ValueError(
                        "原因只能填一般、協議或自建公司"
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
            if self.action == "request":
                case_id = await asyncio.to_thread(
                    request_exit,
                    self.gid, number, self.uid, reason,
                )
                result = (
                    f"✅ 已建立解約案件 #{case_id}。\n"
                    "請確認解約條件後再完成解約。"
                )
            elif self.action == "approve":
                result = await asyncio.to_thread(
                    approve_mutual_exit,
                    self.gid, number, self.uid,
                )
            else:
                result = await asyncio.to_thread(
                    finalize_exit,
                    self.gid, number, self.uid,
                )
        except (
            ValueError,
            PermissionError,
            LookupError,
            RuntimeError,
        ) as exc:
            result = f"⚠️ {exc}"
        except Exception:
            LOG.exception("合約解約操作失敗")
            result = (
                "⚠️ 解約狀態無法確認，"
                "請管理員查核帳本後再操作。"
            )

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


class ContractExitView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=300)
        self.gid = str(gid)
        self.uid = str(uid)

    async def interaction_check(self, interaction):
        if (
            str(interaction.guild_id) != self.gid
            or str(interaction.user.id) != self.uid
        ):
            await interaction.response.send_message(
                "這不是你的合約管理面板。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="📝 提出解約",
        style=discord.ButtonStyle.primary,
    )
    async def request(self, interaction, button):
        await interaction.response.send_modal(
            ExitModal(self.gid, self.uid, "request")
        )

    @discord.ui.button(
        label="✅ 公司批准協議",
        style=discord.ButtonStyle.secondary,
    )
    async def approve(self, interaction, button):
        await interaction.response.send_modal(
            ExitModal(self.gid, self.uid, "approve")
        )

    @discord.ui.button(
        label="💰 確認完成解約",
        style=discord.ButtonStyle.danger,
    )
    async def finalize(self, interaction, button):
        await interaction.response.send_modal(
            ExitModal(self.gid, self.uid, "finalize")
        )
