"""系統總覽 V3：公開資料查詢、玩法、指令、獨立群主指引。"""
from __future__ import annotations

import logging
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from core.database import get_connection

LOG = logging.getLogger(__name__)
PAGE_SIZE = 8
MENU = {
    "commands": "📚 指令大全",
    "event_open": "🎟️ 報名中活動",
    "event_running": "🎪 進行中活動",
    "event_closed": "🏁 已完成活動",
    "guide": "🎮 玩法說明",
    "tags": "🏷️ 標籤庫",
    "characters": "🎭 全服角色名單",
    "awards": "🏆 公開獎項圖鑑",
}
GUIDES = {
    "角色與背包": "使用 `/創角` 建立角色；使用 `/查看` 查閱角色資料、背包、稱號與裝備。各功能逐步支援直接指定角色編號或姓名。",
    "申請與審核": "玩家透過 `/申請` 提交企劃、店鋪、金融、檢舉等申請；授權人員在 `/審核` 處理。案件結果將由通知系統集中通知。",
    "活動玩法": "透過 `/總覽` 查看報名中、進行中及歷史活動。正式報名時依公告使用報名按鈕；任務證明由官方審核。",
    "經濟與商店": "透過 `/銀行` 管理角色資金；透過 `/商城` 查看商品及背包。交易前請確認指定的角色與金額。",
    "陣營與公司": "透過 `/陣營`、`/公司` 查詢相關組織與經營功能；涉及申請的流程統一由 `/申請` 進入。",
    "任務與新手": "新手指引安排在 `/任務`，總覽只提供玩法查詢，不直接發送新手獎勵。",
}
OWNER_GUIDE = (
    "👑 **群主建置指引**\n\n"
    "① 確認 Railway Volume 和資料庫備份。\n"
    "② 設定 Discord 權限、身分組和必要頻道。\n"
    "③ 進入 `/官方` 設定授權人員與系統參數。\n"
    "④ 設定角色、店鋪、活動及金融審核職能。\n"
    "⑤ 設定活動報名頻道與通知頻道。\n"
    "⑥ 透過測試角色驗證創角、申請、審核與交易。\n"
    "⑦ 檢查 `/總覽` 公開資料及 Railway Logs。\n\n"
    "*本指引不會自動變更伺服器設定。*"
)


def escape(value, limit=130):
    s = discord.utils.escape_markdown(discord.utils.escape_mentions(str(value or "")))
    return s[:limit] or "（未填寫）"


def query_rows(gid, section):
    if section.startswith("event_"):
        status = section.split("_", 1)[1]
        sql = """SELECT id,title,description,capacity,channel_id,status
                 FROM rp_event_projects WHERE guild_id=? AND status=?
                 ORDER BY id DESC"""
        with get_connection() as db:
            return [dict(r) for r in db.execute(sql, (str(gid), status))]
    if section == "awards":
        with get_connection() as db:
            return [dict(r) for r in db.execute(
                """SELECT id,kind,name,description,condition_json,rewards_json
                   FROM rp_award_definitions
                   WHERE guild_id=? AND active=1 AND hidden=0
                   ORDER BY id DESC""", (str(gid),))]
    if section == "tags":
        from modules.tag_library import seed
        seed(gid)
        with get_connection() as db:
            return [dict(r) for r in db.execute(
                """SELECT id,name,category FROM rp_tag_library
                   WHERE guild_id=? AND enabled=1 ORDER BY category,id""", (str(gid),))]
    if section == "characters":
        with get_connection() as db:
            return [dict(r) for r in db.execute(
                """SELECT id,public_code,name,character_type,occupation,status
                   FROM characters WHERE guild_id=? AND status IN ('active','retired')
                   ORDER BY id DESC""", (str(gid),))]
    return []


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
        if self.section in ("characters", "awards") or self.section.startswith("event_"):
            try:
                rows = query_rows(self.guild_id, self.section)
                visible = rows[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]
                if visible:
                    self.add_item(DetailSelect(visible, self.section, self.detail_id))
            except sqlite3.Error:
                LOG.exception("建立總覽詳情選單失敗")
        if self.section == "guide":
            self.add_item(GuideSelect(self.guide))
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.section in ("guide", "commands")
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.home_button)

    def render(self):
        title = MENU[self.section]
        embed = discord.Embed(title=f"🌐 流光城｜{title}", color=discord.Color.blurple())
        if self.section == "guide":
            embed.add_field(name=self.guide, value=GUIDES[self.guide], inline=False)
            embed.set_footer(text="新手教學與任務獎勵請使用 /任務")
            return embed
        if self.section == "commands":
            commands_list = sorted(self.bot.tree.get_commands(), key=lambda c:c.name)
            visible = [c for c in commands_list if c.type == discord.AppCommandType.chat_input]
            embed.description = "目前機器人已載入的頂層斜線指令（非所有功能都已完成實測）。"
            lines = [f"`/{escape(c.name,50)}`｜{escape(c.description,100)}" for c in visible]
            # Discord Embed description max 4096, separate into fields.
            for start in range(0, min(len(lines), 120), 16):
                embed.add_field(name=f"指令 {start+1}–{min(start+16,len(lines))}",
                                value="\n".join(lines[start:start+16])[:1024] or "無", inline=False)
                if len(embed.fields) >= 6:
                    break
            embed.set_footer(text=f"目前載入 {len(visible)} 個頂層 Slash Command")
            return embed
        try:
            rows = query_rows(self.guild_id, self.section)
        except (sqlite3.Error, ImportError) as exc:
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
                detail = self.render_detail(selected)
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
        if self.section == "characters":
            return (f"角色編號：{escape(r.get('public_code') or r['id'],50)}\n"
                    f"角色名稱：{escape(r['name'],100)}\n"
                    f"類別：{escape(r['character_type'],70)}\n"
                    f"職業：{escape(r['occupation'],100)}\n"
                    f"公開狀態：{escape(r['status'],40)}\n"
                    "詳細角色面板請使用 `/查看`。")
        return ""

    async def update(self, interaction):
        self.rebuild()
        embed = self.render()
        # render may clamp page; update navigation after it.
        self.rebuild()
        if self.section not in ('guide','commands'):
            try:
                count = len(query_rows(self.guild_id, self.section))
                self.next_button.disabled = (self.page+1)*PAGE_SIZE >= count
            except (sqlite3.Error, ImportError):
                self.next_button.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

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
        view = OverviewView(self.bot, interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(embed=view.render(), view=view, ephemeral=True)

    @app_commands.command(name="群主指引", description="群主專用的系統建置與設定清單")
    @app_commands.guild_only()
    async def owner_guide(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("⛔ 只有伺服器群主可以查看。", ephemeral=True)
            return
        await interaction.response.send_message(OWNER_GUIDE, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SystemHub(bot))
