"""世界觀權限設定：可視化定義角色、審核與管理職責。

此檔只儲存政策；實際權限由各業務模組查詢 has_world_permission 才生效。
群主永遠保留最高權限，不能以自訂規則停用稽核與審核迴避。
"""
from __future__ import annotations
import discord

POLICIES = {
    "world_edit": ("🌍 世界觀設定", "僅群主", "誰能設定世界背景、功能和規則"),
    "stats_edit": ("📊 數值規則管理", "僅群主", "誰能修改數值名稱、來源、數量與限制"),
    "content_manage": ("🗂️ 內容與分類管理", "群主與授權管理員", "誰能新增分類和管理世界內容"),
    "events_manage": ("🎉 活動管理", "群主與授權管理員", "誰能管理活動與受理申請"),
    "shop_manage": ("🏬 商城管理", "群主與授權管理員", "誰能審核或管理官方商品"),
    "character_review": ("✅ 創角審核", "既有正式審核規則", "一二審及自審隔離規則不可繞過"),
    "discipline": ("⚖️ 紀律與舉報", "既有正式紀律規則", "舉報保密、申訴與群主複審不可繞過"),
}
LOCKED = {"character_review", "discipline"}
OPTIONS = ["僅群主", "群主與授權管理員", "不額外授權"]

def core():
    from modules import content_builder
    return content_builder

def policies(gid):
    raw = core().world_get(gid).get("permissions", {})
    return raw if isinstance(raw, dict) else {}

def has_world_permission(interaction, key):
    if interaction.guild is None or key not in POLICIES:
        return False
    if interaction.user.id == interaction.guild.owner_id:
        return True
    if key in LOCKED:
        return False  # 必須使用原有正式審核/紀律權限服務
    cfg = policies(interaction.guild_id).get(key, {})
    if not isinstance(cfg, dict): cfg = {}
    mode = cfg.get('mode', POLICIES[key][1])
    return mode == "群主與授權管理員" and any(
        role.id in set(cfg.get("role_ids", [])) for role in getattr(interaction.user,"roles",[])
    )

def summary(gid):
    rows = ["🔐 **世界觀｜權限設定**", "選擇職責查看或修改管理身分組，不影響群主權限。"]
    for k,(label,default,_) in POLICIES.items():
        setting=policies(gid).get(k,{})
        if not isinstance(setting,dict):setting={}
        rows.append(f"{label}｜{setting.get('mode',default)}")
    rows.append("\n⚠️ 此處為權限政策；未接入的模組仍沿用既有權限。審核與紀律安全規則不可覆蓋。")
    return "\n".join(rows)[:1900]

def detail(gid,key):
    label,default,note=POLICIES[key]
    obj=policies(gid).get(key,{})
    if not isinstance(obj,dict):obj={}
    ids=obj.get("role_ids",[])
    roles=", ".join(f"<@&{rid}>" for rid in ids) or "尚未指定"
    return f"🔐 **{label}**\n{note}\n模式：{obj.get('mode',default)}\n授權身分組：{roles}\n" + ("⚠️ 此項為不可覆寫的正式安全規則。" if key in LOCKED else "此設定需由對應服務檢查才會生效。")

class Guard(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id
    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.uid or not core().owner(i):
            await i.response.send_message("⛔ 僅群主能管理權限。",ephemeral=True);return False
        return True

class PermissionSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="選擇權限種類",options=[discord.SelectOption(label=v[0],value=k,description=v[2][:90]) for k,v in POLICIES.items()])
    async def callback(self,i):
        key=self.values[0]
        await i.response.edit_message(content=detail(i.guild_id,key),view=PermissionDetailView(i,key))

class PermissionHomeView(Guard):
    def __init__(self,i):super().__init__(i);self.add_item(PermissionSelect())
    @discord.ui.button(label="◀️ 返回世界觀",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content=core().world_summary(i.guild_id),view=core().WorldSettingsView(i))

class EditPermissionsModal(discord.ui.Modal,title="修改管理權限"):
    def __init__(self,key,gid):
        super().__init__();self.key=key;cfg=policies(gid).get(key,{})
        if not isinstance(cfg,dict):cfg={}
        self.mode=discord.ui.TextInput(label="模式：僅群主／群主與授權管理員／不額外授權",default=cfg.get('mode',POLICIES[key][1]),max_length=30)
        self.roles=discord.ui.TextInput(label="管理身分組 ID（英文逗號分隔）",default=','.join(map(str,cfg.get('role_ids',[]))),required=False,max_length=350)
        self.add_item(self.mode);self.add_item(self.roles)
    async def on_submit(self,i):
        if not core().owner(i):return await i.response.send_message("⛔ 無權修改。",ephemeral=True)
        if self.key in LOCKED:return await i.response.send_message("⛔ 此項只能使用既有正式審核與紀律權限。",ephemeral=True)
        mode=str(self.mode.value).strip()
        if mode not in OPTIONS:return await i.response.send_message("模式不正確，請依提示填寫。",ephemeral=True)
        try:
            ids=[int(x.strip()) for x in self.roles.value.split(',') if x.strip()]
        except ValueError:return await i.response.send_message("身分組必須填 ID。",ephemeral=True)
        if len(ids)>15 or any(x<=0 or i.guild.get_role(x) is None for x in ids):return await i.response.send_message("請填寫本伺服器存在的身分組 ID，最多 15 個。",ephemeral=True)
        data=core().world_get(i.guild_id)
        data.setdefault('permissions',{})[self.key]={"mode":mode,"role_ids":ids}
        data.setdefault('overrides',{})['permissions.'+self.key]=True
        core().world_save(i.guild_id,data)
        await i.response.send_message("✅ 權限政策已儲存。注意：尚未接線的模組仍使用原權限。",ephemeral=True)

class PermissionDetailView(Guard):
    def __init__(self,i,key):super().__init__(i);self.key=key
    @discord.ui.button(label="✏️ 修改權限與身分組",style=discord.ButtonStyle.primary)
    async def edit(self,i,b):
        if self.key in LOCKED:return await i.response.send_message("⛔ 固定安全規則不可覆寫。",ephemeral=True)
        await i.response.send_modal(EditPermissionsModal(self.key,i.guild_id))
    @discord.ui.button(label="返回權限列表",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content=summary(i.guild_id),view=PermissionHomeView(i))
