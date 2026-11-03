"""演藝圈 RP：登記中心。新建 modules/registration.py。

/登記：玩家登記、履歷、官方待審、類型管理。
自動登記 API：register_verified_event，僅供信任的伺服器模組調用。
所有登記預設不發金錢／屬性獎勵。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction
from core.permissions import Actor, InvalidState, PermissionDenied

LOG = logging.getLogger(__name__)
PAGE = 25
DEFAULT_TYPES = (
    ("電影", ["作品名稱", "擔任職務", "上映年份"]),
    ("電視劇", ["作品名稱", "擔任職務", "播出年份"]),
    ("網路劇", ["作品名稱", "擔任職務", "播出年份"]),
    ("音樂作品", ["作品名稱", "演唱或製作身分", "發行年份"]),
    ("MV", ["作品名稱", "參與身分", "發行年份"]),
    ("代言", ["品牌或商品", "合作身分", "合作年份"]),
    ("綜藝", ["節目名稱", "參與身分", "播出年份"]),
    ("舞台劇", ["劇目名稱", "擔任職務", "演出年份"]),
    ("商業活動", ["活動名稱", "參與身分", "活動年份"]),
    ("其他", ["項目名稱", "參與身分", "發生年份"]),
)


def initialize():
    """新增獨立表，不更動既有 productions/contracts。"""
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS registration_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            fields_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (guild_id, name),
            FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS career_registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            type_id INTEGER NOT NULL,
            submitted_by TEXT NOT NULL,
            title TEXT NOT NULL,
            fields_json TEXT NOT NULL,
            evidence_url TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            source_key TEXT,
            production_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer_id TEXT,
            review_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,
            UNIQUE (guild_id, source, source_key),
            FOREIGN KEY (guild_id, character_id) REFERENCES characters(guild_id, id),
            FOREIGN KEY (type_id) REFERENCES registration_types(id),
            CHECK (source IN ('manual','automatic')),
            CHECK (status IN ('pending','approved','rejected'))
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_career_owner ON career_registrations(guild_id,character_id,status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_career_review ON career_registrations(guild_id,status,id)")



def ensure_types(guild_id):
    """先建立伺服器紀錄，再初始化登記分類。"""

    with transaction() as db:
        # 先確保伺服器存在，避免外鍵錯誤
        db.execute(
            """
            INSERT OR IGNORE INTO guilds (guild_id)
            VALUES (?)
            """,
            (str(guild_id),),
        )

        # 建立預設登記類型
        for name, fields in DEFAULT_TYPES:
            db.execute(
                """
                INSERT OR IGNORE INTO registration_types
                    (guild_id, name, fields_json)
                VALUES (?, ?, ?)
                """,
                (
                    str(guild_id),
                    name,
                    json.dumps(
                        fields,
                        ensure_ascii=False,
                    ),
                ),
            )


def audit(db, actor, action, key, details):
    db.execute("""INSERT INTO audit_events
        (guild_id,actor_id,action,target_key,details_json)
        VALUES (?,?,?,?,?)""",
        (actor.guild_id, actor.user_id, action, key,
         json.dumps(details,ensure_ascii=False)))


def owner_admin(interaction):
    """類型設定限定伺服器擁有者，避免賦予審核員額外管理權。"""
    return bool(interaction.guild and interaction.guild.owner_id == interaction.user.id)


def reviewer(interaction):
    from modules.official_center import has_staff_permission
    return has_staff_permission(interaction, "reviewer")


def require_reviewer(interaction):
    if not reviewer(interaction):
        raise PermissionDenied("僅限正式審核員或伺服器擁有者。")


def check_person(interaction, actor):
    who = Actor.from_interaction(interaction)
    if who.guild_id != actor.guild_id or who.user_id != actor.user_id:
        raise PermissionDenied("這不是你的操作介面。")
    return who


def clean(value, limit=100):
    value = str(value or "").strip()
    if not value or len(value) > limit:
        raise ValueError(f"欄位必須填寫1至{limit}字。")
    return value


def check_url(value):
    value = str(value or "").strip()
    if value and (len(value) > 500 or not re.fullmatch(r"https?://\S+", value)):
        raise ValueError("證明連結必須是 http/https 網址，最多500字。")
    return value


def get_types(guild_id, active_only=False):
    with get_connection() as db:
        rows = db.execute("""SELECT * FROM registration_types
            WHERE guild_id=? AND (?=0 OR enabled=1) ORDER BY id""",
            (str(guild_id),int(active_only))).fetchall()
    return [dict(x) for x in rows]


def get_type(guild_id, type_id, active_only=True):
    with get_connection() as db:
        row = db.execute("""SELECT * FROM registration_types
            WHERE guild_id=? AND id=?""",(str(guild_id),int(type_id))).fetchone()
    if row is None or (active_only and not row["enabled"]):
        raise InvalidState("此登記類型不存在或已停用。")
    return dict(row)


def owned_characters(actor):
    with get_connection() as db:
        rows = db.execute("""SELECT id,name,occupation FROM characters
            WHERE guild_id=? AND owner_id=? AND status='active'
            AND character_type='player' ORDER BY id""",
            (actor.guild_id,actor.user_id)).fetchall()
    return [dict(x) for x in rows]


def check_character(db, actor, character_id):
    row = db.execute("""SELECT id,name FROM characters
        WHERE guild_id=? AND id=? AND owner_id=? AND status='active'
        AND character_type='player'""",
        (actor.guild_id,int(character_id),actor.user_id)).fetchone()
    if row is None:
        raise PermissionDenied("只能替本人已啟用的原創角色登記。")
    return dict(row)


def submit_registration(actor, char_id, type_id, title, values, evidence=""):
    title = clean(title,150)
    evidence = check_url(evidence)
    with transaction() as db:
        check_character(db,actor,char_id)
        typ = db.execute("""SELECT * FROM registration_types
            WHERE guild_id=? AND id=? AND enabled=1""",
            (actor.guild_id,int(type_id))).fetchone()
        if typ is None:
            raise InvalidState("類型已被停用。")
        names = json.loads(typ["fields_json"])
        if set(values) != set(names):
            raise ValueError("登記欄位與目前類型設定不相符，請重新開啟表單。")
        normalized = {x:clean(values[x],500) for x in names}
        cur = db.execute("""INSERT INTO career_registrations
            (guild_id,character_id,type_id,submitted_by,title,fields_json,evidence_url)
            VALUES (?,?,?,?,?,?,?)""",
            (actor.guild_id,int(char_id),int(type_id),actor.user_id,title,
             json.dumps(normalized,ensure_ascii=False),evidence))
        rid = cur.lastrowid
        audit(db,actor,"registration.submit",f"registration:{rid}",{"type_id":type_id})
        return rid


def register_verified_event(guild_id, character_id, type_id, title, values, source_key,
                            *, production_id=None):
    """可信任的活動／作品模組呼叫；同一 source_key 冪等。

    注意：本函式是直接驗證入履歷的內部 API，不能暴露為玩家指令。
    不發放金錢、屬性、粉絲獎勵。以資料庫來源唯一鍵防重複。
    """
    key = clean(source_key,180)
    title = clean(title,150)
    with transaction() as db:
        char = db.execute("""SELECT id FROM characters WHERE guild_id=? AND id=?
            AND status='active'""",(str(guild_id),int(character_id))).fetchone()
        typ = db.execute("""SELECT * FROM registration_types
            WHERE guild_id=? AND id=? AND enabled=1""",
            (str(guild_id),int(type_id))).fetchone()
        if char is None or typ is None:
            raise InvalidState("角色或類型不可用。")
        names = json.loads(typ["fields_json"])
        if set(values) != set(names):
            raise ValueError("自動登記欄位不符合官方類型。")
        normalized={name:clean(values[name],500) for name in names}
        db.execute("""INSERT OR IGNORE INTO career_registrations
            (guild_id,character_id,type_id,submitted_by,title,fields_json,source,
             source_key,production_id,status,reviewer_id,review_reason,reviewed_at)
            VALUES (?,?,?,?,?,?,'automatic',?,?,'approved','system',
                    '由可信任活動系統完成','CURRENT_TIMESTAMP')""".replace("'CURRENT_TIMESTAMP'","CURRENT_TIMESTAMP"),
            (str(guild_id),int(character_id),int(type_id),'system',title,
             json.dumps(normalized,ensure_ascii=False),key,production_id))
        found=db.execute("""SELECT id FROM career_registrations
            WHERE guild_id=? AND source='automatic' AND source_key=?""",
            (str(guild_id),key)).fetchone()
        return int(found["id"])


def get_records(guild_id, *, char_id=None, status=None, owner_id=None, page=0):
    where=["r.guild_id=?"]
    params=[str(guild_id)]
    if char_id is not None:
        where.append("r.character_id=?")
        params.append(int(char_id))
    if status is not None:
        where.append("r.status=?")
        params.append(status)
    if owner_id is not None:
        where.append("c.owner_id=?")
        params.append(str(owner_id))
    clause=" AND ".join(where)
    with get_connection() as db:
        total=db.execute(f"""SELECT COUNT(*) AS n FROM career_registrations r
            JOIN characters c ON c.id=r.character_id AND c.guild_id=r.guild_id
            WHERE {clause}""",params).fetchone()["n"]
        last=max(0,(total-1)//PAGE)
        page=max(0,min(page,last))
        rows=db.execute(f"""SELECT r.*,t.name AS type_name,c.name AS char_name,
            c.owner_id FROM career_registrations r
            JOIN registration_types t ON t.id=r.type_id
            JOIN characters c ON c.id=r.character_id AND c.guild_id=r.guild_id
            WHERE {clause} ORDER BY r.id DESC LIMIT ? OFFSET ?""",
            (*params,PAGE,page*PAGE)).fetchall()
    return [dict(x) for x in rows],total,page,last


def review_registration(actor, number, approved, reason, *, interaction):
    """審核前及交易內再次驗證正式授權。"""
    require_reviewer(interaction)
    reason=str(reason or "").strip()
    if not approved and not reason:
        raise ValueError("退件必須說明原因。")
    if len(reason)>500:
        raise ValueError("審核意見最多500字。")
    with transaction() as db:
        # 伺服器擁有者不用持有正式職位。
        if not owner_admin(interaction):
            grant=db.execute("""SELECT 1 FROM official_staff_grants
                WHERE guild_id=? AND user_id=? AND staff_type='reviewer'""",
                (actor.guild_id,actor.user_id)).fetchone()
            if grant is None:
                raise PermissionDenied("審核資格已撤銷。")
        row=db.execute("""SELECT r.*,c.owner_id FROM career_registrations r
            JOIN characters c ON c.guild_id=r.guild_id AND c.id=r.character_id
            WHERE r.guild_id=? AND r.id=? AND r.status='pending'""",
            (actor.guild_id,int(number))).fetchone()
        if row is None:
            raise InvalidState("此登記不存在或已處理。")
        if str(row["owner_id"])==actor.user_id:
            raise PermissionDenied("不得審核自己角色的履歷登記。")
        new_status="approved" if approved else "rejected"
        changed=db.execute("""UPDATE career_registrations
            SET status=?,reviewer_id=?,review_reason=?,reviewed_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND id=? AND status='pending'""",
            (new_status,actor.user_id,reason,actor.guild_id,int(number)))
        if changed.rowcount != 1:
            raise InvalidState("案件已經由其他人處理。")
        audit(db,actor,"registration.review",f"registration:{number}",
              {"approved":approved,"reason":reason})
    return new_status


def save_type(actor, name, fields, *, type_id=None, enabled=True):
    name=clean(name,80)
    if not 1<=len(fields)<=4 or len(fields)!=len(set(fields)):
        raise ValueError("請設定1～4個不重複的欄位。")
    fields=[clean(x,45) for x in fields]
    with transaction() as db:
        if type_id is None:
            result=db.execute("""INSERT INTO registration_types
                (guild_id,name,fields_json,enabled) VALUES (?,?,?,?)""",
                (actor.guild_id,name,json.dumps(fields,ensure_ascii=False),int(enabled)))
            number=result.lastrowid
        else:
            # 已建立的登記資料保留當時的 fields_json，不回寫歷史。
            changed=db.execute("""UPDATE registration_types SET name=?,fields_json=?,
                enabled=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?""",
                (name,json.dumps(fields,ensure_ascii=False),int(enabled),actor.guild_id,int(type_id)))
            if changed.rowcount!=1:
                raise InvalidState("類型不存在。")
            number=int(type_id)
        audit(db,actor,"registration.type_save",f"registration_type:{number}",
              {"name":name,"fields":fields,"enabled":enabled})
        return number


async def failure(interaction, exc):
    if isinstance(exc, sqlite3.Error):
        LOG.exception("登記資料庫錯誤")
        message="資料庫操作失敗，請查看 Railway 日誌。"
    else:
        message=str(exc)[:1600]
    try:
        if interaction.response.is_done():
            await interaction.followup.send("⚠️ "+message,ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ "+message,ephemeral=True)
    except discord.HTTPException:
        LOG.exception("無法回傳登記錯誤訊息")


class Guard(discord.ui.View):
    def __init__(self,actor):
        super().__init__(timeout=900)
        self.actor=actor

    async def interaction_check(self,interaction):
        try:
            check_person(interaction,self.actor)
            return True
        except (PermissionDenied,InvalidState) as exc:
            await failure(interaction,exc)
            return False


class TypeSelect(discord.ui.Select):
    def __init__(self,types,*,placeholder="選擇登記類型"):
        super().__init__(placeholder=placeholder,options=[
            discord.SelectOption(label=x["name"][:100],value=str(x["id"]))
            for x in types[:25]],row=0)

    async def callback(self,interaction):
        if not await self.view.interaction_check(interaction):
            return
        await self.view.type_chosen(interaction,int(self.values[0]))


class CharacterSelect(discord.ui.Select):
    def __init__(self,characters):
        super().__init__(placeholder="選擇登記角色",options=[
            discord.SelectOption(label=x["name"][:100],description=f"角色 #{x['id']}",value=str(x["id"]))
            for x in characters[:25]],row=0)

    async def callback(self,interaction):
        if not await self.view.interaction_check(interaction):
            return
        await self.view.character_chosen(interaction,int(self.values[0]))


class CharacterPick(Guard):
    def __init__(self,actor,characters,type_id,page=0):
        super().__init__(actor)
        self.characters=characters
        self.type_id=type_id
        self.last=max(0,(len(characters)-1)//PAGE)
        self.page=max(0,min(page,self.last))
        self.add_item(CharacterSelect(characters[self.page*PAGE:(self.page+1)*PAGE]))
        self.prev.disabled=self.page==0
        self.next.disabled=self.page==self.last

    async def character_chosen(self,interaction,cid):
        await interaction.response.send_modal(RegistrationModal(self.actor,cid,self.type_id))

    @discord.ui.button(label="⬅️ 上一頁",style=discord.ButtonStyle.secondary,row=1)
    async def prev(self,interaction,button):
        await interaction.response.edit_message(view=CharacterPick(self.actor,self.characters,self.type_id,self.page-1))

    @discord.ui.button(label="下一頁 ➡️",style=discord.ButtonStyle.secondary,row=1)
    async def next(self,interaction,button):
        await interaction.response.edit_message(view=CharacterPick(self.actor,self.characters,self.type_id,self.page+1))


class RegistrationModal(discord.ui.Modal):
    def __init__(self,actor,cid,type_id):
        typ=get_type(actor.guild_id,type_id)
        super().__init__(title=f"登記｜{typ['name']}"[:45])
        self.actor,self.cid,self.type_id=actor,cid,type_id
        self.names=json.loads(typ["fields_json"])
        self.title_field=discord.ui.TextInput(label="履歷／作品標題",max_length=150)
        self.add_item(self.title_field)
        self.inputs=[]
        for name in self.names:
            field=discord.ui.TextInput(label=name,max_length=500)
            self.add_item(field)
            self.inputs.append(field)
        # Discord modal 最多 5 個 TextInput；證明網址改成補充入口，
        # 登記資料預設待審，不允許沒有來源就自動確認。

    async def on_submit(self,interaction):
        try:
            check_person(interaction,self.actor)
            values={name:str(field.value) for name,field in zip(self.names,self.inputs)}
            number=submit_registration(self.actor,self.cid,self.type_id,
                                       str(self.title_field.value),values)
            await interaction.response.send_message(
                f"✅ 登記 #{number} 已送件，狀態：待官方驗證。\n"
                "這份登記尚不計入公開認證履歷，也不會自動發獎勵。",
                ephemeral=True)
        except (ValueError,InvalidState,PermissionDenied,sqlite3.Error) as exc:
            await failure(interaction,exc)


class TypePick(Guard):
    def __init__(self,actor,types,page=0):
        super().__init__(actor)
        self.types=types
        self.last=max(0,(len(types)-1)//PAGE)
        self.page=max(0,min(page,self.last))
        self.add_item(TypeSelect(types[self.page*PAGE:(self.page+1)*PAGE]))
        self.prev.disabled=self.page==0
        self.next.disabled=self.page==self.last

    async def type_chosen(self,interaction,type_id):
        characters=owned_characters(self.actor)
        if not characters:
            await interaction.response.send_message("沒有已啟用的原創角色。",ephemeral=True)
        else:
            await interaction.response.edit_message(
                content="🎭 請選擇要登記的角色。",
                view=CharacterPick(self.actor,characters,type_id))

    @discord.ui.button(label="⬅️ 上一頁",style=discord.ButtonStyle.secondary,row=1)
    async def prev(self,interaction,button):
        await interaction.response.edit_message(view=TypePick(self.actor,self.types,self.page-1))

    @discord.ui.button(label="下一頁 ➡️",style=discord.ButtonStyle.secondary,row=1)
    async def next(self,interaction,button):
        await interaction.response.edit_message(view=TypePick(self.actor,self.types,self.page+1))


class EvidenceModal(discord.ui.Modal,title="登記｜補充證明網址"):
    number=discord.ui.TextInput(label="登記編號",max_length=12)
    url=discord.ui.TextInput(label="證明網址（https://）",max_length=500)

    async def on_submit(self,interaction):
        try:
            actor=Actor.from_interaction(interaction)
            url=check_url(self.url.value)
            with transaction() as db:
                changed=db.execute("""UPDATE career_registrations SET evidence_url=?
                    WHERE guild_id=? AND id=? AND submitted_by=? AND status='pending'
                    AND source='manual'""",
                    (url,actor.guild_id,int(self.number.value),actor.user_id))
                if changed.rowcount!=1:
                    raise InvalidState("找不到本人尚待審的手動登記。")
                audit(db,actor,"registration.evidence",f"registration:{self.number.value}",{})
            await interaction.response.send_message("✅ 證明網址已保存。",ephemeral=True)
        except (ValueError,InvalidState,PermissionDenied,sqlite3.Error) as exc:
            await failure(interaction,exc)


class TypeEditModal(discord.ui.Modal):
    def __init__(self,actor,type_id=None):
        old=get_type(actor.guild_id,type_id,False) if type_id is not None else None
        super().__init__(title="管理｜修改登記類型" if old else "管理｜新增登記類型")
        self.actor,self.type_id=actor,type_id
        fields=json.loads(old["fields_json"]) if old else []
        self.name_field=discord.ui.TextInput(label="類型名稱",max_length=80,
                                              default=old["name"] if old else None)
        self.fields_field=discord.ui.TextInput(label="欄位名稱（逗號分隔，最多四項）",
                                               style=discord.TextStyle.paragraph,max_length=300,
                                               default="、".join(fields) if fields else "作品名稱、參與身分、年份")
        self.enabled_field=discord.ui.TextInput(label="是否啟用（1啟用／0停用）",max_length=1,
                                                default=str(old["enabled"]) if old else "1")
        for item in (self.name_field,self.fields_field,self.enabled_field):
            self.add_item(item)

    async def on_submit(self,interaction):
        try:
            check_person(interaction,self.actor)
            if not owner_admin(interaction):
                raise PermissionDenied("僅伺服器擁有者可修改類型。")
            raw=str(self.fields_field.value)
            fields=[x.strip() for x in re.split(r"[,，、]",raw) if x.strip()]
            enabled=str(self.enabled_field.value).strip()
            if enabled not in ("0","1"):
                raise ValueError("是否啟用只能填 0 或 1。")
            number=save_type(self.actor,str(self.name_field.value),fields,
                             type_id=self.type_id,enabled=enabled=="1")
            await interaction.response.send_message(f"✅ 登記類型 #{number} 已保存。",ephemeral=True)
        except (ValueError,InvalidState,PermissionDenied,sqlite3.IntegrityError) as exc:
            await failure(interaction,exc)


class AdminTypeSelect(discord.ui.Select):
    def __init__(self,types):
        super().__init__(placeholder="選擇要修改／停用的登記類型",options=[
            discord.SelectOption(label=x["name"][:100],value=str(x["id"]),
                                 description="啟用" if x["enabled"] else "停用") for x in types],row=0)
    async def callback(self,interaction):
        if not await self.view.interaction_check(interaction):
            return
        if not owner_admin(interaction):
            await failure(interaction,PermissionDenied("僅伺服器擁有者可修改類型。"))
            return
        await interaction.response.send_modal(TypeEditModal(self.view.actor,int(self.values[0])))


class TypeAdminView(Guard):
    def __init__(self,actor,types,page=0):
        super().__init__(actor)
        self.types=types
        self.last=max(0,(len(types)-1)//PAGE)
        self.page=max(0,min(page,self.last))
        if types:
            self.add_item(AdminTypeSelect(types[self.page*PAGE:(self.page+1)*PAGE]))
        self.prev.disabled=self.page==0
        self.next.disabled=self.page==self.last

    @discord.ui.button(label="➕ 新增類型",style=discord.ButtonStyle.success,row=1)
    async def add(self,interaction,button):
        if not owner_admin(interaction):
            await failure(interaction,PermissionDenied("僅伺服器擁有者可操作。"))
        else:
            await interaction.response.send_modal(TypeEditModal(self.actor))

    @discord.ui.button(label="⬅️ 上一頁",style=discord.ButtonStyle.secondary,row=2)
    async def prev(self,interaction,button):
        await interaction.response.edit_message(view=TypeAdminView(self.actor,self.types,self.page-1))

    @discord.ui.button(label="下一頁 ➡️",style=discord.ButtonStyle.secondary,row=2)
    async def next(self,interaction,button):
        await interaction.response.edit_message(view=TypeAdminView(self.actor,self.types,self.page+1))


class RecordSelect(discord.ui.Select):
    def __init__(self,rows,mode):
        self.mode=mode
        super().__init__(placeholder="選擇登記紀錄",options=[
            discord.SelectOption(label=f"#{x['id']} {x['title']}"[:100],
                                 description=f"{x['type_name']}｜{x['char_name']}｜{x['status']}"[:100],
                                 value=str(x["id"])) for x in rows],row=0)

    async def callback(self,interaction):
        if not await self.view.interaction_check(interaction):
            return
        number=int(self.values[0])
        if self.mode=="review":
            if not reviewer(interaction):
                await failure(interaction,PermissionDenied("沒有審核權限。"))
                return
            await interaction.response.edit_message(content="請查看案件資料後決定：",
                                                     embed=record_embed(self.view.actor,number),
                                                     view=DecisionView(self.view.actor,number))
        else:
            await interaction.response.send_message(embed=record_embed(self.view.actor,number),ephemeral=True)


def record_embed(actor,number):
    with get_connection() as db:
        row=db.execute("""SELECT r.*,t.name AS type_name,c.name AS char_name,c.owner_id
            FROM career_registrations r
            JOIN registration_types t ON t.id=r.type_id
            JOIN characters c ON c.id=r.character_id AND c.guild_id=r.guild_id
            WHERE r.guild_id=? AND r.id=?""",(actor.guild_id,int(number))).fetchone()
    if not row:
        raise InvalidState("找不到登記紀錄。")
    item=dict(row)
    embed=discord.Embed(title=f"🎬 登記 #{number}｜{item['title'][:160]}",
                        description=f"角色：{item['char_name']}｜類型：{item['type_name']}\n狀態：{item['status']}",
                        color=discord.Color.blurple())
    for label,value in json.loads(item["fields_json"]).items():
        embed.add_field(name=label[:256],value=str(value)[:1000],inline=False)
    if item["evidence_url"]:
        embed.add_field(name="證明網址",value=item["evidence_url"][:1000],inline=False)
    if item["review_reason"]:
        embed.add_field(name="審核意見",value=item["review_reason"][:1000],inline=False)
    embed.set_footer(text=f"來源：{item['source']}｜建立：{item['created_at']}")
    return embed


class RecordList(Guard):
    def __init__(self,actor,mode,page=0):
        super().__init__(actor)
        self.mode=mode
        kw={"owner_id":actor.user_id} if mode=="mine" else {"status":"pending"} if mode=="review" else {"status":"approved"}
        rows,total,self.page,self.last=get_records(actor.guild_id,page=page,**kw)
        self.total=total
        if rows:
            self.add_item(RecordSelect(rows,mode))
        self.prev.disabled=self.page==0
        self.next.disabled=self.page==self.last

    @discord.ui.button(label="⬅️ 上一頁",style=discord.ButtonStyle.secondary,row=1)
    async def prev(self,interaction,button):
        await interaction.response.edit_message(content=f"登記清單 第{self.page}/{self.last+1}頁",
                                                 view=RecordList(self.actor,self.mode,self.page-1))

    @discord.ui.button(label="下一頁 ➡️",style=discord.ButtonStyle.secondary,row=1)
    async def next(self,interaction,button):
        await interaction.response.edit_message(content=f"登記清單 第{self.page+2}/{self.last+1}頁",
                                                 view=RecordList(self.actor,self.mode,self.page+1))


class RejectModal(discord.ui.Modal,title="登記審核｜退件理由"):
    reason=discord.ui.TextInput(label="退件原因",style=discord.TextStyle.paragraph,max_length=500)
    def __init__(self,actor,number):
        super().__init__()
        self.actor,self.number=actor,number
    async def on_submit(self,interaction):
        try:
            check_person(interaction,self.actor)
            result=review_registration(self.actor,self.number,False,str(self.reason.value),interaction=interaction)
            await interaction.response.send_message(f"✅ 登記 #{self.number}：{result}",ephemeral=True)
        except (PermissionDenied,InvalidState,ValueError,sqlite3.Error) as exc:
            await failure(interaction,exc)


class DecisionView(Guard):
    def __init__(self,actor,number):
        super().__init__(actor)
        self.number=number

    @discord.ui.button(label="✅ 驗證通過",style=discord.ButtonStyle.success)
    async def approve(self,interaction,button):
        try:
            result=review_registration(self.actor,self.number,True,"",interaction=interaction)
            await interaction.response.edit_message(content=f"✅ 登記 #{self.number}：{result}",embed=None,view=None)
        except (PermissionDenied,InvalidState,ValueError,sqlite3.Error) as exc:
            await failure(interaction,exc)

    @discord.ui.button(label="❌ 退件",style=discord.ButtonStyle.danger)
    async def reject(self,interaction,button):
        await interaction.response.send_modal(RejectModal(self.actor,self.number))


class Home(Guard):
    @discord.ui.button(label="➕ 手動登記",style=discord.ButtonStyle.primary,row=0)
    async def submit(self,interaction,button):
        types=get_types(self.actor.guild_id,True)
        if not types:
            await failure(interaction,InvalidState("沒有啟用中的登記類型。"))
        else:
            await interaction.response.send_message("選擇登記類型：",view=TypePick(self.actor,types),ephemeral=True)

    @discord.ui.button(label="📁 我的登記",style=discord.ButtonStyle.secondary,row=0)
    async def mine(self,interaction,button):
        await interaction.response.send_message("📁 我的登記紀錄",view=RecordList(self.actor,"mine"),ephemeral=True)

    @discord.ui.button(label="📖 已驗證履歷",style=discord.ButtonStyle.secondary,row=0)
    async def approved(self,interaction,button):
        await interaction.response.send_message("📖 已驗證的履歷紀錄",view=RecordList(self.actor,"approved"),ephemeral=True)

    @discord.ui.button(label="🔗 補充證明",style=discord.ButtonStyle.secondary,row=1)
    async def evidence(self,interaction,button):
        await interaction.response.send_modal(EvidenceModal())

    @discord.ui.button(label="🛡️ 官方待審",style=discord.ButtonStyle.success,row=1)
    async def pending(self,interaction,button):
        if not reviewer(interaction):
            await failure(interaction,PermissionDenied("只有正式審核員可查看。"))
            return
        await interaction.response.send_message("🛡️ 待驗證登記",view=RecordList(self.actor,"review"),ephemeral=True)

    @discord.ui.button(label="⚙️ 編輯登記類型",style=discord.ButtonStyle.secondary,row=2)
    async def type_settings(self,interaction,button):
        if not owner_admin(interaction):
            await failure(interaction,PermissionDenied("僅伺服器擁有者可以設定登記類型。"))
            return
        types=get_types(self.actor.guild_id)
        await interaction.response.send_message("⚙️ 管理登記類型：新增、編輯、停用",view=TypeAdminView(self.actor,types),ephemeral=True)


class Registration(commands.Cog):
    def __init__(self,bot):
        self.bot=bot

    async def cog_load(self):
        initialize()

    @app_commands.command(name="登記",description="作品、演藝活動與代言履歷登記中心")
    @app_commands.guild_only()
    async def registration(self,interaction:discord.Interaction):
        try:
            actor=Actor.from_interaction(interaction)
            ensure_types(actor.guild_id)
            await interaction.response.send_message(
                "🎬 **演藝履歷登記中心**\n\n"
                "可以手動登記電影、劇集、MV、歌曲、代言與活動。\n"
                "手動登記須官方驗證；驗證前不計入正式履歷。\n"
                "類型、欄位及啟用狀態可由伺服器擁有者編輯。\n"
                "此系統不會自動發放片酬、粉絲或屬性獎勵。",
                view=Home(actor),ephemeral=True)
            try:
                from modules.task_event_bridge import record_successful_command_interaction
                record_successful_command_interaction(interaction, '登記')
            except Exception:
                pass  # 教學紀錄不得影響原有功能
        except (PermissionDenied,InvalidState,ValueError,sqlite3.Error) as exc:
            await failure(interaction,exc)


async def setup(bot:commands.Bot):
    await bot.add_cog(Registration(bot))
