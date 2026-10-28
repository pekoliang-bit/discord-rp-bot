"""活動任務待審查詢與互動審核。獨立 Extension，不取代 event_tasks。"""
from __future__ import annotations

import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction
from modules.event_project_manager import (
    init_db as init_events, fetch_event, is_event_admin,
    is_owner, audit, respond,
)
from modules.event_tasks import init_db as init_tasks

LOG = logging.getLogger(__name__)
PAGE_SIZE = 10


def clean(value: object, maximum: int = 1000) -> str:
    value = discord.utils.escape_mentions(str(value or ""))
    value = discord.utils.escape_markdown(value)
    return value[:maximum]


def eligible(interaction: discord.Interaction, event: dict) -> bool:
    if not is_event_admin(interaction):
        return False
    if is_owner(interaction):
        return True
    uid = str(interaction.user.id)
    return uid not in {
        str(event["creator_id"]), str(event["organizer_id"]),
    }


def pending_rows(guild_id: int, event_id: int, offset: int = 0):
    with get_connection() as db:
        rows = db.execute("""
            SELECT p.task_id, p.user_id, p.evidence, p.submitted_at,
                   t.step, t.name, t.requirement, t.required
            FROM rp_event_task_progress AS p
            JOIN rp_event_tasks AS t
              ON t.id=p.task_id
             AND t.guild_id=p.guild_id
             AND t.event_id=p.event_id
            WHERE p.guild_id=? AND p.event_id=? AND p.status='submitted'
            ORDER BY p.submitted_at ASC, p.task_id ASC, p.user_id ASC
            LIMIT ? OFFSET ?
        """, (str(guild_id), event_id, PAGE_SIZE, offset)).fetchall()
        total = db.execute("""
            SELECT COUNT(*) AS n
            FROM rp_event_task_progress AS p
            JOIN rp_event_tasks AS t
              ON t.id=p.task_id
             AND t.guild_id=p.guild_id AND t.event_id=p.event_id
            WHERE p.guild_id=? AND p.event_id=? AND p.status='submitted'
        """, (str(guild_id), event_id)).fetchone()["n"]
    return [dict(r) for r in rows], total


def get_submission(guild_id: int, event_id: int, task_id: int, user_id: str):
    with get_connection() as db:
        row = db.execute("""
            SELECT p.*, t.step, t.name, t.requirement, t.required
            FROM rp_event_task_progress p
            JOIN rp_event_tasks t ON t.id=p.task_id
               AND t.guild_id=p.guild_id AND t.event_id=p.event_id
            WHERE p.guild_id=? AND p.event_id=? AND p.task_id=? AND p.user_id=?
        """, (str(guild_id), event_id, task_id, str(user_id))).fetchone()
    if row is None:
        raise ValueError("找不到這筆任務證明。")
    return dict(row)


def decide(interaction: discord.Interaction, event_id: int, task_id: int,
           user_id: str, passed: bool) -> None:
    """重新檢查權限、狀態並以條件式 UPDATE 防止雙重審核。"""
    with transaction() as db:
        event = dict(fetch_event(db, interaction.guild_id, event_id))
        if not eligible(interaction, event):
            raise PermissionError("你沒有審核此活動的權限。")
        if event["status"] not in ("running", "closed"):
            raise ValueError("活動狀態不允許審核任務。")
        if not is_owner(interaction) and str(interaction.user.id) == str(user_id):
            raise PermissionError("不能審核自己的任務。")
        row = db.execute("""
            SELECT p.status, t.step FROM rp_event_task_progress p
            JOIN rp_event_tasks t ON t.id=p.task_id
              AND t.guild_id=p.guild_id AND t.event_id=p.event_id
            WHERE p.guild_id=? AND p.event_id=? AND p.task_id=? AND p.user_id=?
        """, (str(interaction.guild_id), event_id, task_id, str(user_id))).fetchone()
        if row is None or row["status"] != "submitted":
            raise ValueError("這筆證明已由其他管理員處理，或不屬於本活動。")
        cur = db.execute("""
            UPDATE rp_event_task_progress
            SET status=?, reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND event_id=? AND task_id=? AND user_id=?
              AND status='submitted'
        """, (
            "approved" if passed else "rejected",
            str(interaction.user.id), str(interaction.guild_id), event_id,
            task_id, str(user_id),
        ))
        if cur.rowcount != 1:
            raise ValueError("此紀錄已被其他管理員審核。")
        audit(db, interaction.guild_id, event_id, interaction.user.id,
              "task_approved" if passed else "task_rejected",
              f"task={task_id},step={row['step']},user={user_id}")


class SubmissionSelect(discord.ui.Select):
    def __init__(self, rows: list[dict]):
        opts = [discord.SelectOption(
            label=f"階段 {r['step']}｜{str(r['name'])[:55]}",
            description=f"提交者：{r['user_id']}"[:100],
            value=f"{r['task_id']}:{r['user_id']}",
        ) for r in rows]
        super().__init__(placeholder="選擇一筆待審證明", options=opts, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: ReviewPanel = self.view
        task, user = self.values[0].split(":", 1)
        view.selected = (int(task), user)
        await view.render(interaction)


class ReviewPanel(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, event_id: int, page: int = 0):
        super().__init__(timeout=600)
        self.guild_id = interaction.guild_id
        self.reviewer_id = interaction.user.id
        self.event_id = event_id
        self.page = page
        self.selected: tuple[int, str] | None = None
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (interaction.guild_id != self.guild_id
                or interaction.user.id != self.reviewer_id):
            await respond(interaction, "這不是你的審核面板。")
            return False
        with get_connection() as db:
            event = dict(fetch_event(db, self.guild_id, self.event_id))
        if not eligible(interaction, event):
            await respond(interaction, "你目前沒有這個活動的審核資格。")
            return False
        return True

    def rebuild(self) -> None:
        for item in list(self.children):
            if isinstance(item, SubmissionSelect):
                self.remove_item(item)
        rows, total = pending_rows(self.guild_id, self.event_id,
                                   self.page * PAGE_SIZE)
        if not rows and self.page > 0:
            self.page -= 1
            rows, total = pending_rows(self.guild_id, self.event_id,
                                       self.page * PAGE_SIZE)
        self.rows, self.total = rows, total
        if rows:
            self.add_item(SubmissionSelect(rows))
        self.previous.disabled = self.page == 0
        self.next.disabled = (self.page + 1) * PAGE_SIZE >= total
        self.approve.disabled = self.reject.disabled = self.selected is None

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎪 活動 #{self.event_id}｜待審任務",
            description=(f"共 {self.total} 筆待審｜第 {self.page + 1} 頁\n"
                         "點選下拉選單查看完成證明。"),
            color=discord.Color.blurple(),
        )
        if self.selected:
            try:
                r = get_submission(self.guild_id, self.event_id, *self.selected)
                if r["status"] == "submitted":
                    embed.add_field(name="任務", value=f"第 {r['step']} 階段｜{clean(r['name'], 180)}", inline=False)
                    embed.add_field(name="提交者", value=f"<@{r['user_id']}>", inline=False)
                    embed.add_field(name="完成條件", value=clean(r["requirement"], 1024) or "無", inline=False)
                    embed.add_field(name="完成證明", value=clean(r["evidence"], 1024) or "無", inline=False)
                    embed.set_footer(text="請核對證明後再按核准或駁回")
            except ValueError:
                self.selected = None
        else:
            for r in self.rows[:8]:
                embed.add_field(
                    name=f"第 {r['step']} 階段｜{clean(r['name'], 80)}",
                    value=f"提交者 <@{r['user_id']}>｜{clean(r['submitted_at'], 30)}",
                    inline=False,
                )
        if not self.rows:
            embed.description = "✅ 本活動沒有待審任務證明。"
        return embed

    async def render(self, interaction: discord.Interaction):
        self.rebuild()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.selected = None
        await self.render(interaction)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.selected = None
        await self.render(interaction)

    @discord.ui.button(label="✅ 核准", style=discord.ButtonStyle.success, row=1)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._decide(interaction, True)

    @discord.ui.button(label="❌ 駁回", style=discord.ButtonStyle.danger, row=1)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._decide(interaction, False)

    @discord.ui.button(label="🔄 重新整理", style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected = None
        await self.render(interaction)

    async def _decide(self, interaction: discord.Interaction, passed: bool):
        if self.selected is None:
            return await respond(interaction, "請先選擇一筆待審證明。")
        try:
            decide(interaction, self.event_id, *self.selected, passed)
        except (ValueError, PermissionError, sqlite3.Error) as error:
            return await respond(interaction, f"⚠️ {error}")
        self.selected = None
        await self.render(interaction)


class TaskReviewUI(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="活動任務待審", description="查看活動任務完成證明並使用按鈕核准或駁回")
    @app_commands.guild_only()
    async def pending(self, interaction: discord.Interaction, 活動編號: int):
        if not is_event_admin(interaction):
            return await respond(interaction, "僅群主及正式活動管理員可以審核。")
        try:
            with get_connection() as db:
                event = dict(fetch_event(db, interaction.guild_id, 活動編號))
            if not eligible(interaction, event):
                raise PermissionError("你不能審核自己主辦的活動。")
            view = ReviewPanel(interaction, 活動編號)
            await interaction.response.send_message(
                embed=view.make_embed(), view=view, ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (ValueError, PermissionError, sqlite3.Error) as error:
            await respond(interaction, f"⚠️ {error}")


async def setup(bot: commands.Bot):
    init_tasks()
    await bot.add_cog(TaskReviewUI(bot))
