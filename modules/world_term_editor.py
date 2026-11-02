"""世界觀第4部分：用詞自訂（顯示名稱，不修改業務識別碼）。"""
from __future__ import annotations
import discord

FIELDS = (
    ('company','🏢 公司','玩家成立及經營的公司','公司'),
    ('group','🏛️ 集團','由多間公司組成的集團','集團'),
    ('brand','🏷️ 品牌','公司旗下的品牌','品牌'),
    ('faction','⚔️ 陣營','玩家所加入的陣營','陣營'),
    ('position','💼 職位','公司內部的職位名稱','職位'),
    ('currency','💰 貨幣','遊戲內通用貨幣的顯示稱呼','金幣'),
)
FIELD_MAP = {x[0]:x for x in FIELDS}

def api():
    from modules import content_builder
    return content_builder

def current(gid, key):
    return str(api().world_get(gid).get('terms',{}).get(key, FIELD_MAP[key][3]))

def summary(gid):
    lines=['✏️ **世界觀｜用詞自訂**','請選擇一個稱呼查看說明和修改；不需要填寫設定鍵名。','']
    lines.extend(f'{name}：**{current(gid,key)}**' for key,name,_,_ in FIELDS)
    lines += ['','⚠️ 修改的是顯示稱呼，不會更改資料庫內部代碼、公司資產或既有合約。其他系統需讀取 `world_terms` 才會同步顯示。']
    return '\n'.join(lines)

def detail(gid,key):
    _,name,purpose,example=FIELD_MAP[key]
    return f'✏️ **{name}**\n用途：{purpose}\n目前稱呼：**{current(gid,key)}**\n範例：{example}\n\n點「修改稱呼」即可填寫新名稱；不會修改玩家資料。'

class TermModal(discord.ui.Modal):
    def __init__(self,gid,key):
        self.key=key
        _,name,purpose,example=FIELD_MAP[key]
        super().__init__(title=f'修改{name}'[:45])
        self.value=discord.ui.TextInput(label='新的顯示稱呼',default=current(gid,key),placeholder=f'例如：{example}',min_length=1,max_length=30)
        self.add_item(self.value)
    async def on_submit(self,i):
        if not api().owner(i):
            return await i.response.send_message('⛔ 僅群主可以修改世界觀。',ephemeral=True)
        value=str(self.value.value).strip()
        if not value or '\n' in value or '@everyone' in value or '@here' in value:
            return await i.response.send_message('請輸入 1～30 字、不可包含換行或全體提及的稱呼。',ephemeral=True)
        data=api().world_get(i.guild_id)
        data.setdefault('terms',{})[self.key]=value
        data.setdefault('overrides',{})['terms.'+self.key]=True
        api().world_save(i.guild_id,data)
        await i.response.send_message(f'✅ 已保存「{FIELD_MAP[self.key][1]}」的新稱呼：{value}\n其他功能是否同步顯示，取決於對應模組是否讀取世界觀設定。',ephemeral=True)

class TermSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='選擇要修改的稱呼',options=[discord.SelectOption(label=name,value=key,description=purpose[:90]) for key,name,purpose,_ in FIELDS])
    async def callback(self,i):
        key=self.values[0]
        await i.response.edit_message(content=detail(i.guild_id,key),view=TermDetailView(i,key))

class OwnerView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600)
        self.uid,self.gid=i.user.id,i.guild_id
    async def interaction_check(self,i):
        if i.guild_id != self.gid or i.user.id != self.uid or not api().owner(i):
            await i.response.send_message('⛔ 只有開啟面板的群主可操作。',ephemeral=True)
            return False
        return True

class TermHomeView(OwnerView):
    def __init__(self,i):
        super().__init__(i)
        self.add_item(TermSelect())
    @discord.ui.button(label='◀️ 返回世界觀',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):
        await i.response.edit_message(content=api().world_summary(i.guild_id),view=api().WorldSettingsView(i))

class TermDetailView(OwnerView):
    def __init__(self,i,key):
        super().__init__(i)
        self.key=key
    @discord.ui.button(label='✏️ 修改稱呼',style=discord.ButtonStyle.primary)
    async def edit(self,i,b):
        await i.response.send_modal(TermModal(i.guild_id,self.key))
    @discord.ui.button(label='↩️ 恢復預設稱呼',style=discord.ButtonStyle.secondary)
    async def reset(self,i,b):
        await i.response.edit_message(content=detail(i.guild_id,self.key)+'\n\n⚠️ 確認恢復為預設名稱？',view=TermResetView(i,self.key))
    @discord.ui.button(label='◀️ 返回稱呼列表',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):
        await i.response.edit_message(content=summary(i.guild_id),view=TermHomeView(i))

class TermResetView(OwnerView):
    def __init__(self,i,key):
        super().__init__(i)
        self.key=key
    @discord.ui.button(label='確認恢復預設',style=discord.ButtonStyle.danger)
    async def confirm(self,i,b):
        data=api().world_get(i.guild_id)
        data.setdefault('terms',{})[self.key]=FIELD_MAP[self.key][3]
        data.setdefault('overrides',{})['terms.'+self.key]=True
        api().world_save(i.guild_id,data)
        await i.response.edit_message(content=detail(i.guild_id,self.key)+'\n✅ 已恢復預設稱呼。',view=TermDetailView(i,self.key))
    @discord.ui.button(label='取消',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,b):
        await i.response.edit_message(content=detail(i.guild_id,self.key),view=TermDetailView(i,self.key))
