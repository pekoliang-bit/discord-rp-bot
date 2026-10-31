"""/任務：恢復玩家任務入口（唯讀版，不修改資料庫）。

提供新手教學、活動階段任務查詢、通告開發狀態；
既有活動任務的提交/審核仍由原模組處理。
"""
from __future__ import annotations

import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection

LOG = logging.getLogger(__name__)

GUIDE = (
    "**① 建立角色**：使用 `/創角` 建立角色並依規定完成審核。\n"
    "**② 角色單連結**：在 `/創角` 查看登記入口，或在角色單訊息的應用程式選單尋找「登記角色單」。\n"
    "**③ 查閱角色**：使用 `/查看` 或 `/角色` 查詢角色與背包。\n"
    "**④ 經濟與組織**：使用 `/銀行`、`/商城`、`/公司`、`/陣營`。\n"
    "**⑤ 參與活動**：使用 `/總覽` 查看活動，依活動公告報名及提交證明。\n"
    "**⑥ 取得協助**：使用 `/總覽` 查看玩法及指令。\n\n"
    "*這是操作指引，不會發放或重複領取新手獎勵。*"
)


def safe_text(value: object, limit: int = 90) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value or "")))[:limit]


def read_active_tasks(guild_id: int, user_id: int) -> list[dict]:
    """只讀取已存在的活動任務資料；缺表時安全返回。"""
    try:
        with get_connection() as db:
            db.row_factory = sqlite3.Row
            result = db.execute(
                """SELECT e.id AS event_id, e.title AS event_title,
                          t.step, t.name AS task_name, t.requirement,
                          COALESCE(p.status, 'not_submitted') AS progress
                   FROM rp_event_tasks t
                   JOIN rp_event_projects e
                     ON e.id=t.event_id AND e.guild_id=t.guild_id
                   LEFT JOIN rp_event_task_progress p
                     ON p.guild_id=t.guild_id AND p.task_id=t.id
                    AND p.user_id=?
                   WHERE t.guild_id=? AND e.status IN ('open','running')
                   ORDER BY e.id DESC,t.step ASC LIMIT 30""",
                (str(user_id), str(guild_id)),
            ).fetchall()
            return [dict(r) for r in result]
    except (sqlite3.Error, KeyError, TypeError):
        LOG.info("任務查詢資料表尚未就緒，改顯示空清單", exc_info=True)
        return []


class TasksView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("請自行使用 `/任務` 開啟面板。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="新手指引", emoji="📖", style=discord.ButtonStyle.primary)
    async def guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(GUIDE, ephemeral=True)

    @discord.ui.button(label="活動任務", emoji="📋", style=discord.ButtonStyle.secondary)
    async def event_tasks(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = read_active_tasks(self.guild_id, interaction.user.id)
        if not rows:
            await interaction.response.send_message(
                "目前沒有可顯示的進行中活動階段任務；活動報名與進度可到 `/總覽` 查看。",
                ephemeral=True,
            )
            return
        status_names = {"approved": "已核准", "submitted": "待審核", "rejected": "未通過", "not_submitted": "未提交"}
        lines = [
            f"**{safe_text(r['event_title'], 35)}** #{r['event_id']}｜第{r['step']}階段 "
            f"{safe_text(r['task_name'], 40)}（{status_names.get(r['progress'], safe_text(r['progress'], 12))}）"
            for r in rows
        ]
        chunks = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 1800:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current:
            chunks.append(current)
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    @discord.ui.button(label="演藝通告", emoji="🎬", style=discord.ButtonStyle.secondary)
    async def notices(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "🎬 **演藝通告系統正在恢復中**。目前尚未接回正式通告資料及獎勵結算，"
            "因此不提供虛假的可接取通告。現有活動請先至 `/總覽` 查詢。",
            ephemeral=True,
        )


class TasksCenter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="任務", description="新手指引、活動任務及演藝通告入口")
    @app_commands.guild_only()
    async def tasks(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📋 任務中心",
            description="選擇下方功能查看教學與現有活動任務。演藝通告仍待恢復，不會誤發獎勵。",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(
            embed=embed, view=TasksView(interaction.user.id, interaction.guild_id), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TasksCenter(bot))
