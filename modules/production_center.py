"""Discord RP：作品企劃、招募、完工驗證與履歷連動。

指令 /作品；不生成合約、不發獎勵、不結算片酬。
依賴 modules.registration 的正式認證履歷 API。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction
from core.permissions import Actor, InvalidState, PermissionDenied
from modules.registration import get_types, register_verified_event
from modules.official_center import has_staff_permission

LOG = logging.getLogger(__name__)
PAGE = 25
CATEGORIES = ("電影", "電視劇", "網路劇", "音樂作品", "MV", "代言", "綜藝", "舞台劇", "商業活動", "其他")


def initialize():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS production_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            production_id INTEGER NOT NULL,
            position_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            decided_by TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            decided_at TEXT,
            UNIQUE(guild_id, production_id, position_id, character_id),
            FOREIGN KEY(guild_id,production_id) REFERENCES productions(guild_id,id),
            FOREIGN KEY(guild_id,position_id) REFERENCES production_positions(guild_id,id),
            FOREIGN KEY(guild_id,character_id) REFERENCES characters(guild_id,id),
            CHECK(status IN ('pending','accepted','rejected'))
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_production_apps
            ON production_applications(guild_id,production_id,status)""")
        db.execute("""CREATE TABLE IF NOT EXISTS production_completion (
            guild_id TEXT NOT NULL,
            production_id INTEGER NOT NULL,
            requested_by TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by TEXT,
            reason TEXT,
            requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,
            PRIMARY KEY(guild_id,production_id),
            CHECK(status IN ('pending','approved','rejected')),
            FOREIGN KEY(guild_id,production_id) REFERENCES productions(guild_id,id)
        )""")


def audit(db, actor, action, target, data):
    db.execute("""INSERT INTO audit_events
        (guild_id,actor_id,action,target_key,details_json) VALUES(?,?,?,?,?)""",
        (actor.guild_id,actor.user_id,action,target,json.dumps(data,ensure_ascii=False)))


def check(actor, cid, db):
    row = db.execute("""SELECT id,name FROM characters WHERE guild_id=? AND id=?
        AND owner_id=? AND status='active' AND character_type='player'""",
        (actor.guild_id,int(cid),actor.user_id)).fetchone()
    if not row:
        raise PermissionDenied("請選擇本人已啟用的原創角色。")
    return dict(row)


def get_production(db, guild_id, pid):
    row=db.execute("""SELECT * FROM productions WHERE guild_id=? AND id=?""",
        (str(guild_id),int(pid))).fetchone()
    if not row:
        raise InvalidState("找不到該作品企劃。")
    return dict(row)


def create_production(actor, character_id, title, category, scale):
    title=str(title).strip()
    if not 1<=len(title)<=150:
        raise ValueError("作品名稱應為1～150字。")
    if category not in CATEGORIES:
        raise ValueError("請選擇現有登記類型。")
    if scale not in ("S1","S2","S3","S4","S5"):
        raise ValueError("作品規模須為 S1～S5。")
    with transaction() as db:
        check(actor,character_id,db)
        cur=db.execute("""INSERT INTO productions(guild_id,title,category,scale,
            owner_character_id,status) VALUES(?,?,?,?,?,'recruiting')""",
            (actor.guild_id,title,category,scale,int(character_id)))
        pid=cur.lastrowid
        pos=db.execute("""INSERT INTO production_positions
            (guild_id,production_id,title,capacity) VALUES(?,?,?,1)""",
            (actor.guild_id,pid,"製作人"))
        db.execute("""INSERT INTO production_applications
            (guild_id,production_id,position_id,character_id,status,decided_by,decided_at)
            VALUES(?,?,?,?, 'accepted', ?, CURRENT_TIMESTAMP)""",
            (actor.guild_id,pid,pos.lastrowid,int(character_id),actor.user_id))
        audit(db,actor,"production.create",f"production:{pid}",{"title":title})
        return pid


def add_position(actor, pid, title, capacity):
    title=str(title).strip()
    if not 1<=len(title)<=80 or not 1<=capacity<=100:
        raise ValueError("職位名稱1～80字、名額1～100人。")
    with transaction() as db:
        project=get_production(db,actor.guild_id,pid)
        check(actor,project["owner_character_id"],db)
        if project["status"]!="recruiting":
            raise InvalidState("只有招募中作品可新增職位。")
        total=db.execute("""SELECT COUNT(*) AS n FROM production_positions
            WHERE guild_id=? AND production_id=?""",(actor.guild_id,pid)).fetchone()["n"]
        if total>=100:
            raise InvalidState("每部作品最多100種職位。")
        cur=db.execute("""INSERT INTO production_positions
            (guild_id,production_id,title,capacity) VALUES(?,?,?,?)""",
            (actor.guild_id,pid,title,int(capacity)))
        audit(db,actor,"production.position",f"production:{pid}",{"position_id":cur.lastrowid})
        return cur.lastrowid


def apply_position(actor, pid, position_id, character_id):
    with transaction() as db:
        check(actor,character_id,db)
        project=get_production(db,actor.guild_id,pid)
        if project["status"]!="recruiting":
            raise InvalidState("此作品目前未開放招募。")
        position=db.execute("""SELECT id FROM production_positions
            WHERE guild_id=? AND production_id=? AND id=?""",
            (actor.guild_id,pid,position_id)).fetchone()
        if not position:
            raise InvalidState("作品中沒有這個職位。")
        existing=db.execute("""SELECT id FROM production_applications WHERE guild_id=?
            AND production_id=? AND position_id=? AND character_id=?""",
            (actor.guild_id,pid,position_id,character_id)).fetchone()
        if existing:
            raise InvalidState("該角色已經申請過這個職位。")
        cur=db.execute("""INSERT INTO production_applications
            (guild_id,production_id,position_id,character_id) VALUES(?,?,?,?)""",
            (actor.guild_id,pid,position_id,character_id))
        audit(db,actor,"production.apply",f"production_application:{cur.lastrowid}",{})
        return cur.lastrowid


def decide(actor,pid,app_id,accept):
    with transaction() as db:
        project=get_production(db,actor.guild_id,pid)
        check(actor,project["owner_character_id"],db)
        if project["status"]!="recruiting":
            raise InvalidState("該作品已停止招募。")
        row=db.execute("""SELECT a.*,p.capacity FROM production_applications a
            JOIN production_positions p ON p.guild_id=a.guild_id AND p.id=a.position_id
            WHERE a.guild_id=? AND a.production_id=? AND a.id=? AND a.status='pending'""",
            (actor.guild_id,pid,app_id)).fetchone()
        if not row:
            raise InvalidState("申請不存在或已處理。")
        if accept:
            accepted=db.execute("""SELECT COUNT(*) AS n FROM production_applications
                WHERE guild_id=? AND position_id=? AND status='accepted'""",
                (actor.guild_id,row["position_id"])).fetchone()["n"]
            if accepted>=row["capacity"]:
                raise InvalidState("此職位名額已滿。")
        new="accepted" if accept else "rejected"
        db.execute("""UPDATE production_applications SET status=?,decided_by=?,
            decided_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=? AND status='pending'""",
            (new,actor.user_id,actor.guild_id,app_id))
        audit(db,actor,"production.decide",f"production_application:{app_id}",{"status":new})
        return new


def request_completion(actor,pid):
    with transaction() as db:
        project=get_production(db,actor.guild_id,pid)
        check(actor,project["owner_character_id"],db)
        if project["status"]!="recruiting":
            raise InvalidState("此企劃目前不可申請完工驗證。")
        count=db.execute("""SELECT COUNT(*) AS n FROM production_applications WHERE
            guild_id=? AND production_id=? AND status='accepted'""",
            (actor.guild_id,pid)).fetchone()["n"]
        if count<1:
            raise InvalidState("作品沒有正式參與成員。")
        db.execute("""INSERT INTO production_completion(guild_id,production_id,requested_by,status)
            VALUES(?,?,?,'pending') ON CONFLICT(guild_id,production_id) DO UPDATE SET
            requested_by=excluded.requested_by,status='pending',reviewed_by=NULL,
            reason=NULL,requested_at=CURRENT_TIMESTAMP,reviewed_at=NULL""",
            (actor.guild_id,pid,actor.user_id))
        db.execute("""UPDATE productions SET status='pending_completion'
            WHERE guild_id=? AND id=?""",(actor.guild_id,pid))
        audit(db,actor,"production.request_complete",f"production:{pid}",{})


def finalize_completion(interaction,actor,pid,approve,reason=""):
    if not has_staff_permission(interaction,"reviewer"):
        raise PermissionDenied("需要正式審核員授權。")
    reason=str(reason).strip()[:500]
    if not approve and not reason:
        raise ValueError("退件需要說明原因。")
    # 前半段與作品完工狀態在同一交易中；履歷寫入冪等且可重試。
    with transaction() as db:
        project=get_production(db,actor.guild_id,pid)
        if project["status"]!="pending_completion":
            raise InvalidState("作品不在待驗證完工狀態。")
        owner=db.execute("""SELECT owner_id FROM characters WHERE guild_id=? AND id=?""",
            (actor.guild_id,project["owner_character_id"])).fetchone()
        if owner and str(owner["owner_id"])==actor.user_id:
            raise PermissionDenied("不能審核自己擁有的作品。")
        if interaction.guild.owner_id != interaction.user.id:
            grant=db.execute("""SELECT 1 FROM official_staff_grants WHERE guild_id=?
                AND user_id=? AND staff_type='reviewer'""",
                (actor.guild_id,actor.user_id)).fetchone()
            if not grant:
                raise PermissionDenied("你的審核資格已被撤銷。")
        db.execute("""UPDATE production_completion SET status=?,reviewed_by=?,reason=?,
            reviewed_at=CURRENT_TIMESTAMP WHERE guild_id=? AND production_id=? AND status='pending'""",
            ("approved" if approve else "rejected",actor.user_id,reason,actor.guild_id,pid))
        db.execute("""UPDATE productions SET status=? WHERE guild_id=? AND id=?""",
            ("completed" if approve else "recruiting",actor.guild_id,pid))
        audit(db,actor,"production.complete_review",f"production:{pid}",{"approved":approve,"reason":reason})
    if approve:
        # 角色履歷與作品狀態在不同交易，呼叫冪等鍵，失敗可安全重跑下方同步函式。
        return sync_verified_careers(actor.guild_id,pid)
    return 0


def sync_verified_careers(guild_id,pid):
    """安全重試：已核准作品的每位參與者每一職位僅寫一次履歷。"""
    with get_connection() as db:
        project=get_production(db,guild_id,pid)
        if project["status"]!="completed":
            raise InvalidState("僅完成正式驗證的作品可同步履歷。")
        rows=db.execute("""SELECT a.character_id,a.position_id,p.title AS position_title
            FROM production_applications a JOIN production_positions p
            ON p.guild_id=a.guild_id AND p.id=a.position_id
            WHERE a.guild_id=? AND a.production_id=? AND a.status='accepted'""",
            (str(guild_id),pid)).fetchall()
    types=get_types(guild_id,active_only=False)
    typ=next((x for x in types if x["name"]==project["category"]),None)
    if not typ or not typ["enabled"]:
        raise InvalidState("登記類型不存在或已停用，需先恢復類型再同步。")
    names=json.loads(typ["fields_json"])
    year=str(datetime.now().year)
    total=0
    for row in rows:
        values={}
        for name in names:
            if any(word in name for word in ("年份","年度")):
                values[name]=year
            elif any(word in name for word in ("職務","身分","角色")):
                values[name]=str(row["position_title"])
            else:
                values[name]=str(project["title"])
        key=f"production:{pid}:position:{row['position_id']}:character:{row['character_id']}"
        register_verified_event(guild_id,row["character_id"],typ["id"],
            project["title"],values,key,production_id=pid)
        total+=1
    return total


def available_characters(actor):
    with get_connection() as db:
        rows=db.execute("""SELECT id,name FROM characters WHERE guild_id=? AND
            owner_id=? AND status='active' AND character_type='player' ORDER BY id""",
            (actor.guild_id,actor.user_id)).fetchall()
    return [dict(x) for x in rows]


def projects(actor,owned=False):
    with get_connection() as db:
        if owned:
            rows=db.execute("""SELECT p.id,p.title,p.status FROM productions p
                JOIN characters c ON c.guild_id=p.guild_id AND c.id=p.owner_character_id
                WHERE p.guild_id=? AND c.owner_id=? ORDER BY p.id DESC LIMIT 25""",
                (actor.guild_id,actor.user_id)).fetchall()
        else:
            rows=db.execute("""SELECT id,title,status FROM productions WHERE guild_id=?
                AND status='recruiting' ORDER BY id DESC LIMIT 25""",
                (actor.guild_id,)).fetchall()
    return [dict(x) for x in rows]


async def failure(interaction,exc):
    msg=f"⚠️ {str(exc)[:1600]}"
    if interaction.response.is_done():
        await interaction.followup.send(msg,ephemeral=True)
    else:
        await interaction.response.send_message(msg,ephemeral=True)


class Gate(discord.ui.View):
    def __init__(self,actor):
        super().__init__(timeout=900)
        self.actor=actor
    async def interaction_check(self,interaction):
        if str(interaction.guild_id)!=self.actor.guild_id or str(interaction.user.id)!=self.actor.user_id:
            await failure(interaction,PermissionDenied("這不是你的操作介面。"))
            return False
        return True


class CreateModal(discord.ui.Modal,title="建立作品企劃"):
    title_field=discord.ui.TextInput(label="作品名稱",max_length=150)
    category_field=discord.ui.TextInput(label="作品分類",placeholder="電影／電視劇／MV／舞台劇等",max_length=30)
    scale_field=discord.ui.TextInput(label="規模",default="S1",max_length=2)
    def __init__(self,actor,cid):
        super().__init__()
        self.actor,self.cid=actor,cid
    async def on_submit(self,interaction):
        try:
            number=create_production(self.actor,self.cid,str(self.title_field.value),
                str(self.category_field.value).strip(),str(self.scale_field.value).upper().strip())
            await interaction.response.send_message(
                f"✅ 作品企劃 #{number} 建立成功。\n已將創辦角色登記為製作人。\n請從 /作品 管理企劃及新增招募職位。",
                ephemeral=True)
        except (ValueError,InvalidState,PermissionDenied) as exc:
            await failure(interaction,exc)


class PositionModal(discord.ui.Modal,title="新增招募職位"):
    position_field=discord.ui.TextInput(label="職位名稱",max_length=80)
    count_field=discord.ui.TextInput(label="招募名額",placeholder="1",max_length=3)
    def __init__(self,actor,pid):
        super().__init__()
        self.actor,self.pid=actor,pid
    async def on_submit(self,interaction):
        try:
            n=add_position(self.actor,self.pid,str(self.position_field.value),int(self.count_field.value))
            await interaction.response.send_message(f"✅ 職位 #{n} 已開放招募。",ephemeral=True)
        except (ValueError,InvalidState,PermissionDenied) as exc:
            await failure(interaction,exc)


class Select(discord.ui.Select):
    def __init__(self,rows,placeholder):
        super().__init__(placeholder=placeholder,options=[discord.SelectOption(
            label=f"#{x['id']} {x.get('title',x.get('name',''))}"[:100],value=str(x["id"]))
            for x in rows[:25]])
    async def callback(self,interaction):
        if not await self.view.interaction_check(interaction):
            return
        await self.view.chosen(interaction,int(self.values[0]))


class ChooseCharacter(Gate):
    def __init__(self,actor,action,pid=None,position=None):
        super().__init__(actor)
        self.action,self.pid,self.position=action,pid,position
        chars=available_characters(actor)
        if chars:
            self.add_item(Select(chars,"選擇自己已啟用的角色"))
    async def chosen(self,interaction,cid):
        if self.action=="create":
            await interaction.response.send_modal(CreateModal(self.actor,cid))
        else:
            try:
                number=apply_position(self.actor,self.pid,self.position,cid)
                await interaction.response.edit_message(content=f"✅ 已送出職位申請 #{number}。",view=None)
            except (InvalidState,PermissionDenied,ValueError) as exc:
                await failure(interaction,exc)


class ChoosePosition(Gate):
    def __init__(self,actor,pid):
        super().__init__(actor)
        self.pid=pid
        with get_connection() as db:
            rows=[dict(x) for x in db.execute("""SELECT id,title FROM production_positions
                WHERE guild_id=? AND production_id=? AND title != '製作人' ORDER BY id LIMIT 25""",
                (actor.guild_id,pid)).fetchall()]
        if rows:
            self.add_item(Select(rows,"選擇想申請的職位"))
    async def chosen(self,interaction,pos):
        await interaction.response.edit_message(content="請選擇參演角色：",
            view=ChooseCharacter(self.actor,"apply",self.pid,pos))


class ApplicationChoices(Gate):
    def __init__(self,actor,pid):
        super().__init__(actor)
        self.pid=pid
        with get_connection() as db:
            rows=[dict(x) for x in db.execute("""SELECT a.id,
                c.name AS title FROM production_applications a JOIN characters c
                ON c.guild_id=a.guild_id AND c.id=a.character_id
                WHERE a.guild_id=? AND a.production_id=? AND a.status='pending'
                ORDER BY a.id LIMIT 25""",(actor.guild_id,pid)).fetchall()]
        if rows:
            self.add_item(Select(rows,"選擇待審職位申請"))
    async def chosen(self,interaction,app_id):
        await interaction.response.edit_message(content=f"職位申請 #{app_id}：是否錄取？",
            view=DecideView(self.actor,self.pid,app_id))


class DecideView(Gate):
    def __init__(self,actor,pid,app_id):
        super().__init__(actor)
        self.pid,self.app_id=pid,app_id
    @discord.ui.button(label="✅ 錄取",style=discord.ButtonStyle.success)
    async def accept(self,interaction,button):
        try:
            result=decide(self.actor,self.pid,self.app_id,True)
            await interaction.response.edit_message(content=f"已處理：{result}",view=None)
        except (PermissionDenied,InvalidState,ValueError) as exc:
            await failure(interaction,exc)
    @discord.ui.button(label="❌ 不錄取",style=discord.ButtonStyle.danger)
    async def reject(self,interaction,button):
        try:
            result=decide(self.actor,self.pid,self.app_id,False)
            await interaction.response.edit_message(content=f"已處理：{result}",view=None)
        except (PermissionDenied,InvalidState,ValueError) as exc:
            await failure(interaction,exc)


class ProjectActions(Gate):
    def __init__(self,actor,pid):
        super().__init__(actor)
        self.pid=pid
    @discord.ui.button(label="➕ 新增招募職位",style=discord.ButtonStyle.primary)
    async def position(self,interaction,button):
        try:
            with get_connection() as db:
                project=get_production(db,self.actor.guild_id,self.pid)
                check(self.actor,project["owner_character_id"],db)
            await interaction.response.send_modal(PositionModal(self.actor,self.pid))
        except (PermissionDenied,InvalidState) as exc:
            await failure(interaction,exc)
    @discord.ui.button(label="📥 錄取申請",style=discord.ButtonStyle.secondary)
    async def applications(self,interaction,button):
        try:
            with get_connection() as db:
                project=get_production(db,self.actor.guild_id,self.pid)
                check(self.actor,project["owner_character_id"],db)
            await interaction.response.edit_message(content="請選擇待審報名：",view=ApplicationChoices(self.actor,self.pid))
        except (PermissionDenied,InvalidState) as exc:
            await failure(interaction,exc)
    @discord.ui.button(label="🎬 申請完工驗證",style=discord.ButtonStyle.success)
    async def complete(self,interaction,button):
        try:
            request_completion(self.actor,self.pid)
            await interaction.response.edit_message(content="✅ 已送出作品完工驗證。需由其他正式審核員確認，通過後才會同步正式履歷。",view=None)
        except (PermissionDenied,InvalidState) as exc:
            await failure(interaction,exc)


class ProjectList(Gate):
    def __init__(self,actor,mode):
        super().__init__(actor)
        self.mode=mode
        rows=projects(actor,owned=mode=="owned")
        if rows:
            self.add_item(Select(rows,"選擇作品企劃"))
    async def chosen(self,interaction,pid):
        if self.mode=="owned":
            await interaction.response.edit_message(content=f"🎬 作品 #{pid} 管理",view=ProjectActions(self.actor,pid))
        else:
            await interaction.response.edit_message(content=f"🎭 作品 #{pid} 職位招募",view=ChoosePosition(self.actor,pid))


class CompletionSelect(discord.ui.Select):
    def __init__(self,actor):
        with get_connection() as db:
            rows=db.execute("""SELECT p.id,p.title FROM productions p WHERE
                p.guild_id=? AND p.status='pending_completion' ORDER BY p.id LIMIT 25""",
                (actor.guild_id,)).fetchall()
        super().__init__(placeholder="選擇待驗證完工作品",options=[
            discord.SelectOption(label=f"#{r['id']} {r['title']}"[:100],value=str(r['id']))
            for r in rows] or [discord.SelectOption(label="沒有待審作品",value="empty")],
            disabled=not bool(rows))
    async def callback(self,interaction):
        if not await self.view.interaction_check(interaction):
            return
        await interaction.response.edit_message(content=f"作品 #{self.values[0]}：完工驗證",view=CompletionActions(self.view.actor,int(self.values[0])))


class CompletionList(Gate):
    def __init__(self,actor):
        super().__init__(actor)
        self.add_item(CompletionSelect(actor))


class CompletionActions(Gate):
    def __init__(self,actor,pid):
        super().__init__(actor)
        self.pid=pid
    @discord.ui.button(label="✅ 核准完工",style=discord.ButtonStyle.success)
    async def approve(self,interaction,button):
        try:
            n=finalize_completion(interaction,self.actor,self.pid,True)
            await interaction.response.edit_message(content=f"✅ 完工驗證已通過，已同步 {n} 筆參與履歷。",view=None)
        except (PermissionDenied,InvalidState,ValueError) as exc:
            await failure(interaction,exc)
    @discord.ui.button(label="❌ 退回招募",style=discord.ButtonStyle.danger)
    async def reject(self,interaction,button):
        await interaction.response.send_modal(CompletionRejectModal(self.actor,self.pid))


class CompletionRejectModal(discord.ui.Modal,title="作品完工退件"):
    reason=discord.ui.TextInput(label="退件原因",max_length=500,style=discord.TextStyle.paragraph)
    def __init__(self,actor,pid):
        super().__init__()
        self.actor,self.pid=actor,pid
    async def on_submit(self,interaction):
        try:
            finalize_completion(interaction,self.actor,self.pid,False,str(self.reason.value))
            await interaction.response.send_message("✅ 已退回招募階段。",ephemeral=True)
        except (PermissionDenied,InvalidState,ValueError) as exc:
            await failure(interaction,exc)


class Home(Gate):
    @discord.ui.button(label="🎬 建立企劃",style=discord.ButtonStyle.success,row=0)
    async def create(self,interaction,button):
        if not available_characters(self.actor):
            await failure(interaction,InvalidState("需要先啟用一名原創角色。"))
        else:
            await interaction.response.send_message("選擇建立企劃的角色：",view=ChooseCharacter(self.actor,"create"),ephemeral=True)
    @discord.ui.button(label="🎭 報名作品",style=discord.ButtonStyle.primary,row=0)
    async def join(self,interaction,button):
        await interaction.response.send_message("選擇正在招募的作品：",view=ProjectList(self.actor,"join"),ephemeral=True)
    @discord.ui.button(label="📁 我的企劃",style=discord.ButtonStyle.secondary,row=1)
    async def owned(self,interaction,button):
        await interaction.response.send_message("選擇自己建立的作品：",view=ProjectList(self.actor,"owned"),ephemeral=True)
    @discord.ui.button(label="🛡️ 完工審核",style=discord.ButtonStyle.secondary,row=1)
    async def review(self,interaction,button):
        if not has_staff_permission(interaction,"reviewer"):
            await failure(interaction,PermissionDenied("僅正式審核員可查看。"))
        else:
            await interaction.response.send_message("待驗證完工企劃：",view=CompletionList(self.actor),ephemeral=True)


class ProductionCenter(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
    async def cog_load(self):
        initialize()
    @app_commands.command(name="作品",description="建立作品企劃、報名職位、錄取與完工驗證")
    @app_commands.guild_only()
    async def production(self,interaction:discord.Interaction):
        actor=Actor.from_interaction(interaction)
        await interaction.response.send_message(
            "🎬 **作品製作中心**\n建立企劃、招募與錄取演藝職位；正式完工驗證後同步至 /登記。\n不自動簽約、不發片酬或屬性獎勵。",
            view=Home(actor),ephemeral=True)


async def setup(bot:commands.Bot):
    await bot.add_cog(ProductionCenter(bot))
