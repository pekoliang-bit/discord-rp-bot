"""世界觀第九部分：自訂內容分類清單（不更動固定十大管理分類）。"""
from __future__ import annotations
import re
import discord

DEFAULT = {
    'story': ('📖 攻略劇情','供劇情內容分類使用'),
    'event': ('🎉 活動','供活動內容分類使用'),
    'item': ('🏬 商品','供商品內容分類使用'),
    'world': ('🌍 世界資料','供世界背景及資料索引使用'),
}

def core():
    from modules import content_builder
    return content_builder

def records(gid):
    data=core().world_get(gid).get('categories',{})
    return data if isinstance(data,dict) else {}

def merged(gid):
    data={k:{'name':v[0],'description':v[1],'enabled':True} for k,v in DEFAULT.items()}
    for key,val in records(gid).items():
        if isinstance(val,dict):data[key]={**data.get(key,{}),**val}
    return data

def summary(gid):
    rows=['🗂️ **世界觀｜分類管理**','這裡管理世界觀內容的自訂分類，不會刪除十大管理選單。','']
    for key,item in list(merged(gid).items())[:25]:
        rows.append(f"{item.get('name',key)}（{key}）｜{'啟用' if item.get('enabled',True) else '停用'}")
    rows.append('\n分類變更只保存設定；其他內容清單若要依此分類，仍須另行串接。')
    return '\n'.join(rows)[:1900]

def detail(gid,key):
    item=merged(gid).get(key,{})
    return (f"🗂️ **{item.get('name',key)}**\n代碼：`{key}`\n"
            f"用途說明：{item.get('description') or '未設定'}\n"
            f"狀態：{'啟用' if item.get('enabled',True) else '停用'}\n"
            f"內建分類：{'是（可編輯及停用，不可移除）' if key in DEFAULT else '否（可刪除設定）'}")

class Guard(discord.ui.View):
    def __init__(self,i):super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id
    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.uid or not core().owner(i):
            await i.response.send_message('⛔ 僅限開啟面板的群主使用。',ephemeral=True);return False
        return True

class EditModal(discord.ui.Modal):
    def __init__(self,gid,key=None):
        self.key=key; obj=merged(gid).get(key,{}) if key else {}
        super().__init__(title='修改分類' if key else '新增分類')
        if not key:
            self.code=discord.ui.TextInput(label='分類代碼（英文及底線）',placeholder='例如: education',max_length=32)
            self.add_item(self.code)
        self.name_input=discord.ui.TextInput(label='分類顯示名稱',default=str(obj.get('name',''))[:80],placeholder='例如：🎓 校園',max_length=80)
        self.description=discord.ui.TextInput(label='用途說明',default=str(obj.get('description',''))[:600],placeholder='例如：校園劇情及活動分類',required=False,max_length=600,style=discord.TextStyle.paragraph)
        self.add_item(self.name_input);self.add_item(self.description)
    async def on_submit(self,i):
        if not core().owner(i):return await i.response.send_message('⛔ 僅群主可儲存。',ephemeral=True)
        key=self.key or self.code.value.strip().lower()
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,31}',key):return await i.response.send_message('分類代碼需 2～32 字，英文開頭，只能含小寫英數及底線。',ephemeral=True)
        if not self.name_input.value.strip():return await i.response.send_message('分類名稱不可空白。',ephemeral=True)
        data=core().world_get(i.guild_id);items=data.setdefault('categories',{})
        if not self.key and key in merged(i.guild_id):return await i.response.send_message('此分類代碼已存在，請改用編輯功能。',ephemeral=True)
        if not self.key and len(merged(i.guild_id))>=25:return await i.response.send_message('目前最多可設 25 個分類。',ephemeral=True)
        entry=items.get(key,{})
        if not isinstance(entry,dict):entry={}
        items[key]={**entry,'name':self.name_input.value.strip(),'description':self.description.value.strip(),'enabled':entry.get('enabled',True)}
        data.setdefault('overrides',{})['categories.'+key]=True;core().world_save(i.guild_id,data)
        await i.response.send_message('✅ 已儲存分類。\n'+detail(i.guild_id,key),ephemeral=True)

class CategorySelect(discord.ui.Select):
    def __init__(self,gid):
        items=list(merged(gid).items())[:25]
        super().__init__(placeholder='選擇分類',options=[discord.SelectOption(label=str(x.get('name',k))[:100],value=k,description=k) for k,x in items])
    async def callback(self,i):
        key=self.values[0];await i.response.edit_message(content=detail(i.guild_id,key),view=CategoryDetailView(i,key))

class CategoryHomeView(Guard):
    def __init__(self,i):super().__init__(i);self.add_item(CategorySelect(i.guild_id))
    @discord.ui.button(label='➕ 新增分類',style=discord.ButtonStyle.primary,row=1)
    async def add(self,i,b):await i.response.send_modal(EditModal(i.guild_id))
    @discord.ui.button(label='◀️ 返回世界觀',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content=core().world_summary(i.guild_id),view=core().WorldSettingsView(i))

class CategoryDetailView(Guard):
    def __init__(self,i,key):super().__init__(i);self.key=key
    @discord.ui.button(label='✏️ 修改分類',style=discord.ButtonStyle.primary)
    async def edit(self,i,b):await i.response.send_modal(EditModal(i.guild_id,self.key))
    @discord.ui.button(label='啟用／停用',style=discord.ButtonStyle.secondary)
    async def toggle(self,i,b):
        value=not merged(i.guild_id)[self.key].get('enabled',True)
        await i.response.edit_message(content=f'確定要將分類 {self.key} 設為{"啟用" if value else "停用"}？',view=CategoryConfirmView(i,self.key,'toggle',value))
    @discord.ui.button(label='🗑️ 刪除自訂分類',style=discord.ButtonStyle.danger,row=1)
    async def delete(self,i,b):
        if self.key in DEFAULT:return await i.response.send_message('內建分類不能刪除，可停用。',ephemeral=True)
        await i.response.edit_message(content=f'⚠️ 確定刪除分類 `{self.key}` 的世界觀設定？既有內容不會被刪除。',view=CategoryConfirmView(i,self.key,'delete',False))
    @discord.ui.button(label='返回分類清單',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content=summary(i.guild_id),view=CategoryHomeView(i))

class CategoryConfirmView(Guard):
    def __init__(self,i,key,action,value):super().__init__(i);self.key=key;self.action=action;self.value=value
    @discord.ui.button(label='確認變更',style=discord.ButtonStyle.danger)
    async def confirm(self,i,b):
        data=core().world_get(i.guild_id);items=data.setdefault('categories',{})
        if self.action=='delete':
            if self.key in DEFAULT:return await i.response.send_message('內建分類無法刪除。',ephemeral=True)
            items.pop(self.key,None)
            data.setdefault('overrides',{}).pop('categories.'+self.key,None)
        else:
            entry=items.get(self.key,{})
            if not isinstance(entry,dict):entry={}
            items[self.key]={**entry,'enabled':self.value};data.setdefault('overrides',{})['categories.'+self.key]=True
            content='✅ 已儲存分類狀態。'
        core().world_save(i.guild_id,data)
        await i.response.edit_message(content=content if self.action!='delete' else '✅ 已刪除自訂分類設定。\n'+summary(i.guild_id),view=CategoryHomeView(i) if self.action=='delete' else CategoryDetailView(i,self.key))
    @discord.ui.button(label='取消',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,b):await i.response.edit_message(content=detail(i.guild_id,self.key),view=CategoryDetailView(i,self.key))
