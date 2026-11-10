"""商業銀行帳戶申請互動流程；不註冊額外斜線指令。

公司／集團：只有 rp_business_entities.owner_id 能送件。
品牌：只有品牌 owner_id 能送件。
個人／頻道商店：只有 personal_shops 所屬角色的 owner_id 能送件。
每次申請至少一名被核驗的授權角色，權限採白名單，核准前不生效。
"""
from __future__ import annotations

import json
import sqlite3
import discord
from core.database import transaction, get_connection

PERMISSIONS = {
    "view": "查看帳戶及交易紀錄",
    "deposit": "存款",
    "withdraw": "提款",
    "transfer": "轉帳",
    "statements": "下載／查看對帳紀錄",
}


def resolve_target(db, gid, kind, raw, user_id):
    raw = str(raw).strip()
    if not raw:
        raise ValueError("請輸入公司、集團、品牌或店鋪編碼。")
    if kind == "company_bank":
        from core.business_public_ids import resolve_business
        bid = resolve_business(db, gid, raw, allowed=("company", "group"))
        row = db.execute("SELECT kind,owner_id,status FROM rp_business_entities WHERE guild_id=? AND business_id=?", (str(gid),bid)).fetchone()
        if not row or row["status"] != "active" or row["kind"] not in ("company","group"):
            raise ValueError("公司／集團不存在或尚未核准。")
        if str(row["owner_id"]) != str(user_id):
            raise PermissionError("只有公司或集團真正持有人可申請開戶。")
        return bid, row["kind"]

    from core.business_public_ids import resolve_business
    if raw.upper().startswith("B-") or raw.startswith("brand:"):
        bid = resolve_business(db,gid,raw.removeprefix("brand:"),allowed=("brand",))
        row = db.execute("SELECT owner_id,status,kind FROM rp_business_entities WHERE guild_id=? AND business_id=?", (str(gid),bid)).fetchone()
        if not row or row["kind"]!="brand" or row["status"]!="active":
            raise ValueError("找不到已核准的品牌。")
        if str(row["owner_id"])!=str(user_id):
            raise PermissionError("只有品牌真正持有人可申請開戶。")
        return "brand:" + bid, "brand"
    from core.business_public_ids import resolve_shop
    shop_id=resolve_shop(db,gid,raw.removeprefix("shop:"))
    row=db.execute("SELECT s.id,s.character_id,s.status,c.owner_id,c.status AS character_status "
      "FROM personal_shops s JOIN characters c ON c.guild_id=s.guild_id AND c.id=s.character_id "
      "WHERE s.guild_id=? AND s.id=?",(str(gid),shop_id)).fetchone()
    if not row or row["status"]!="open" or row["character_status"]!="active":
        raise ValueError("店鋪不存在或未開業。")
    if str(row["owner_id"])!=str(user_id):
        raise PermissionError("只有店鋪持有人可申請開戶。")
    return str(shop_id),"shop"


def validate_operator(db,gid,raw):
    value=str(raw).strip()
    row=None
    if value.isdecimal():
        row=db.execute("SELECT id,name,status FROM characters WHERE guild_id=? AND id=?",(str(gid),int(value))).fetchone()
    else:
        row=db.execute("SELECT id,name,status FROM characters WHERE guild_id=? AND public_code=?",(str(gid),value)).fetchone()
    if row is None or row["status"]!="active":
        raise ValueError("找不到已啟用的授權角色，請確認角色編號。")
    return int(row["id"]),str(row["name"])


def store_bank_application(gid,user_id,kind,raw,purpose,operators):
    """原子送件，只有申請者持有的實體可使用；審核時須再次驗證。"""
    from modules.application_extras import init_db
    init_db()
    if not purpose.strip():
        raise ValueError("請填寫帳戶用途。")
    if not operators:
        raise ValueError("至少指定一名授權操作人。")
    with transaction() as db:
        title, entity_kind=resolve_target(db,gid,kind,raw,user_id)
        normalized=[]
        used=set()
        for operator in operators:
            cid,name=validate_operator(db,gid,operator["character_id"])
            perms=sorted(set(operator["permissions"]))
            if not perms or not set(perms)<=set(PERMISSIONS):
                raise ValueError("每位授權操作人至少選擇一項有效權限。")
            if cid in used:
                raise ValueError("授權操作人重複，請使用編輯而非再次新增。")
            used.add(cid)
            normalized.append({"character_id":cid,"name":name,"permissions":perms})
        old=db.execute("SELECT 1 FROM rp_general_applications WHERE guild_id=? AND kind=? AND title=? AND status IN ('pending','approved') LIMIT 1",(str(gid),kind,title)).fetchone()
        if old:
            raise ValueError("該主體已存在待審或已核准的銀行開戶申請。")
        details=json.dumps({"bank_v2":True,"subject_kind":entity_kind,"purpose":purpose.strip(),"operators":normalized},ensure_ascii=False)
        cur=db.execute("INSERT INTO rp_general_applications(guild_id,user_id,character_id,kind,title,details,reference) VALUES (?,?,?,?,?,?,?)",(str(gid),str(user_id),None,kind,title,details,""))
        return int(cur.lastrowid)


class BankBaseModal(discord.ui.Modal,title="填寫銀行帳號資料"):
    code=discord.ui.TextInput(label="公司／集團／品牌／店鋪編碼",max_length=100)
    purpose=discord.ui.TextInput(label="帳戶用途",style=discord.TextStyle.short,max_length=200)

    def __init__(self,parent):
        super().__init__(timeout=300)
        self.parent=parent
        if parent.kind=="shop_bank":
            self.code.label="店鋪編碼／品牌編碼"

    async def on_submit(self,i):
        if not await self.parent.interaction_check(i):return
        try:
            supplied=self.code.value.strip()
            if self.parent.kind=="shop_bank" and self.parent.subject_type=="brand":
                supplied="brand:"+supplied.removeprefix("brand:")
            with get_connection() as db:
                title,subject_kind=resolve_target(db,self.parent.gid,self.parent.kind,supplied,self.parent.uid)
        except (ValueError,PermissionError,sqlite3.Error) as exc:
            return await i.response.send_message(f"⚠️ {exc}",ephemeral=True)
        self.parent.subject=supplied
        self.parent.purpose=self.purpose.value.strip()
        await i.response.send_message(self.parent.summary(),view=self.parent,ephemeral=True)


class BankOperatorModal(discord.ui.Modal,title="新增授權操作人"):
    character=discord.ui.TextInput(label="授權操作人角色編號",max_length=100,placeholder="公開編號，未開群可用既有角色 ID")
    def __init__(self,parent):
        super().__init__(timeout=300)
        self.parent=parent
    async def on_submit(self,i):
        if not await self.parent.interaction_check(i):return
        try:
            with get_connection() as db:
                cid,name=validate_operator(db,self.parent.gid,self.character.value)
            if cid in {x["character_id"] for x in self.parent.operators}:
                raise ValueError("角色已在授權名單中。")
        except (ValueError,sqlite3.Error) as exc:
            return await i.response.send_message(f"⚠️ {exc}",ephemeral=True)
        await i.response.send_message(
            f"請勾選 **{name}** 可以操作的項目（可複選）。",
            view=BankPermissionView(self.parent,cid,name),ephemeral=True)


class BankPermissionSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="選擇這位角色的授權權限（可複選）",min_values=1,max_values=len(PERMISSIONS),options=[discord.SelectOption(label=v,value=k) for k,v in PERMISSIONS.items()])
    async def callback(self,i):
        v=self.view
        if not await v.parent.interaction_check(i):return
        if not v.saved:
            v.parent.operators.append({"character_id":v.character_id,"name":v.character_name,"permissions":list(self.values)})
            v.saved=True
        else:
            for item in v.parent.operators:
                if item["character_id"]==v.character_id:
                    item["permissions"]=list(self.values)
        await i.response.edit_message(content="✅ 已儲存授權。返回原本的銀行申請訊息，按＋可繼續新增操作人。",view=None)

class BankPermissionView(discord.ui.View):
    def __init__(self,parent,cid,name):
        super().__init__(timeout=300)
        self.parent=parent
        self.character_id=cid
        self.character_name=name
        self.saved=False
        self.add_item(BankPermissionSelect())

class BankRequestView(discord.ui.View):
    def __init__(self,gid,uid,kind):
        super().__init__(timeout=900)
        self.gid=str(gid)
        self.uid=str(uid)
        self.kind=kind
        self.subject_type="shop"
        self.subject=""
        self.purpose=""
        self.operators=[]
        self.sent=False
        if kind=="company_bank":
            self.remove_item(self.subject_choice)

    async def interaction_check(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            await i.response.send_message("這不是你的銀行申請。",ephemeral=True)
            return False
        return True

    def summary(self):
        rows=[f"{x['name']}（角色 #{x['character_id']}）："+"、".join(PERMISSIONS[p] for p in x['permissions']) for x in self.operators]
        return f"🏦 **銀行申請草稿**\\n主體編碼：{self.subject or '尚未填寫'}\\n帳戶用途：{self.purpose or '尚未填寫'}\\n授權操作人：{len(rows)} 位\\n"+("\\n".join(rows) if rows else "請至少新增一位授權操作人。")

    @discord.ui.select(placeholder="選擇開戶對象類型",row=2,options=[
        discord.SelectOption(label="店鋪／商城店鋪／頻道店鋪",value="shop"),
        discord.SelectOption(label="品牌",value="brand"),
    ])
    async def subject_choice(self,i,select):
        if self.kind!="shop_bank":
            return await i.response.send_message("公司／集團申請不需要選擇這個項目。",ephemeral=True)
        self.subject_type=select.values[0]
        self.subject=""
        await i.response.send_message("已選擇："+("品牌" if self.subject_type=="brand" else "店鋪")+"。請點『填寫主體與帳戶用途』。",ephemeral=True)

    @discord.ui.button(label="填寫主體與帳戶用途",style=discord.ButtonStyle.primary,row=0)
    async def base(self,i,button):
        await i.response.send_modal(BankBaseModal(self))

    @discord.ui.button(label="＋ 新增授權操作人",style=discord.ButtonStyle.secondary,row=0)
    async def add(self,i,button):
        if self.sent:
            return await i.response.send_message("申請已送出。",ephemeral=True)
        await i.response.send_modal(BankOperatorModal(self))

    @discord.ui.button(label="查看目前授權名單",style=discord.ButtonStyle.secondary,row=1)
    async def inspect(self,i,button):
        await i.response.send_message(self.summary(),ephemeral=True)

    @discord.ui.button(label="確認送出申請",style=discord.ButtonStyle.success,row=1)
    async def submit(self,i,button):
        if self.sent:
            return await i.response.send_message("申請已送出，請勿重複提交。",ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            import asyncio
            rid=await asyncio.to_thread(store_bank_application,self.gid,self.uid,self.kind,self.subject,self.purpose,self.operators)
        except (ValueError,PermissionError,sqlite3.Error) as exc:
            return await i.followup.send(f"⚠️ 申請失敗：{exc}",ephemeral=True)
        self.sent=True
        for child in self.children:child.disabled=True
        await i.followup.send(f"✅ 銀行帳號申請 #{rid} 已提交，待審核後才會開立帳戶。",ephemeral=True)
