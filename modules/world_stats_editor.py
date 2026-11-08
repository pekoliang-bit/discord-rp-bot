"""世界觀數值設定：13 預設、可新增/停用、獨立管制影響指數。

不直接更改已啟用角色的數值；可由業務層呼叫 stat_policy/validate_grant。
內部識別碼固定不變，顯示名稱可調。禁止透過改名或修改世界觀設定
繞過影響指數的不可購買/不可隨機/不可付費配點規則。
"""
from __future__ import annotations
import re
import discord

BASE = (
    "流量熱度", "商業價值", "路人緣", "公關", "影響指數",
    "容貌", "才智", "歌唱", "舞蹈", "演繹", "主持", "綜藝", "個人才藝",
)
INFLUENCE = "影響指數"
ACTION = "action_points"
# 永不重複使用的內部代碼，與數值顯示名稱不同。
DEFAULTS = {key: {"name": key, "enabled": True, "sources": "依既有正式系統規則", "limits": "依既有正式系統規則", "per_gain": 1, "per_loss": 1} for key in BASE}
DEFAULTS[INFLUENCE] = {"name": INFLUENCE, "enabled": True,
    "sources": "系統正式規則、核准稱號、指定任務、指定活動", "per_gain": 1, "per_loss": 1,
    "limits": "不得購買、隨機掉落、付費配點；只許正式授權獎勵"}
DEFAULTS[ACTION] = {"name": "行動值", "enabled": True, "sources": "系統恢復、核准活動、正式道具獎勵", "limits": "僅依正式行動值規則增減", "per_gain": 1, "per_loss": 1}

DISALLOWED_INFLUENCE_SOURCES = {"shop", "purchase", "paid_allocation", "random", "roulette", "gacha", "lottery", "money", "paid", "cash", "auction"}
ALLOWED_INFLUENCE_SOURCES = {"system_rule", "achievement", "approved_event", "approved_task", "approved_title", "admin_adjustment"}

def api():
    from modules import content_builder
    return content_builder

def can_edit(interaction):
    if api().owner(interaction):
        return True
    from modules.world_permission_editor import has_world_permission
    return has_world_permission(interaction, "stats_edit")

def saved(gid):
    raw = api().world_get(gid).get("stats", {})
    return raw if isinstance(raw, dict) else {}

def rules(gid):
    out={k:dict(v) for k,v in DEFAULTS.items()}
    for key,value in saved(gid).items():
        if isinstance(value, dict):
            out[key]={**out.get(key, {"name":key, "enabled":True, "sources":"", "limits":"", "per_gain":1, "per_loss":1}),**value}
    # Lock safety policy on every read, including previously saved invalid overrides.
    out[INFLUENCE]["enabled"] = True
    out[INFLUENCE]["protected"] = True
    out[INFLUENCE]["non_purchasable"] = True
    out[INFLUENCE]["non_random"] = True
    return out

def stat_policy(gid, key):
    item=rules(gid).get(key)
    if item is None:raise KeyError(f"未知數值：{key}")
    return dict(item)

def validate_grant(gid, key, source: str, amount: int):
    """業務發獎可呼叫本函式檢查；不取代既有專屬數值校驗。"""
    rule=stat_policy(gid,key)
    if not rule.get("enabled",True):raise PermissionError("數值未啟用")
    if not isinstance(amount,int) or amount <= 0:raise ValueError("增減必須為正整數")
    if key == INFLUENCE:
        if source not in ALLOWED_INFLUENCE_SOURCES:
            raise PermissionError("影響指數不可透過購買、隨機、商城、付費配點等取得")
        # amount must be validated against the specific approved source separately.
    return True

def name(gid,key):
    return str(stat_policy(gid,key).get('name',key))

def text(gid):
    data=rules(gid)
    lines=["📊 **世界觀｜數值設定**", "內建十三項數值 + 行動值（獨立資源）；可調整顯示名稱與取得方式。", "✅ 啟用／⛔ 停用；停用不刪除角色舊資料。", ""]
    for key,value in data.items():
        label="🔒" if key==INFLUENCE else ("✅" if value.get("enabled",True) else "⛔")
        lines.append(f"{label} {value.get('name',key)}" + ("（行動值）" if key==ACTION else ""))
    lines.append("\n影響指數固定不可買賣、抽取或付費配點，僅可改名稱和授權來源的獎勵量。")
    return "\n".join(lines)[:1900]

def detail(gid,key):
    item=stat_policy(gid,key)
    lock=("\n🔒 固定安全規則：不可花錢購買、不能隨機獲得、不能付費配點；不能停用或更改內部代碼。" if key==INFLUENCE else "")
    return (f"📊 **{item.get('name',key)}**\n內部代碼：`{key}`\n狀態：{'啟用' if item.get('enabled',True) else '停用'}\n"
            f"授權獲取來源：{item.get('sources') or '尚未設定'}\n每次增加：{item.get('per_gain',1)}\n每次減少：{item.get('per_loss',1)}\n"
            f"其他限制：{item.get('limits') or '未設定'}{lock}\n\n本頁保存未來規則，不會重設現有角色數值。")[:1900]

class Guard(discord.ui.View):
    def __init__(self,i):super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id
    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.uid or not can_edit(i):
            await i.response.send_message("⛔ 僅群主能修改本頁設定。",ephemeral=True);return False
        return True

class CreateStatModal(discord.ui.Modal,title="新增自訂數值"):
    code=discord.ui.TextInput(label="固定識別碼（英文、數字、底線）",min_length=2,max_length=40)
    display=discord.ui.TextInput(label="顯示名稱",max_length=50)
    sources=discord.ui.TextInput(label="獲得方式",required=False,max_length=300,style=discord.TextStyle.paragraph)
    limits=discord.ui.TextInput(label="取得限制",required=False,max_length=300,style=discord.TextStyle.paragraph)
    async def on_submit(self,i):
        if not can_edit(i):return await i.response.send_message("⛔ 無權操作。",ephemeral=True)
        key=self.code.value.strip().lower();label=self.display.value.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,39}",key) or not label:
            return await i.response.send_message("識別碼格式不正確或名稱為空。",ephemeral=True)
        if key in {"influence", "influence_index", "action_points"} or label == INFLUENCE:
            return await i.response.send_message("⛔ 保留識別碼／影響指數不可新增為一般數值。",ephemeral=True)
        if key in rules(i.guild_id):return await i.response.send_message("該代碼已存在，請編輯原有數值。",ephemeral=True)
        data=api().world_get(i.guild_id)
        if len(rules(i.guild_id))>=60:return await i.response.send_message("最多 60 項數值。",ephemeral=True)
        data.setdefault("stats",{})[key]={"name":label,"sources":self.sources.value.strip(),"limits":self.limits.value.strip(),"per_gain":1,"per_loss":1,"enabled":True}
        data.setdefault("overrides",{})['stats.'+key]=True
        api().world_save(i.guild_id,data)
        await i.response.send_message(f"✅ 已新增 {label}。其他模組接入後才會實際計算。",ephemeral=True)

class StatForm(discord.ui.Modal):
    def __init__(self,gid,key):
        super().__init__(title="獨立管理影響指數" if key==INFLUENCE else "編輯數值規則")
        self.key=key;item=stat_policy(gid,key)
        self.name=discord.ui.TextInput(label="顯示名稱",max_length=50,default=str(item.get('name',key)))
        self.sources=discord.ui.TextInput(label="核准獲取來源（僅描述；不得解鎖禁令）",max_length=400,required=False,style=discord.TextStyle.paragraph,default=str(item.get('sources',''))[:400])
        self.gain=discord.ui.TextInput(label="單次增加量（正整數）",max_length=10,default=str(item.get('per_gain',1)))
        self.loss=discord.ui.TextInput(label="單次減少量（正整數）",max_length=10,default=str(item.get('per_loss',1)))
        for f in (self.name,self.sources,self.gain,self.loss):self.add_item(f)
        if key!=INFLUENCE:
            self.limits=discord.ui.TextInput(label="自訂限制／例外",max_length=500,required=False,style=discord.TextStyle.paragraph,default=str(item.get('limits',''))[:500]);self.add_item(self.limits)
    async def on_submit(self,i):
        if not can_edit(i):return await i.response.send_message("⛔ 無權修改。",ephemeral=True)
        label=str(self.name.value).strip()
        if self.key != INFLUENCE and label == INFLUENCE:
            return await i.response.send_message("⛔ 影響指數名稱保留給管制數值。",ephemeral=True)
        try:gain=int(self.gain.value);loss=int(self.loss.value)
        except ValueError:return await i.response.send_message("增減量請填整數。",ephemeral=True)
        if not label or not 1<=gain<=1_000_000 or not 1<=loss<=1_000_000:return await i.response.send_message("名稱不可空白，單次增減必須介於 1～100萬。",ephemeral=True)
        sources=str(self.sources.value).strip()
        if self.key==INFLUENCE:
            # Text is a description only; cannot grant unauthorized sources.
            forbidden=("購買", "付費", "商城", "隨機", "抽獎", "抽抽樂", "轉帳", "金幣", "拍賣", "販售", "兌換")
            if any(word in sources for word in forbidden):return await i.response.send_message("⛔ 影響指數不得新增購買、抽獎、隨機或付費取得來源。",ephemeral=True)
        data=api().world_get(i.guild_id); old=data.setdefault('stats',{}).get(self.key,{})
        if not isinstance(old,dict):old={}
        changed={**old,"name":label,"sources":sources,"per_gain":gain,"per_loss":loss}
        if self.key!=INFLUENCE: changed["limits"]=str(self.limits.value).strip()
        else:
            changed.pop("min",None);changed.pop("max",None);changed.pop("initial",None)
            changed.update({"enabled":True,"protected":True,"non_purchasable":True,"non_random":True,"limits":DEFAULTS[INFLUENCE]['limits']})
        data['stats'][self.key]=changed
        data.setdefault('overrides',{})['stats.'+self.key]=True
        api().world_save(i.guild_id,data)
        await i.response.send_message("✅ 已儲存。既有角色數值未改動；安全限制仍然有效。",ephemeral=True)

class StatSelect(discord.ui.Select):
    def __init__(self,gid):
        options=[discord.SelectOption(label=str(item.get('name',key))[:100],value=key,description=f"代碼：{key}"[:100]) for key,item in list(rules(gid).items())[:25]]
        super().__init__(placeholder="選擇數值（前25項）",options=options,row=0)
    async def callback(self,i):
        key=self.values[0];await i.response.edit_message(content=detail(i.guild_id,key),view=StatDetailView(i,key))

class StatHomeView(Guard):
    def __init__(self,i):super().__init__(i);self.add_item(StatSelect(self.gid))
    @discord.ui.button(label="➕ 新增自訂數值",style=discord.ButtonStyle.primary,row=1)
    async def create(self,i,b):await i.response.send_modal(CreateStatModal())
    @discord.ui.button(label="◀️ 返回世界觀",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content=api().world_summary(i.guild_id),view=api().WorldSettingsView(i))

class StatDetailView(Guard):
    def __init__(self,i,key):super().__init__(i);self.key=key
    @discord.ui.button(label="✏️ 名稱／來源／增減量",style=discord.ButtonStyle.primary)
    async def edit(self,i,b):await i.response.send_modal(StatForm(i.guild_id,self.key))
    @discord.ui.button(label="啟用／停用",style=discord.ButtonStyle.secondary)
    async def toggle(self,i,b):
        if self.key==INFLUENCE:return await i.response.send_message("🔒 影響指數是管制數值，不可停用或刪除；可修改顯示名稱和核准獎勵。",ephemeral=True)
        await i.response.edit_message(content="確認切換？不會刪除現有角色數值。",view=StatConfirmView(i,self.key))
    @discord.ui.button(label="返回數值列表",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content=text(i.guild_id),view=StatHomeView(i))

class StatConfirmView(Guard):
    def __init__(self,i,key):super().__init__(i);self.key=key
    @discord.ui.button(label="確認切換",style=discord.ButtonStyle.danger)
    async def confirm(self,i,b):
        if self.key==INFLUENCE:return await i.response.send_message("不可停用管制數值。",ephemeral=True)
        data=api().world_get(i.guild_id);item=data.setdefault('stats',{}).get(self.key,{})
        if not isinstance(item,dict):item={}
        item={**item,'enabled':not rules(i.guild_id)[self.key].get('enabled',True)}
        data['stats'][self.key]=item;data.setdefault('overrides',{})['stats.'+self.key]=True
        api().world_save(i.guild_id,data)
        await i.response.edit_message(content=detail(i.guild_id,self.key),view=StatDetailView(i,self.key))
    @discord.ui.button(label="取消",style=discord.ButtonStyle.secondary)
    async def cancel(self,i,b):await i.response.edit_message(content=detail(i.guild_id,self.key),view=StatDetailView(i,self.key))
