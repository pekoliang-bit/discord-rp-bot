
"""Discord /公司 → 集團管理。

不新增頂層斜線指令。
"""

from __future__ import annotations

import asyncio
import logging

import discord

from core.database import transaction
from modules.company_select_ui import open_company_chooser
from core.company_group_overview import (
    group_overview,
    format_group_overview,
)
from core.company_group_membership import (
    attach_company,
)

LOG = logging.getLogger(__name__)


class GroupAttachModal(discord.ui.Modal):
    def __init__(self, gid, uid, selected_company=None):
        super().__init__(
            title="集團｜納入旗下公司",
            timeout=300,
        )

        self.gid = str(gid)
        self.uid = str(uid)
        self.selected_company = selected_company

        self.company_id = discord.ui.TextInput(
            label="正式公司編號",
            max_length=100,
        )

        if self.selected_company is None:
            self.add_item(self.company_id)
        else:
            self.confirm = discord.ui.TextInput(label='輸入「確認」以納入集團', max_length=2)
            self.add_item(self.confirm)

    async def on_submit(self, interaction):
        if (
            str(interaction.guild_id) != self.gid
            or str(interaction.user.id) != self.uid
        ):
            return await interaction.response.send_message(
                "這不是你的操作表單。",
                ephemeral=True,
            )

        if self.selected_company is not None and self.confirm.value.strip() != '確認':
            return await interaction.response.send_message('尚未確認，沒有變更集團公司名單。', ephemeral=True)

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            result = await asyncio.to_thread(
                group_overview,
                self.gid,
                self.uid,
            )

            gid = result["group_id"]

            if gid is None:
                raise ValueError(
                    "你目前沒有正式集團。"
                )

            company_id = self.selected_company or self.company_id.value.strip()

            def do_attach():
                with transaction() as db:
                    changed = attach_company(
                        db,
                        self.gid,
                        gid,
                        company_id,
                        self.uid,
                    )

                    if changed:
                        db.execute(
                            """
                            INSERT INTO rp_business_audit (
                                guild_id,
                                business_id,
                                action,
                                actor_id,
                                details
                            )
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                self.gid,
                                gid,
                                "group_attach_company",
                                self.uid,
                                company_id,
                            ),
                        )

                    return changed

            changed = await asyncio.to_thread(
                do_attach
            )

            message = (
                "✅ 公司已納入集團。"
                if changed
                else "該公司已經隸屬此集團。"
            )

        except (
            ValueError,
            PermissionError,
            LookupError,
            RuntimeError,
        ) as exc:
            message = f"⚠️ {exc}"

        except Exception:
            LOG.exception(
                "集團公司歸屬失敗"
            )
            message = (
                "⚠️ 操作無法完成，"
                "請檢查集團紀錄。"
            )

        await interaction.followup.send(
            message,
            ephemeral=True,
        )


class CompanyGroupView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=300)

        self.gid = str(gid)
        self.uid = str(uid)

        from modules.company_group_delegation_ui import (
            DelegationEntry,
            DelegatedOverviewEntry,
            DelegatedCompaniesEntry,
            DelegatedMemberReportEntry,
        )

        self.add_item(DelegationEntry())
        self.add_item(DelegatedOverviewEntry())
        self.add_item(DelegatedCompaniesEntry())
        self.add_item(DelegatedMemberReportEntry())

    async def interaction_check(self, interaction):
        allowed = (
            str(interaction.guild_id) == self.gid
            and str(interaction.user.id) == self.uid
        )

        if not allowed:
            await interaction.response.send_message(
                "這不是你的集團管理面板。",
                ephemeral=True,
            )

        return allowed

    @discord.ui.button(
        label="🏢 查看集團",
        style=discord.ButtonStyle.primary,
    )
    async def overview(self, interaction, button):
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            data = await asyncio.to_thread(
                group_overview,
                self.gid,
                self.uid,
            )

            message = format_group_overview(data)

        except Exception:
            LOG.exception("集團查詢失敗")
            message = "⚠️ 無法讀取集團資料。"

        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )

    @discord.ui.button(
        label="➕ 納入公司",
        style=discord.ButtonStyle.secondary,
    )
    async def attach(self, interaction, button):
        await open_company_chooser(interaction, self.uid, GroupAttachModal, kinds=('company',))


class CompanyGroupEntry(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🏢 集團管理",
            style=discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction):
        panel = self.view

        if (
            str(interaction.user.id) != panel.uid
            or str(interaction.guild_id) != panel.gid
        ):
            return await interaction.response.send_message(
                "這不是你的公司管理面板。",
                ephemeral=True,
            )

        await interaction.response.send_message(
            "🏢 **集團管理中心**\n"
            "查看自己的集團與成立資格，"
            "或管理旗下正式公司。",
            view=CompanyGroupView(
                panel.gid,
                panel.uid,
            ),
            ephemeral=True,
        )
