"""系統總覽 V3：公開資料查詢、玩法、指令、獨立群主指引。"""
from __future__ import annotations

import logging
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from core.database import get_connection
from modules.overview_catalog import PAGE_SIZE, MENU
from modules.overview_guides import GUIDES, OWNER_GUIDE
from modules.overview_helpers import escape
from modules.overview_data import query_rows
from modules.overview_status import system_lines, status_text
from modules.overview_updates import recent_updates
from modules.leaderboard_ui import LeaderboardView

LOG = logging.getLogger(__name__)






class OverviewSelect(discord.ui.Select):
    def __init__(self, selected):
        super().__init__(
            placeholder="選擇總覽分類",
            options=[discord.SelectOption(label=v, value=k, default=(k == selected))
                     for k, v in MENU.items()], row=0,
        )

    async def callback(self, interaction):
        view = self.view
        view.section = self.values[0]
        view.page = 0
        view.detail_id = None
        await view.update(interaction)


class GuideSelect(discord.ui.Select):
    def __init__(self, current):
        super().__init__(
            placeholder="選擇玩法主題",
            options=[discord.SelectOption(label=k, value=k, default=(k == current))
                     for k in GUIDES], row=1,
        )

    async def callback(self, interaction):
        view = self.view
        view.guide = self.values[0]
        await view.update(interaction)


class DetailSelect(discord.ui.Select):
    def __init__(self, rows, section, chosen):
        opts = []
        for row in rows:
            rid = int(row["id"])
            name = row.get("title") or row.get("name") or str(rid)
            opts.append(discord.SelectOption(
                label=f"#{rid}｜{str(name)[:85]}", value=str(rid),
                default=(rid == chosen)))
        super().__init__(placeholder="選擇項目查看詳情", options=opts, row=1)

    async def callback(self, interaction):
        view = self.view
        view.detail_id = int(self.values[0])
        await view.update(interaction)


class OverviewView(discord.ui.View):
    def __init__(self, bot, user_id, guild_id):
        super().__init__(timeout=600)
        self.bot = bot
        self.user_id = user_id
        self.guild_id = guild_id
        self.section = "commands"
        self.guide = next(iter(GUIDES))
        self.page = 0
        self.detail_id = None
        self.rebuild()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id or interaction.guild_id != self.guild_id:
            await interaction.response.send_message("請自行使用 `/總覽` 開啟查詢。", ephemeral=True)
            return False
        return True

    def rebuild(self):
        self.clear_items()
        self.add_item(OverviewSelect(self.section))
        if self.section in ("characters", "awards", "extra_likes") or self.section.startswith("event_"):
            try:
                rows = query_rows(self.guild_id, self.section)
                visible = rows[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]
                if visible:
                    self.add_item(DetailSelect(visible, self.section, self.detail_id))
            except Exception:
                LOG.exception("建立總覽詳情選單失敗")
        if self.section == "guide":
            self.add_item(GuideSelect(self.guide))
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.section in ("guide", "commands", "systems", "status", "updates")
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.home_button)

    def render(self):
        title = MENU[self.section]
        embed = discord.Embed(title=f"🌐 流光城｜{title}", color=discord.Color.blurple())
        if self.section == "systems":
            embed.description = "以下是目前指令樹的系統導覽；未註冊的入口以 ▫️ 顯示。"
            for line in system_lines(self.bot):
                embed.add_field(name="分類", value=line[:1024], inline=False)
            return embed
        if self.section == "status":
            embed.description = status_text(self.bot)
            return embed
        if self.section == "updates":
            embed.description = recent_updates(self.guild_id)
            return embed
        if self.section == "guide":
            embed.add_field(name=self.guide, value=GUIDES[self.guide], inline=False)
            embed.set_footer(text="新手教學與任務獎勵請使用 /任務")
            return embed
        if self.section == "commands":
            # 不依賴 discord.AppCommandType：部分部署的 discord.py 版本可能沒有該名稱。
            commands_list = sorted(self.bot.tree.get_commands(), key=lambda c: c.name)
            visible = [c for c in commands_list if isinstance(c, (app_commands.Command, app_commands.Group))]
            embed.description = "目前機器人已載入的頂層斜線指令；功能實作與 Discord 同步狀態可能不同。"
            lines = [f"`/{escape(c.name,50)}`｜{escape(getattr(c, 'description', '') or '指令群組', 78)}" for c in visible]
            # 每欄控制在 Discord 1024 字元之內，避免指令過多時整個總覽開啟失敗。
            if not lines:
                embed.description += "\n\n目前沒有找到已載入指令。"
            for start in range(0, min(len(lines), 40), 5):
                value = "\n".join(lines[start:start+5])
                embed.add_field(name=f"指令 {start+1}–{min(start+5,len(lines))}",
                                value=value[:1024] or "無", inline=False)
                if len(embed.fields) >= 10:
                    break
            embed.set_footer(text=f"目前載入 {len(visible)} 個頂層指令")
            return embed
        try:
            rows = query_rows(self.guild_id, self.section)
        except Exception as exc:
            LOG.exception("總覽讀取失敗：%s", self.section)
            embed.description = "⚠️ 目前無法讀取資料，請通知管理員檢查資料庫。"
            return embed
        page_count = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(self.page, page_count-1)
        chunk = rows[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]
        if not chunk:
            embed.description = "📭 目前此分類沒有公開紀錄。"
        for r in chunk:
            if self.section == "awards":
                kind = {"achievement":"成就", "honor":"榮譽", "title":"稱號"}.get(r['kind'], r['kind'])
                embed.add_field(name=f"#{r['id']}｜{escape(r['name'],80)}",
                                value=f"{kind}｜{escape(r['description'],150)}", inline=False)
            elif self.section.startswith("event_"):
                ch = f"<# {r['channel_id']}>".replace("# ", "#") if r.get("channel_id") and str(r['channel_id']).isdigit() else "未設定"
                value = f"{escape(r['description'], 160)}\n上限：{r['capacity']} 人｜頻道：{ch}"
                embed.add_field(name=f"#{r['id']}｜{escape(r['title'], 90)}", value=value[:1024], inline=False)
            elif self.section == "extra_likes":
                rank = self.page * PAGE_SIZE + len(embed.fields) + 1
                embed.add_field(name=f"#{rank}｜{escape(r['name'],80)}",
                                value=f"💗 {r['hearts']} 人喜歡｜角色 #{r['id']}",inline=False)
            elif self.section == "tags":
                embed.add_field(name=f"#{r['id']}｜{escape(r['name'],85)}",
                                value=f"分類：{escape(r['category'],60)}", inline=False)
            else:
                code = escape(r.get('public_code') or r['id'], 30)
                kind = {'player':'玩家角色','player_npc':'玩家 NPC','official_npc':'官方 NPC'}.get(r['character_type'], '角色')
                embed.add_field(name=f"{code}｜{escape(r['name'],90)}",
                                value=f"{kind}｜{escape(r['occupation'],60)}｜{'已退場' if r['status']=='retired' else '活躍'}", inline=False)
        if self.detail_id is not None:
            selected = next((r for r in chunk if int(r["id"]) == self.detail_id), None)
            if selected:
                
                try:
                    detail = self.render_detail(selected)
                except Exception:
                    LOG.exception("總覽詳情讀取失敗：section=%s", self.section)
                    detail = "⚠️ 詳情暫時無法讀取，請稍後重試。"
                if detail:
                    embed.insert_field_at(0, name="🔎 選取項目詳情", value=detail[:1024], inline=False)
            else:
                self.detail_id = None
        embed.set_footer(text=f"共 {len(rows)} 筆｜第 {self.page+1}/{page_count} 頁｜選單切換分類")
        return embed

    def render_detail(self, r):
        if self.section == "awards":
            # JSON 規則直接展示摘要，不把內部規則 JSON 當成可任意領獎的操作。
            return (f"名稱：{escape(r['name'],100)}\n"
                    f"說明：{escape(r['description'],280)}\n"
                    f"取得條件：{escape(r['condition_json'],280)}\n"
                    f"獎勵設定：{escape(r['rewards_json'],280)}")
        if self.section.startswith("event_"):
            with get_connection() as db:
                event = db.execute(
                    """SELECT eligibility,schedule_text,rewards_text,title_reward_text,
                              channel_id FROM rp_event_projects
                       WHERE guild_id=? AND id=? AND status=?""",
                    (str(self.guild_id), int(r['id']), self.section.split('_',1)[1])
                ).fetchone()
                if event is None:
                    return "活動資料已更新，請重新整理。"
                counts = {x['status']: x['n'] for x in db.execute(
                    """SELECT status,COUNT(*) n FROM rp_event_enrollments
                       WHERE guild_id=? AND event_id=? GROUP BY status""",
                    (str(self.guild_id), int(r['id'])))}
            return (f"資格：{escape(event['eligibility'],180)}\n"
                    f"時間／流程：{escape(event['schedule_text'],180)}\n"
                    f"活動獎勵：{escape(event['rewards_text'],160)}\n"
                    f"稱號獎勵：{escape(event['title_reward_text'],120)}\n"
                    f"已通過：{counts.get('accepted',0)}｜審核中：{counts.get('pending',0)}")
        if self.section == "extra_likes":
            return (f"路人：{escape(r['name'],100)}\n"
                    f"編號：#{r['id']}\n"
                    f"目前人氣：💗 {r['hearts']}\n"
                    "前往 `/查看` → 路人角色，可按愛心或取消。")
        if self.section == "characters":
            return (f"角色編號：{escape(r.get('public_code') or r['id'],50)}\n"
                    f"角色名稱：{escape(r['name'],100)}\n"
                    f"類別：{escape(r['character_type'],70)}\n"
                    f"職業：{escape(r['occupation'],100)}\n"
                    f"公開狀態：{escape(r['status'],40)}\n"
                    "詳細角色面板請使用 `/查看`。")
        return ""

    async def update(self, interaction):
        try:
            self.rebuild()
            embed = self.render()
            # render 會修正頁碼，因此需重新建構按鈕狀態。
            self.rebuild()
            if self.section not in ('guide','commands','systems','status','updates'):
                try:
                    count = len(query_rows(self.guild_id, self.section))
                    self.next_button.disabled = (self.page+1)*PAGE_SIZE >= count
                except Exception:
                    LOG.exception("總覽分頁狀態讀取失敗")
                    self.next_button.disabled = True
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception:
            LOG.exception("總覽互動更新失敗：section=%s", self.section)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "⚠️ 這個分類暫時無法開啟，請返回總覽後重試；管理員可查看日誌。",
                    ephemeral=True,
                )

    @discord.ui.button(label="◀ 上一頁", style=discord.ButtonStyle.secondary, row=2)
    async def prev_button(self, interaction, button):
        self.page = max(0, self.page-1)
        self.detail_id = None
        await self.update(interaction)

    @discord.ui.button(label="下一頁 ▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_button(self, interaction, button):
        self.page += 1
        self.detail_id = None
        await self.update(interaction)

    @discord.ui.button(label="指令大全", style=discord.ButtonStyle.primary, row=2)
    async def home_button(self, interaction, button):
        self.section = "commands"
        self.page = 0
        self.detail_id = None
        await self.update(interaction)


class SystemHub(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="總覽", description="查閱指令、活動、獎項、玩法、標籤與角色")
    @app_commands.guild_only()
    async def overview(self, interaction: discord.Interaction):
        try:
            view = OverviewView(self.bot, interaction.user.id, interaction.guild_id)
            embed = view.render()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception:
            LOG.exception("/總覽 開啟失敗：guild=%s user=%s", interaction.guild_id, interaction.user.id)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "⚠️ 總覽暫時無法載入，管理員請查看 Railway Logs 中的「/總覽 開啟失敗」。",
                    ephemeral=True,
                )

    @app_commands.command(name="排行", description="查看全服28類排行榜與排名")
    @app_commands.guild_only()
    async def ranking(self, interaction: discord.Interaction):
        try:
            view = LeaderboardView(interaction.user.id, interaction.guild_id)
            await interaction.response.send_message(embed=view.render(), view=view, ephemeral=True)
        except Exception:
            LOG.exception("排行榜查詢失敗")
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ 排行資料目前無法讀取，請查看機器人日誌。", ephemeral=True)

    @app_commands.command(name="新手指引", description="查閱創角、角色單登記與遊戲入門教學")
    @app_commands.guild_only()
    async def beginner_guide(self, interaction: discord.Interaction):
        embed = discord.Embed(title="📖 新手指引", description=(
            "**① 創角**：使用 `/創角` 建立角色，依流程完成審核。\n"
            "**② 角色單連結**：已審核角色或 NPC 可使用 `/角色單登記`，"
            "也可長按原始角色單訊息 → 應用程式 → 登記角色單。\n"
            "**③ 查看角色**：使用 `/查看` 查詢屬性、資產及背包。\n"
            "**④ 金融與商城**：使用 `/銀行`、`/商城`。\n"
            "**⑤ 申請及活動**：使用 `/申請`，活動資訊可在 `/總覽` 查看。\n"
            "**⑥ 其他玩法**：使用 `/總覽` 查閱完整指令與系統資訊。\n\n"
            "*新手任務及通告獎勵入口仍待恢復，本指引不發放獎勵。*"
        ), color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="群主指引", description="群主專用的系統建置與設定清單")
    @app_commands.guild_only()
    async def owner_guide(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("⛔ 只有伺服器群主可以查看。", ephemeral=True)
            return
        await interaction.response.send_message(OWNER_GUIDE, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SystemHub(bot))
