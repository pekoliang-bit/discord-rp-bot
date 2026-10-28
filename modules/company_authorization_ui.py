
"""公司登記與銀行申請代理人管理。

只有群主能操作。
不建立獨立斜線指令。
"""

from __future__ import annotations

import logging
import sqlite3

import discord

from modules.application_extras import (
    init_db,
    register_approved_company,
    set_company_bank_proxy,
    say,
)

LOG = logging.getLogger(__name__)


def require_owner(i: discord.Interaction):
    if (
        i.guild is None
        or i.user.id != i.guild.owner_id
    ):
        raise PermissionError("只有群主可以操作公司授權。")


def validate_id(value: str, name: str):
    value = str(value).strip()

    if not value or len(value) > 100:
        raise ValueError(f"{name}格式不正確。")

    return value


class CompanyRegisterModal(
    discord.ui.Modal,
    title="登記已核准公司",
):
    company_id = discord.ui.TextInput(
        label="正式公司編號",
        max_length=100,
        required=True,
    )

    applicant_id = discord.ui.TextInput(
        label="原公司申請人 Discord ID",
        max_length=22,
        required=True,
    )

    async def on_submit(self, i):
        try:
            require_owner(i)

            company = validate_id(
                self.company_id.value,
                "公司編號",
            )

            applicant = str(
                self.applicant_id.value
            ).strip()

            if not applicant.isdigit():
                raise ValueError(
                    "請填寫原申請人的 Discord 數字 ID。"
                )

            # 必須由群主先核實：
            # 公司確實獲准成立，
            # Discord ID 確實屬於原申請人。
            register_approved_company(
                i.guild_id,
                company,
                applicant,
                actor_is_guild_owner=True,
            )

            await say(
                i,
                f"✅ 公司 {company} 已登記。\n"
                f"原申請人：<@{applicant}>\n"
                "此操作不會自動建立銀行帳號。",
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            LOG.warning(
                "公司登記失敗：%s",
                exc,
            )
            await say(i, f"⚠️ {exc}")


class ProxyModal(discord.ui.Modal):
    company_id = discord.ui.TextInput(
        label="正式公司編號",
        max_length=100,
        required=True,
    )

    proxy_id = discord.ui.TextInput(
        label="代理人 Discord ID",
        max_length=22,
        required=True,
    )

    def __init__(self, active: bool):
        super().__init__(
            title=(
                "設定公司銀行申請代理人"
                if active
                else "撤銷公司銀行申請代理人"
            ),
        )
        self.active = active

    async def on_submit(self, i):
        try:
            require_owner(i)

            company = validate_id(
                self.company_id.value,
                "公司編號",
            )

            proxy = str(
                self.proxy_id.value
            ).strip()

            if not proxy.isdigit():
                raise ValueError(
                    "請填寫代理人的 Discord 數字 ID。"
                )

            set_company_bank_proxy(
                i.guild_id,
                company,
                proxy,
                self.active,
                actor_is_guild_owner=True,
            )

            action = (
                "已授權"
                if self.active
                else "已撤銷授權"
            )

            await say(
                i,
                f"✅ 公司 {company}\n"
                f"代理人：<@{proxy}>\n"
                f"狀態：{action}\n\n"
                "這項代理權僅適用於"
                "公司銀行帳號申請，"
                "不包含提款或轉帳權限。",
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            LOG.warning(
                "公司代理操作失敗：%s",
                exc,
            )
            await say(i, f"⚠️ {exc}")


class CompanyAuthorizationView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)

        self.owner_id = i.user.id
        self.guild_id = i.guild_id

    async def interaction_check(self, i):
        if (
            i.guild_id != self.guild_id
            or i.user.id != self.owner_id
            or i.guild is None
            or i.user.id != i.guild.owner_id
        ):
            await say(
                i,
                "⛔ 只有群主本人能使用此管理面板。",
            )
            return False

        return True

    @discord.ui.button(
        label="🏢 登記公司",
        style=discord.ButtonStyle.primary,
    )
    async def register(self, i, button):
        await i.response.send_modal(
            CompanyRegisterModal()
        )

    @discord.ui.button(
        label="✅ 指定代理人",
        style=discord.ButtonStyle.success,
    )
    async def grant(self, i, button):
        await i.response.send_modal(
            ProxyModal(True)
        )

    @discord.ui.button(
        label="❌ 撤銷代理人",
        style=discord.ButtonStyle.danger,
    )
    async def revoke(self, i, button):
        await i.response.send_modal(
            ProxyModal(False)
        )


class OpenCompanyAuthorization(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🏢 公司授權",
            style=discord.ButtonStyle.secondary,
            row=4,
        )

    async def callback(self, i):
        try:
            require_owner(i)
        except PermissionError as exc:
            return await say(i, str(exc))

        await i.response.send_message(
            "🏢 **公司登記與授權管理**\n\n"
            "可登記已正式核准的公司、"
            "指定銀行申請代理人，"
            "或撤銷代理資格。\n\n"
            "請先核實原公司成立紀錄，"
            "再填入公司編號與 Discord ID。",
            view=CompanyAuthorizationView(i),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot):
    init_db()
    LOG.info("公司授權管理介面已載入。")
