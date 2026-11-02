"""世界觀管理第六部分：LV1～LV20 地位詳細編輯與評分權重。"""
from __future__ import annotations
import discord


def core():
    from modules import content_builder
    return content_builder


def values(gid):
    v=core().world_get(gid).get('status_levels', {})
    return v if isinstance(v,dict) else {}


def status_summary(gid):
    data=values(gid)
    lines=['🎖️ **世界觀｜地位設定**','請從選單選擇 LV1～LV20 修改。','每個等級可設定名稱、狀態、熱度與商業價值門檻；其他門檻由既有地位規則決定。','']
    for n in range(1,21):
        entry=data.get(str(n),{})
        if not isinstance(entry,dict):entry={}
        lines.append(f"LV{n}｜{entry.get('name') or '尚未命名'}｜{'啟用' if entry.get('enabled',True) else '停用'}")
    return '\n'.join(lines)[:1900]


def level_text(gid,n):
    x=values(gid).get(str(n),{})
    if not isinstance(x,dict):x={}
    return (f"🎖️ **LV{n}｜{x.get('name') or '尚未命名'}**\n"
            f"狀態：{'啟用' if x.get('enabled',True) else '停用'}\n"
            f"熱度門檻：{x.get('heat_required','尚未設定')}\n"
            f"商業價值門檻：{x.get('commercial_required','尚未設定')}\n"
            f"等級說明：{x.get('description') or '尚未填寫'}\n\n"
            '⚠️ 這裡儲存世界觀設定，不會直接升降任何角色。晉升仍按既有逐級規則處理。')


class Guard(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600)
        self.uid=i.user.id
        self.gid=i.guild_id
    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.uid or not core().owner(i):
            await i.response.send_message('⛔ 只有開啟面板的群主可以修改。',ephemeral=True)
            return False
        return True


class LevelModal(discord.ui.Modal):
    def __init__(self,gid,n):
        self.n=n
        data=values(gid).get(str(n),{})
        if not isinstance(data,dict):data={}
        super().__init__(title=f'編輯地位 LV{n}')
        self.label=discord.ui.TextInput(label='地位名稱',default=str(data.get('name') or ''),placeholder='例如：新人演員',max_length=50,required=True)
        self.heat=discord.ui.TextInput(label='最低熱度（非負整數）',default=str(data.get('heat_required',0)),placeholder='例如：10000',max_length=14)
        self.commercial=discord.ui.TextInput(label='最低商業價值（非負整數）',default=str(data.get('commercial_required',0)),placeholder='例如：5000',max_length=14)
        self.desc=discord.ui.TextInput(label='等級用途／待遇說明（選填）',default=str(data.get('description',''))[:900],style=discord.TextStyle.paragraph,required=False,max_length=900)
        for field in (self.label,self.heat,self.commercial,self.desc):self.add_item(field)
    async def on_submit(self,i):
        if not core().owner(i):return await i.response.send_message('⛔ 僅群主可儲存。',ephemeral=True)
        try:heat=int(self.heat.value);commercial=int(self.commercial.value)
        except ValueError:return await i.response.send_message('門檻必須是非負整數。',ephemeral=True)
        if min(heat,commercial)<0:return await i.response.send_message('門檻不得小於 0。',ephemeral=True)
        data=core().world_get(i.guild_id)
        slot=data.setdefault('status_levels',{})
        previous=slot.get(str(self.n),{})
        if not isinstance(previous,dict):previous={}
        slot[str(self.n)]={**previous,'name':self.label.value.strip(),'heat_required':heat,'commercial_required':commercial,'description':self.desc.value.strip(),'enabled':previous.get('enabled',True)}
        data.setdefault('overrides',{})[f'status_levels.{self.n}']=True
        core().world_save(i.guild_id,data)
        await i.response.send_message(f'✅ LV{self.n} 已儲存。\n'+level_text(i.guild_id,self.n),ephemeral=True)


class LevelSelect(discord.ui.Select):
    def __init__(self,gid):
        data=values(gid)
        opts=[]
        for n in range(1,21):
            entry=data.get(str(n),{})
            if not isinstance(entry,dict):entry={}
            opts.append(discord.SelectOption(label=f'LV{n}｜{str(entry.get("name") or "尚未命名")[:75]}',value=str(n)))
        super().__init__(placeholder='選擇要編輯的地位等級',options=opts)
    async def callback(self,i):
        n=int(self.values[0])
        await i.response.edit_message(content=level_text(i.guild_id,n),view=LevelView(i,n))


class StatusHomeView(Guard):
    def __init__(self,i):
        super().__init__(i)
        self.add_item(LevelSelect(i.guild_id))
    @discord.ui.button(label='LV1～18 評分權重',style=discord.ButtonStyle.secondary,row=1)
    async def a(self,i,b):await i.response.send_modal(core().WorldStatusWeightsModal(i.guild_id,'lv1_18'))
    @discord.ui.button(label='LV19～20 評分權重',style=discord.ButtonStyle.secondary,row=1)
    async def b(self,i,b):await i.response.send_modal(core().WorldStatusWeightsModal(i.guild_id,'lv19_20'))
    @discord.ui.button(label='返回世界觀',style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,b):await i.response.edit_message(content=core().world_summary(i.guild_id),view=core().WorldSettingsView(i))


class LevelView(Guard):
    def __init__(self,i,n):
        super().__init__(i)
        self.n=n
    @discord.ui.button(label='編輯名稱與門檻',style=discord.ButtonStyle.primary)
    async def edit(self,i,b):await i.response.send_modal(LevelModal(i.guild_id,self.n))
    @discord.ui.button(label='啟用／停用此等級',style=discord.ButtonStyle.secondary)
    async def toggle(self,i,b):
        data=core().world_get(i.guild_id)
        section=data.setdefault('status_levels',{})
        entry=section.get(str(self.n),{})
        if not isinstance(entry,dict):entry={}
        entry['enabled']=not entry.get('enabled',True)
        section[str(self.n)]=entry
        data.setdefault('overrides',{})[f'status_levels.{self.n}']=True
        core().world_save(i.guild_id,data)
        await i.response.edit_message(content=level_text(i.guild_id,self.n),view=LevelView(i,self.n))
    @discord.ui.button(label='返回等級清單',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content=status_summary(i.guild_id),view=StatusHomeView(i))
