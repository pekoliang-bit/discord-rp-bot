"""世界觀第八部分：細項規則。欄位化設定；不直接改動其他模組。"""
from __future__ import annotations
import discord

RULES = {
    'character': ('🎭 角色規則', '角色建立、資料修改與退場的世界觀附加條件', '例如：本世界可使用哪些職業／身分'),
    'progress': ('📈 成長規則', '角色成長與養成時的附加條件', '例如：每週成長上限及活動限制'),
    'economy': ('💰 經濟規則', '貨幣、交易及資產的世界觀補充限制', '例如：是否限制跨地區交易'),
    'social': ('🤝 社交規則', '社交、CP 與玩家關係的背景限制', '例如：公開關係與身分限制'),
    'story': ('📖 劇情規則', '劇情接取與解鎖的世界觀條件', '例如：特定地區才能接取的劇情'),
    'region': ('🗺️ 地區規則', '地區與建築的附加世界規則', '例如：需要通行證才能進入的區域'),
    'activity': ('🎉 活動規則', '活動參與、時段與特殊要求', '例如：活動開放條件'),
    'custom': ('⚙️ 其他細項', '其他不屬於上述類別的特殊世界規則', '例如：季節或特殊事件的額外限制'),
}

def core():
    from modules import content_builder
    return content_builder

def section(gid):
    obj=core().world_get(gid).get('detail_rules', {})
    return obj if isinstance(obj, dict) else {}

def summary(gid):
    data=section(gid)
    lines=['⚙️ **世界觀｜細項規則**','請先選擇規則類別，查看用途及填寫範例。','']
    for k,(title,*_) in RULES.items():
        entry=data.get(k,{})
        if not isinstance(entry,dict):entry={}
        lines.append(f"{title}｜{'啟用' if entry.get('enabled',True) else '停用'}｜{'已填寫' if entry.get('description') else '未填寫'}")
    lines.append('\n⚠️ 本頁保存世界觀規則；實際遊戲限制仍需對應業務系統讀取。')
    return '\n'.join(lines)[:1900]

def detail(gid,k):
    title,about,example=RULES[k]
    item=section(gid).get(k,{})
    if not isinstance(item,dict): item={}
    return (f'**{title}**\n用途：{about}\n範例：{example}\n'
            f"狀態：{'啟用' if item.get('enabled',True) else '停用'}\n"
            f"適用對象：{item.get('audience') or '全體（預設說明）'}\n"
            f"適用條件：{item.get('conditions') or '尚未設定'}\n"
            f"詳細規則：{item.get('description') or '尚未設定'}\n"
            f"備註：{item.get('notes') or '無'}")[:1900]

class Guard(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id
    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.uid or not core().owner(i):
            await i.response.send_message('⛔ 僅限開啟此面板的群主操作。',ephemeral=True);return False
        return True

class EditModal(discord.ui.Modal):
    def __init__(self,gid,key):
        self.key=key; super().__init__(title='編輯'+RULES[key][0][:35])
        obj=section(gid).get(key,{})
        if not isinstance(obj,dict): obj={}
        self.inputs={}
        for name,label,hint,length in [
            ('audience','適用對象','例如：全部角色／新創角色',120),
            ('conditions','適用條件','例如：達到 LV3 後',350),
            ('description','詳細規則內容','請描述此規則怎麼運作',950),
            ('notes','其他說明／例外','選填，填寫特殊例外',350)]:
            field=discord.ui.TextInput(label=label,placeholder=hint,default=str(obj.get(name,'') or '')[:length],max_length=length,required=False,style=discord.TextStyle.paragraph if length>120 else discord.TextStyle.short)
            self.inputs[name]=field;self.add_item(field)
    async def on_submit(self,i):
        if not core().owner(i):return await i.response.send_message('⛔ 僅群主可保存。',ephemeral=True)
        data=core().world_get(i.guild_id);rules=data.setdefault('detail_rules',{})
        entry=rules.get(self.key,{})
        if not isinstance(entry,dict):entry={}
        rules[self.key]={**entry,**{k:v.value.strip() for k,v in self.inputs.items()}}
        data.setdefault('overrides',{})['detail_rules.'+self.key]=True
        core().world_save(i.guild_id,data)
        await i.response.send_message('✅ 設定已保存。\n'+detail(i.guild_id,self.key),ephemeral=True)

class RuleSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='選擇細項規則分類',options=[discord.SelectOption(label=v[0],value=k,description=v[1][:95]) for k,v in RULES.items()])
    async def callback(self,i):
        key=self.values[0];await i.response.edit_message(content=detail(i.guild_id,key),view=RuleDetailView(i,key))

class RuleHomeView(Guard):
    def __init__(self,i):super().__init__(i);self.add_item(RuleSelect())
    @discord.ui.button(label='返回世界觀',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content=core().world_summary(i.guild_id),view=core().WorldSettingsView(i))

class RuleDetailView(Guard):
    def __init__(self,i,key):super().__init__(i);self.key=key
    @discord.ui.button(label='✏️ 修改各項規則',style=discord.ButtonStyle.primary)
    async def edit(self,i,b):await i.response.send_modal(EditModal(i.guild_id,self.key))
    @discord.ui.button(label='啟用／停用',style=discord.ButtonStyle.secondary)
    async def toggle(self,i,b):
        data=core().world_get(i.guild_id);rules=data.setdefault('detail_rules',{})
        entry=rules.get(self.key,{})
        if not isinstance(entry,dict):entry={}
        self.value=not entry.get('enabled',True)
        await i.response.edit_message(content=f"確認將 {RULES[self.key][0]} 設為{'啟用' if self.value else '停用'}？",view=RuleConfirmView(i,self.key,self.value))
    @discord.ui.button(label='返回分類',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content=summary(i.guild_id),view=RuleHomeView(i))

class RuleConfirmView(Guard):
    def __init__(self,i,key,value):super().__init__(i);self.key=key;self.value=value
    @discord.ui.button(label='確認保存',style=discord.ButtonStyle.danger)
    async def confirm(self,i,b):
        data=core().world_get(i.guild_id);rules=data.setdefault('detail_rules',{});entry=rules.get(self.key,{})
        if not isinstance(entry,dict):entry={}
        rules[self.key]={**entry,'enabled':self.value};data.setdefault('overrides',{})['detail_rules.'+self.key]=True
        core().world_save(i.guild_id,data)
        await i.response.edit_message(content=detail(i.guild_id,self.key),view=RuleDetailView(i,self.key))
    @discord.ui.button(label='取消',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,b):await i.response.edit_message(content=detail(i.guild_id,self.key),view=RuleDetailView(i,self.key))
