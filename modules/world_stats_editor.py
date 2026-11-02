"""世界觀第5部分：數值逐項編輯。僅管理設定，不直接修改既有角色數值。"""
from __future__ import annotations
import re
import discord

SPECIAL = {'影響指數','influence','influence_index'}

def api():
    from modules import content_builder
    return content_builder

def rules(gid):
    section=api().world_get(gid).get('stats',{})
    return section if isinstance(section,dict) else {}

def nice(key,rule):
    return str(rule.get('name',key)) if isinstance(rule,dict) else str(key)

def text(gid):
    entries=rules(gid)
    lines=['📊 **世界觀｜數值設定**','選擇已建立的數值修改，或點「新增數值」。','', '數值設定只影響後續有接入世界觀規則的運算，不會重設角色既有屬性。']
    if not entries:
        lines.append('目前尚無已儲存的自訂數值規則；可用「新增數值」建立。')
    else:
        for key,rule in list(entries.items())[:30]:
            if isinstance(rule,dict):
                lines.append(f"• {nice(key,rule)}｜{'啟用' if rule.get('enabled',True) else '停用'}｜{rule.get('min',0)}～{rule.get('max','未設定')}")
            else:
                lines.append(f'• {key}｜舊格式資料，請逐項編輯')
    lines += ['','🔒 影響指數為管制數值：不得透過隨機、商城購買或付費配點獲得。']
    return '\n'.join(lines)[:1900]

def detail(gid,key):
    rule=rules(gid).get(key,{})
    if not isinstance(rule,dict):rule={}
    note=('\n🔒 此項為管制數值：只能透過正式系統規則／指定獎勵取得。' if key in SPECIAL or rule.get('name')=='影響指數' else '')
    return (f"📊 **{nice(key,rule)}**\n代碼：`{key}`\n狀態：{'啟用' if rule.get('enabled',True) else '停用'}\n"
            f"最低值：{rule.get('min','未設定')}\n最高值：{rule.get('max','未設定')}\n"
            f"初始值：{rule.get('initial','未設定')}\n說明：{rule.get('description') or '尚未填寫'}{note}\n\n"
            '請使用下方按鈕修改，不需要自行輸入 JSON。')[:1900]

class OwnerView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600)
        self.uid,self.gid=i.user.id,i.guild_id
    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.uid or not api().owner(i):
            await i.response.send_message('⛔ 只有開啟面板的群主可以修改世界觀。',ephemeral=True)
            return False
        return True

class StatForm(discord.ui.Modal):
    def __init__(self,gid,key=None):
        self.key=key
        rule=rules(gid).get(key,{}) if key is not None else {}
        if not isinstance(rule,dict):rule={}
        super().__init__(title='修改數值規則' if key else '新增數值規則')
        self.code=None
        if key is None:
            self.code=discord.ui.TextInput(label='數值代碼（唯一識別，不可重複）',placeholder='例如：acting（英數與底線）',max_length=40,min_length=2)
            self.add_item(self.code)
        self.name=discord.ui.TextInput(label='顯示名稱',default=str(rule.get('name',key or '')),placeholder='例如：演技',max_length=50)
        self.minimum=discord.ui.TextInput(label='最低值（整數）',default=str(rule.get('min',0)),placeholder='例如：0',max_length=12)
        self.maximum=discord.ui.TextInput(label='最高值（整數）',default=str(rule.get('max',150)),placeholder='例如：150',max_length=12)
        self.initial=discord.ui.TextInput(label='初始值（整數）',default=str(rule.get('initial',0)),placeholder='例如：0',max_length=12)
        for f in (self.name,self.minimum,self.maximum,self.initial):self.add_item(f)
    async def on_submit(self,i):
        if not api().owner(i):
            return await i.response.send_message('⛔ 僅群主可以儲存設定。',ephemeral=True)
        key=self.key if self.key else str(self.code.value).strip()
        if self.key is None and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{1,39}',key):
            return await i.response.send_message('代碼須由英文字母開頭，只能包含英文、數字、底線（2～40 字）。',ephemeral=True)
        try:
            lo,hi,start=[int(str(x.value).strip()) for x in (self.minimum,self.maximum,self.initial)]
        except ValueError:
            return await i.response.send_message('最低值、最高值、初始值都必須是整數。',ephemeral=True)
        if not (0<=lo<=start<=hi<=1000000000):
            return await i.response.send_message('需符合 0 ≤ 最低值 ≤ 初始值 ≤ 最高值 ≤ 10億。',ephemeral=True)
        name=str(self.name.value).strip()
        if not name:
            return await i.response.send_message('顯示名稱不能為空。',ephemeral=True)
        data=api().world_get(i.guild_id)
        section=data.setdefault('stats',{})
        if not isinstance(section,dict):
            return await i.response.send_message('舊版數值資料格式有誤，請先備份並修復。',ephemeral=True)
        if self.key is None and key in section:
            return await i.response.send_message('此數值代碼已存在，請從數值列表選擇修改。',ephemeral=True)
        old=section.get(key,{})
        if not isinstance(old,dict):old={}
        section[key]={**old,'name':name,'min':lo,'max':hi,'initial':start,'enabled':bool(old.get('enabled',True))}
        data.setdefault('overrides',{})['stats.'+key]=True
        api().world_save(i.guild_id,data)
        await i.response.send_message(f'✅ 已保存「{name}」。角色既有數值不會自動更動；業務計算需接入此設定才會生效。',ephemeral=True)

class DescriptionModal(discord.ui.Modal):
    def __init__(self,gid,key):
        self.key=key
        old=rules(gid).get(key,{})
        if not isinstance(old,dict):old={}
        super().__init__(title='編輯數值說明')
        self.description=discord.ui.TextInput(label='用途及取得方式說明',style=discord.TextStyle.paragraph,required=False,max_length=800,default=str(old.get('description',''))[:800],placeholder='例如：透過指定任務獎勵獲得')
        self.add_item(self.description)
    async def on_submit(self,i):
        if not api().owner(i):return await i.response.send_message('⛔ 僅群主可修改。',ephemeral=True)
        data=api().world_get(i.guild_id)
        rule=data.setdefault('stats',{}).get(self.key)
        if not isinstance(rule,dict):return await i.response.send_message('找不到該項數值。',ephemeral=True)
        rule['description']=str(self.description.value).strip()
        data.setdefault('overrides',{})['stats.'+self.key]=True
        api().world_save(i.guild_id,data)
        await i.response.send_message('✅ 已儲存數值說明。',ephemeral=True)

class StatSelect(discord.ui.Select):
    def __init__(self,gid):
        entries=rules(gid)
        options=[discord.SelectOption(label=nice(k,v)[:100],value=k,description=f'代碼：{k}'[:100]) for k,v in list(entries.items())[:25] if len(k)<=100]
        if not options:
            options=[discord.SelectOption(label='暫無數值（請使用新增數值）',value='__none__')]
        super().__init__(placeholder='選擇數值查看及編輯',options=options,row=0)
    async def callback(self,i):
        key=self.values[0]
        if key=='__none__':return await i.response.send_message('請按「新增數值」。',ephemeral=True)
        await i.response.edit_message(content=detail(i.guild_id,key),view=StatDetailView(i,key))

class StatHomeView(OwnerView):
    def __init__(self,i):
        super().__init__(i)
        self.add_item(StatSelect(self.gid))
    @discord.ui.button(label='➕ 新增數值',style=discord.ButtonStyle.primary,row=1)
    async def create(self,i,b):
        await i.response.send_modal(StatForm(self.gid))
    @discord.ui.button(label='◀️ 返回世界觀',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):
        await i.response.edit_message(content=api().world_summary(i.guild_id),view=api().WorldSettingsView(i))

class StatDetailView(OwnerView):
    def __init__(self,i,key):
        super().__init__(i)
        self.key=key
    @discord.ui.button(label='✏️ 修改名稱與數值範圍',style=discord.ButtonStyle.primary)
    async def edit(self,i,b):await i.response.send_modal(StatForm(i.guild_id,self.key))
    @discord.ui.button(label='📝 修改用途說明',style=discord.ButtonStyle.secondary)
    async def desc(self,i,b):await i.response.send_modal(DescriptionModal(i.guild_id,self.key))
    @discord.ui.button(label='切換啟用／停用',style=discord.ButtonStyle.secondary,row=1)
    async def toggle(self,i,b):
        await i.response.edit_message(content=detail(i.guild_id,self.key)+'\n\n⚠️ 確認修改啟用狀態？',view=StatConfirmView(i,self.key))
    @discord.ui.button(label='◀️ 返回數值列表',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):
        await i.response.edit_message(content=text(i.guild_id),view=StatHomeView(i))

class StatConfirmView(OwnerView):
    def __init__(self,i,key):
        super().__init__(i)
        self.key=key
    @discord.ui.button(label='確認切換',style=discord.ButtonStyle.danger)
    async def confirm(self,i,b):
        data=api().world_get(i.guild_id)
        rule=data.setdefault('stats',{}).get(self.key)
        if not isinstance(rule,dict):return await i.response.send_message('找不到數值規則。',ephemeral=True)
        rule['enabled']=not bool(rule.get('enabled',True))
        data.setdefault('overrides',{})['stats.'+self.key]=True
        api().world_save(i.guild_id,data)
        await i.response.edit_message(content=detail(i.guild_id,self.key)+'\n✅ 已更新狀態。',view=StatDetailView(i,self.key))
    @discord.ui.button(label='取消',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,b):
        await i.response.edit_message(content=detail(i.guild_id,self.key),view=StatDetailView(i,self.key))
