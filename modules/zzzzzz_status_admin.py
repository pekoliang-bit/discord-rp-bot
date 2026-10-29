"""將地位診斷入口加入 /官方 管理功能；不增加新的斜線指令。"""
from __future__ import annotations

import asyncio
import logging

import discord

from core.database import get_connection
from modules import character_status as status
from modules import official_center

LOG = logging.getLogger(__name__)


def _owner(interaction: discord.Interaction) -> bool:
    return bool(interaction.guild and interaction.user.id == interaction.guild.owner_id)


def _diagnose(guild_id: int, character_id: int) -> str:
    status.initialize()
    with get_connection() as db:
        character = db.execute(
            "SELECT * FROM characters WHERE guild_id=? AND id=? AND status='active'",
            (str(guild_id), character_id),
        ).fetchone()
        if not character:
            return "找不到已啟用的角色，請確認角色編號。"
        attrs = status._attributes(db, guild_id, character_id)
        tracking = status._tracking(db, guild_id, character_id)
        skill_name, skill_value = status._main_skill(character, attrs)
    state = status.get_status(guild_id, character_id)
    level = max(1, min(20, int(state["level"])))
    current_score, current_pass = status.score_for(level, attrs, tracking, skill_value)
    lines = [
        f"🏅 **角色 #{character_id} 地位診斷**",
        f"目前：LV{level} {status.LEVELS[level-1][1]}",
        f"維持進度：{state['maintenance']:.0f}%｜警告：{'是' if state['warning_active'] else '否'}",
        f"目前評分：{current_score}/100｜門檻：{'符合' if current_pass else '未符合'}",
        f"熱度：{attrs.get('流量熱度', 0):,}｜商業價值：{attrs.get('商業價值', 0):,}｜影響指數：{attrs.get('影響指數', 0):,}",
        f"主攻：{skill_name or '未設定'}（{skill_value:,}）",
        f"一般工作薪資倍率：×{status.WORK_MULTIPLIERS[level-1]:g}",
        f"最高商業代言資格：T{status.ENDORSEMENT_MAX_TIER[level-1]}",
    ]
    if level < 20:
        score, eligible = status.score_for(level + 1, attrs, tracking, skill_value)
        _, name, heat, commercial, influence = status.LEVELS[level]
        lines.extend((
            f"\n**下一級 LV{level+1} {name}**：{score}/100，{'符合' if eligible else '未符合'}三項核心門檻",
            f"需求：熱度 {heat:,}／商業 {commercial:,}／影響 {influence:,}；綜合分數 ≥80",
        ))
    return "\n".join(lines)[:1900]


class StatusInspectModal(discord.ui.Modal, title="角色地位評分檢查"):
    character_id = discord.ui.TextInput(label="角色編號", placeholder="例如 12", max_length=12)

    def __init__(self, *, settle: bool):
        super().__init__()
        self.settle = settle

    async def on_submit(self, interaction: discord.Interaction):
        if not _owner(interaction):
            await interaction.response.send_message("只有群主可以使用地位診斷管理。", ephemeral=True)
            return
        raw = str(self.character_id.value).strip()
        if not raw.isdecimal() or int(raw) <= 0:
            await interaction.response.send_message("請填寫正確的角色編號。", ephemeral=True)
            return
        cid = int(raw)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            messages = (await asyncio.to_thread(status.review_character, interaction.guild_id, cid)) if self.settle else []
            if messages:
                with get_connection() as db:
                    table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='character_forum_links'").fetchone()
                    link = (db.execute("SELECT notice_id FROM character_forum_links WHERE guild_id=? AND character_id=?",
                                       (str(interaction.guild_id), cid)).fetchone() if table else None)
                if link and link["notice_id"]:
                    try:
                        thread = interaction.guild.get_thread(int(link["notice_id"]))
                        if thread is None:
                            thread = await interaction.client.fetch_channel(int(link["notice_id"]))
                        for message in messages:
                            await thread.send(message, allowed_mentions=discord.AllowedMentions.none())
                    except (discord.HTTPException, AttributeError):
                        LOG.warning("角色 %s 的地位通知未能送達，事件已記錄", cid)
            report = await asyncio.to_thread(_diagnose, interaction.guild_id, cid)
            if messages:
                report += "\n\n**本次結算事件**\n" + "\n".join(messages)
            await interaction.followup.send(report[:2000], ephemeral=True)
        except Exception:
            LOG.exception("地位管理檢查失敗：%s", cid)
            await interaction.followup.send("地位資料讀取失敗，請查看 Railway 錯誤紀錄。", ephemeral=True)


class StatusAdminView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="📊 檢查角色地位", style=discord.ButtonStyle.primary)
    async def inspect(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _owner(interaction):
            await interaction.response.send_message("只有群主可以操作。", ephemeral=True)
            return
        await interaction.response.send_modal(StatusInspectModal(settle=False))

    @discord.ui.button(label="🔄 手動重新結算", style=discord.ButtonStyle.secondary)
    async def settle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _owner(interaction):
            await interaction.response.send_message("只有群主可以操作。", ephemeral=True)
            return
        await interaction.response.send_modal(StatusInspectModal(settle=True))


class StatusAdminButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🏅 地位管理", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        if not _owner(interaction):
            await interaction.response.send_message("只有群主可以操作。", ephemeral=True)
            return
        await interaction.response.send_message(
            "🏅 **地位管理**\n可檢查角色目前分數、下一級門檻，或執行一次即時結算。",
            view=StatusAdminView(), ephemeral=True,
        )


def _install():
    target = official_center.AdminToolsView
    if getattr(target, "_rp_status_admin_patched", False):
        return
    original = target.__init__

    def wrapped(self, interaction, *args, **kwargs):
        original(self, interaction, *args, **kwargs)
        if _owner(interaction) and len(self.children) < 25:
            self.add_item(StatusAdminButton())

    target.__init__ = wrapped
    target._rp_status_admin_patched = True


async def setup(bot):
    _install()
    LOG.info("官方地位診斷按鈕已整合進 /官方 管理中心。")
