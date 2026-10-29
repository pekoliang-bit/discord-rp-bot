"""公司／品牌／集團申請的官方審核介面。

僅提供 /審核 內的按鈕，不註冊額外 Slash 指令。
核准時呼叫正式公司核心，使用原本的資格、資金和交易檢查。
"""

from __future__ import annotations

import logging
import sqlite3

import discord

from core.database import get_connection, transaction
from modules.company_business import approve_application, initialize
from modules.official_center import has_staff_permission

LOG = logging.getLogger(__name__)
PAGE_SIZE = 20
KIND_NAMES = {"company": "公司", "brand": "品牌", "group": "集團"}


def is_reviewer(interaction):
    return bool(
        interaction.guild
        and (
            interaction.user.id == interaction.guild.owner_id
            or has_staff_permission(interaction, "reviewer")
        )
    )


def fetch_cases(guild_id, page):
    initialize()
    with get_connection() as db:
        return db.execute(
            "SELECT * FROM rp_business_applications "
            "WHERE guild_id=? AND status='pending' "
            "ORDER BY id ASC LIMIT ? OFFSET ?",
            (str(guild_id), PAGE_SIZE, max(0, page) * PAGE_SIZE),
        ).fetchall()


class CaseSelect(discord.ui.Select):
    def __init__(self, rows):
        options = [
            discord.SelectOption(
                label=f"#{r['id']} {KIND_NAMES.get(r['kind'], r['kind'])}｜{r['name']}"[:100],
                description=f"{r['category']}｜啟動資金 {r['required_capital']:,}"[:100],
                value=str(r["id"]),
            ) for r in rows
        ]
        super().__init__(
            placeholder="選擇公司／品牌／集團待審案件",
            options=options or [discord.SelectOption(label="沒有待審案件", value="none")],
            disabled=not options,
            row=0,
        )

    async def callback(self, interaction):
        self.view.selected = int(self.values[0])
        await self.view.render(interaction)


class BusinessReviewView(discord.ui.View):
    def __init__(self, interaction, page=0):
        super().__init__(timeout=600)
        self.guild_id = interaction.guild_id
        self.reviewer_id = interaction.user.id
        self.is_owner = bool(interaction.guild and interaction.user.id == interaction.guild.owner_id)
        self.page = page
        self.selected = None
        self.rows = []
        self.refresh_rows()

    async def interaction_check(self, interaction):
        if (interaction.guild_id != self.guild_id
                or interaction.user.id != self.reviewer_id
                or not is_reviewer(interaction)):
            await interaction.response.send_message("沒有權限操作這個審核面板。", ephemeral=True)
            return False
        return True

    def refresh_rows(self):
        self.rows = fetch_cases(self.guild_id, self.page)
        for child in list(self.children):
            if isinstance(child, CaseSelect):
                self.remove_item(child)
        self.add_item(CaseSelect(self.rows))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = len(self.rows) < PAGE_SIZE
        self.approve.disabled = self.reject.disabled = self.selected is None

    def content(self):
        if self.selected is not None:
            with get_connection() as db:
                case = db.execute(
                    "SELECT * FROM rp_business_applications "
                    "WHERE guild_id=? AND id=? AND status='pending'",
                    (str(self.guild_id), self.selected),
                ).fetchone()
            if case is not None:
                safe = discord.utils.escape_mentions
                return (
                    f"🏢 **{KIND_NAMES.get(case['kind'], case['kind'])}申請 #{case['id']}**\n"
                    f"申請人：<@{case['applicant_id']}>｜持有人角色：#{case['owner_character_id']}\n"
                    f"名稱：{safe(case['name'])}\n"
                    f"分類：{safe(case['category'])}｜產業：{safe(case['industry'])}\n"
                    f"地區：{safe(case['region'])}\n"
                    f"說明：{safe(case['description'])[:650]}\n"
                    f"母公司：{safe(str(case['parent_id'] or '無'))}\n"
                    f"品牌代表角色：{case['representative_character_id'] or '未指定'}\n"
                    f"出資來源：{safe(case['funding_source'])}\n"
                    f"啟動資金：**{case['required_capital']:,} 金幣**\n\n"
                    "核准時將由系統重新檢查資格與資金，扣款後正式建立。"
                )[:1900]
        return f"🏢 **公司／品牌／集團審核**｜第 {self.page + 1} 頁\n本頁待審 {len(self.rows)} 筆。"

    async def render(self, interaction):
        self.refresh_rows()
        await interaction.response.edit_message(
            content=self.content(), view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def decide(self, interaction, accepted):
        case_id = self.selected
        if case_id is None:
            return await interaction.response.send_message("請先選擇案件。", ephemeral=True)
        try:
            with get_connection() as db:
                case = db.execute(
                    "SELECT * FROM rp_business_applications "
                    "WHERE guild_id=? AND id=? AND status='pending'",
                    (str(self.guild_id), case_id),
                ).fetchone()
            if case is None:
                raise ValueError("案件已處理，請重新整理。")
            if not self.is_owner and str(case["applicant_id"]) == str(interaction.user.id):
                raise PermissionError("非群主審核員不得審核自己的申請。")

            if accepted:
                # 核心函式在同一筆交易內完成資金轉移、建立與狀態更新。
                created_id = approve_application(
                    self.guild_id, case_id, interaction.user.id, official_verified=True,
                )
                result = f"✅ 申請 #{case_id} 核准成功，已建立 {created_id}。"
            else:
                with transaction() as db:
                    cur = db.execute(
                        "UPDATE rp_business_applications "
                        "SET status='rejected',reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP "
                        "WHERE guild_id=? AND id=? AND status='pending'",
                        (str(interaction.user.id), str(self.guild_id), case_id),
                    )
                    if cur.rowcount != 1:
                        raise ValueError("案件已由其他審核員處理。")
                result = f"❌ 申請 #{case_id} 已駁回，未扣除啟動資金。"

            self.selected = None
            await self.render(interaction)
            await interaction.followup.send(result, ephemeral=True)
        except (ValueError, PermissionError, sqlite3.Error) as exc:
            LOG.warning("商業案件審核失敗：%s", exc)
            await interaction.response.send_message(f"⚠️ 審核未完成：{exc}", ephemeral=True)

    @discord.ui.button(label="✅ 核准並建立", style=discord.ButtonStyle.success, row=1)
    async def approve(self, interaction, button):
        await self.decide(interaction, True)

    @discord.ui.button(label="❌ 駁回", style=discord.ButtonStyle.danger, row=1)
    async def reject(self, interaction, button):
        await self.decide(interaction, False)

    @discord.ui.button(label="◀ 上一頁", style=discord.ButtonStyle.secondary, row=2)
    async def previous(self, interaction, button):
        self.page = max(0, self.page - 1)
        self.selected = None
        await self.render(interaction)

    @discord.ui.button(label="下一頁 ▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(self, interaction, button):
        self.page += 1
        self.selected = None
        await self.render(interaction)

    @discord.ui.button(label="🔄 重新整理", style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction, button):
        self.selected = None
        await self.render(interaction)


class OpenBusinessReview(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🏢 公司／品牌／集團審核", style=discord.ButtonStyle.primary, row=2)

    async def callback(self, interaction):
        if not is_reviewer(interaction):
            return await interaction.response.send_message("沒有商業申請審核權限。", ephemeral=True)
        view = BusinessReviewView(interaction)
        await interaction.response.send_message(
            view.content(), view=view, ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot):
    initialize()
