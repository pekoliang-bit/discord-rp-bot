"""集中申請擴充：案件儲存、玩家表單、審核面板。不額外註冊 Slash 指令。"""
from __future__ import annotations
import logging
import sqlite3
import discord
from core.database import transaction, get_connection

LOG = logging.getLogger(__name__)
CATEGORIES = {
    "report": "🚨 舉報／申訴",
    "item": "📦 物品申請",
    "company_bank": "🏦 公司銀行帳號申請",
    "leave": "📅 請假申請",
}

# 表單每一類的必填資料。物品與公司帳號核准僅記錄案件，
# 不直接發放資產或建立銀行帳戶。
FORM_FIELDS = {
    "report": [
        ("舉報對象／案件名稱", 100, False),
        ("事件時間與位置", 150, False),
        ("事實經過與要求處理事項", 1500, True),
        ("證據連結（至少一個）", 500, False),
    ],
    "item": [
        ("申請物品名稱及數量", 140, False),
        ("用途及取得理由", 1500, True),
        ("依據（活動／任務／合約等）", 250, False),
        ("證明連結（可留空）", 500, False),
    ],
    "company_bank": [
        ("公司名稱及編號", 150, False),
        ("公司負責人角色編號", 100, False),
        ("帳戶用途、授權操作人及權限", 1500, True),
        ("公司核准紀錄連結", 500, False),
    ],
    "leave": [
        ("請假開始與結束時間", 150, False),
        ("請假事由／無法參與的事項", 1500, True),
        ("請假期間需要暫停的義務", 300, False),
        ("代理人或補充說明（選填）", 500, False),
    ],
}


def init_db():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_general_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            character_id INTEGER,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            details TEXT NOT NULL,
            reference TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
            reviewer_id TEXT,
            review_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS ix_rp_general_pending
            ON rp_general_applications(guild_id,status,kind,id)""")

def owner(i):
    return bool(i.guild and i.user.id == i.guild.owner_id)

def reviewer(i):
    if owner(i): return True
    from modules.official_center import has_staff_permission
    return bool(has_staff_permission(i, "reviewer"))

async def say(i, text):
    if i.response.is_done():
        await i.followup.send(str(text)[:1900], ephemeral=True)
    else:
        await i.response.send_message(str(text)[:1900], ephemeral=True)

class ApplicationModal(discord.ui.Modal):
    """依案件類別建立不同的 Discord 表單（最多五欄）。"""

    def __init__(self, i, kind, character_id=None):
        if kind not in CATEGORIES:
            raise ValueError("未知申請類型")
        super().__init__(title=CATEGORIES[kind][:45], timeout=600)
        self.kind = kind
        self.uid = i.user.id
        self.gid = i.guild_id
        self.character_id = character_id
        self.fields = []
        for label, max_length, paragraph in FORM_FIELDS[kind]:
            optional = "選填" in label or "可留空" in label
            field = discord.ui.TextInput(
                label=label,
                max_length=max_length,
                required=not optional,
                style=(discord.TextStyle.paragraph if paragraph else discord.TextStyle.short),
            )
            self.add_item(field)
            self.fields.append(field)

    async def on_submit(self, i):
        if i.guild_id != self.gid or i.user.id != self.uid:
            return await say(i, "⛔ 這不是你的申請表單。")
        try:
            init_db()
            values = [str(f.value or "").strip() for f in self.fields]
            title = values[0]
            if not title:
                raise ValueError("請填寫申請主題。")
            details = "\n".join(
                f"【{f.label}】{value}" for f, value in zip(self.fields, values)
                if value
            )
            with transaction() as db:
                if self.character_id is not None:
                    row = db.execute(
                        "SELECT 1 FROM characters WHERE guild_id=? AND owner_id=? AND id=? AND status='active'",
                        (str(self.gid), str(self.uid), int(self.character_id)),
                    ).fetchone()
                    if row is None:
                        raise ValueError("申請角色不再有效，請重新選擇。")
                cur = db.execute(
                    """INSERT INTO rp_general_applications
                    (guild_id,user_id,character_id,kind,title,details,reference)
                    VALUES(?,?,?,?,?,?,?)""",
                    (str(self.gid), str(self.uid), self.character_id, self.kind,
                     title, details, values[-1]),
                )
                rid = cur.lastrowid
            await say(i, f"✅ {CATEGORIES[self.kind]} #{rid} 已送審。此處核准僅代表案件通過，不會自行發放物品或開立帳戶。")
        except (sqlite3.Error, ValueError) as e:
            LOG.exception("建立申請失敗")
            await say(i, f"⚠️ 申請未完成：{e}")

class ApplicationKindSelect(discord.ui.Select):
    def __init__(self):
        opts=[discord.SelectOption(label="🎪 新建活動企劃",value="event_new"),
              discord.SelectOption(label="🎪 活動企劃送審",value="event_submit"),
              discord.SelectOption(label="🎪 補充活動企劃",value="event_edit")]
        opts += [discord.SelectOption(label=v,value=k) for k,v in CATEGORIES.items()]
        super().__init__(placeholder="選擇申請類型（含活動企劃）", options=opts, row=3)

    async def callback(self,i):
        v=self.view
        if i.user.id != v.actor.user_id or str(i.guild_id) != str(v.actor.guild_id):
            return await say(i,"⛔ 這不是你的申請選單。")
        kind=self.values[0]
        if kind == "company_bank":
            return await say(i, "🏦 公司銀行帳戶申請需核對原公司申請人或系統登記代理人；目前尚未串接公司授權資料，暫不開放送出，以免冒名申請。")
        if kind.startswith("event_"):
            from modules.player_applications import EventProjectNumberModal
            if kind=="event_new":
                from modules.event_project_manager import ProjectModal
                return await i.response.send_modal(ProjectModal())
            return await i.response.send_modal(EventProjectNumberModal("submit" if kind=="event_submit" else "edit"))
        if kind == "item" and v.character_id is None:
            return await say(i,"物品申請需選擇已啟用的角色。")
        await i.response.send_modal(ApplicationModal(i,kind,v.character_id))

class ReviewApplicationSelect(discord.ui.Select):
    def __init__(self,rows):
        opts=[discord.SelectOption(label=f"#{r['id']} {CATEGORIES.get(r['kind'],r['kind'])}"[:100],
                    description=str(r['title'])[:100],value=str(r['id'])) for r in rows]
        super().__init__(placeholder="選擇待審案件",options=opts or [discord.SelectOption(label="沒有待審案件",value="none")],disabled=not bool(opts),row=0)
    async def callback(self,i):
        v=self.view
        if not reviewer(i) or i.user.id!=v.uid or i.guild_id!=v.gid:
            return await say(i,"⛔ 沒有權限操作此審核面板。")
        v.selected=int(self.values[0]); await v.render(i)

class GeneralReviewView(discord.ui.View):
    def __init__(self,i,page=0):
        super().__init__(timeout=600)
        self.gid=i.guild_id; self.uid=i.user.id
        self.owner_mode=owner(i)
        self.page=page;self.selected=None
        self.refresh()
    def refresh(self):
        init_db()
        with get_connection() as db:
            self.rows=db.execute("""SELECT * FROM rp_general_applications
                 WHERE guild_id=? AND status='pending' AND (?=1 OR kind!='report')
                 ORDER BY id ASC LIMIT 20 OFFSET ?""",
                 (str(self.gid), 1 if self.owner_mode else 0, self.page*20)).fetchall()
        for x in list(self.children):
            if isinstance(x,ReviewApplicationSelect):self.remove_item(x)
        self.add_item(ReviewApplicationSelect(self.rows))
        self.prev.disabled=self.page==0
        self.next.disabled=len(self.rows)<20
        self.approve.disabled=self.reject.disabled=self.selected is None
    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.uid or not reviewer(i):
            await say(i,"⛔ 你不能使用這個審核面板。")
            return False
        return True
    def content(self):
        if self.selected is not None:
            with get_connection() as db:
                r=db.execute("SELECT * FROM rp_general_applications WHERE guild_id=? AND id=?",(str(self.gid),self.selected)).fetchone()
            if r is not None and r['status']=='pending' and (self.owner_mode or r['kind']!='report'):
                return (f"🛡️ **案件 #{r['id']}｜{CATEGORIES.get(r['kind'],r['kind'])}**\n"
                        f"申請者：<@{r['user_id']}>｜角色：{r['character_id'] or '未指定'}\n"
                        f"標題：{discord.utils.escape_mentions(r['title'])}\n"
                        f"內容：{discord.utils.escape_mentions(r['details'])[:1500]}\n"
                        f"附件／參照：{discord.utils.escape_mentions(r['reference'])[:300]}")[:1950]
        return f"🛡️ **一般申請審核**｜第 {self.page+1} 頁\n待審：{len(self.rows)} 筆（本頁）\n請從選單選擇案件。"
    async def render(self,i):
        self.refresh()
        await i.response.edit_message(content=self.content(),view=self,allowed_mentions=discord.AllowedMentions.none())
    async def decide(self,i,accepted):
        if self.selected is None:return await say(i,"請先選擇案件。")
        try:
            with transaction() as db:
                r=db.execute("SELECT * FROM rp_general_applications WHERE guild_id=? AND id=? AND status='pending'",
                             (str(self.gid),self.selected)).fetchone()
                if r is None:raise ValueError("案件已被處理。")
                if r['kind']=='report' and not owner(i):
                    raise PermissionError("舉報案件僅群主可於此面板處理，防止內容外洩。")
                if r['kind']=='company_bank':
                    raise PermissionError("公司銀行帳戶需完成申請人／代理授權核驗後，才能核准。")
                if not owner(i) and str(r['user_id'])==str(i.user.id):
                    raise PermissionError("審核人員不能審核自己的案件。")
                cur=db.execute("""UPDATE rp_general_applications SET status=?,reviewer_id=?,reviewed_at=CURRENT_TIMESTAMP
                    WHERE guild_id=? AND id=? AND status='pending'""",
                    ('approved' if accepted else 'rejected',str(i.user.id),str(self.gid),self.selected))
                if cur.rowcount!=1:raise ValueError("案件已被其他人處理。")
            self.selected=None
            await self.render(i)
        except (ValueError,PermissionError,sqlite3.Error) as e:
            await say(i,f"⚠️ {e}")
    @discord.ui.button(label="✅ 核准",style=discord.ButtonStyle.success,row=1)
    async def approve(self,i,b): await self.decide(i,True)
    @discord.ui.button(label="❌ 駁回",style=discord.ButtonStyle.danger,row=1)
    async def reject(self,i,b): await self.decide(i,False)
    @discord.ui.button(label="◀ 上頁",style=discord.ButtonStyle.secondary,row=2)
    async def prev(self,i,b):
        self.page=max(0,self.page-1);self.selected=None;await self.render(i)
    @discord.ui.button(label="下頁 ▶",style=discord.ButtonStyle.secondary,row=2)
    async def next(self,i,b):
        self.page+=1;self.selected=None;await self.render(i)

class OpenGeneralReview(discord.ui.Button):
    def __init__(self):super().__init__(label="📨 其他申請審核",style=discord.ButtonStyle.secondary,row=1)
    async def callback(self,i):
        if not reviewer(i):return await say(i,"⛔ 沒有審核權限。")
        v=GeneralReviewView(i)
        await i.response.send_message(v.content(),view=v,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
