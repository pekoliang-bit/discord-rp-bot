
"""官方路人管理：照片素材、姓名職業庫、批次建立、排行榜總開關。"""
from __future__ import annotations
import re
import discord
from modules import extra_character_store as store

def owner(i): return bool(i.guild and i.user.id==i.guild.owner_id)

class PoolModal(discord.ui.Modal):
    def __init__(self, kind):
        super().__init__(title={'photos':'🖼️ 批次新增照片','names':'🌏 新增姓名庫','jobs':'🎭 新增職業庫'}[kind]); self.kind=kind
        if kind=='names':
            self.nationality=discord.ui.TextInput(label='國籍（例：日本）',max_length=50)
            self.kind_field=discord.ui.TextInput(label='類型 surname 姓氏／given 名字',max_length=12,placeholder='surname')
            self.add_item(self.nationality);self.add_item(self.kind_field)
        self.values=discord.ui.TextInput(label='每行一項（可貼上多行）',style=discord.TextStyle.paragraph,max_length=4000,placeholder='每行一筆照片網址、名字或職業')
        self.add_item(self.values)
    async def on_submit(self,i):
        if not owner(i): return await i.response.send_message('只有群主能操作。',ephemeral=True)
        values=[v.strip() for v in self.values.value.splitlines() if v.strip()]
        try:
            if self.kind=='photos': n=store.add_photos(i.guild_id,values)
            elif self.kind=='jobs': n=store.add_jobs(i.guild_id,values)
            else: n=store.add_names(i.guild_id,self.nationality.value.strip(),self.kind_field.value.strip().lower(),values)
            d=store.inventory(i.guild_id)
            await i.response.send_message(f'✅ 新增 {n} 筆。照片庫剩餘 **{d["photos"]}** 張。'+(' ⚠️ 只剩五張或更少！' if d['photos']<=5 else ''),ephemeral=True)
        except Exception as e: await i.response.send_message(f'⚠️ 匯入失敗：{e}',ephemeral=True)

class BatchModal(discord.ui.Modal,title='🎲 批次建立路人角色'):
    count=discord.ui.TextInput(label='建立人數（1～100）',placeholder='10',max_length=3)
    nation=discord.ui.TextInput(label='指定國籍（空白＝隨機）',required=False,max_length=50)
    level=discord.ui.TextInput(label='指定咖位等級（空白＝隨機）',required=False,max_length=2)
    async def on_submit(self,i):
        if not owner(i): return await i.response.send_message('僅群主能操作。',ephemeral=True)
        try:
            created=store.generate(i.guild_id,i.guild.owner_id,int(self.count.value),nationality=self.nation.value.strip() or None,level=int(self.level.value) if self.level.value.strip() else None)
            remaining=store.inventory(i.guild_id)['photos']
            msg=f'✅ 已建立 **{len(created)}** 位路人角色。\n'+ '\n'.join(f'#{cid} {name}｜{job}｜LV{lv} {title}' for cid,name,job,lv,title in created[:15])
            if len(created)>15:msg+=f'\n……其餘 {len(created)-15} 位可至 `/查看 → 路人角色` 查看。'
            msg+=f'\n🖼️ 未使用照片：{remaining} 張。'
            if remaining<=5:msg+=' ⚠️ 照片庫只剩五張或更少，請補充。'
            await i.response.send_message(msg[:1950],ephemeral=True)
        except (ValueError,TypeError) as e: await i.response.send_message(f'⚠️ 無法建立：{e}',ephemeral=True)

class ExtraCharacterView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600);self.user_id=i.user.id;self.guild_id=i.guild_id
    async def interaction_check(self,i):
        if i.user.id!=self.user_id or i.guild_id!=self.guild_id or not owner(i):
            await i.response.send_message('⛔ 僅群主可使用此面板。',ephemeral=True);return False
        return True
    @discord.ui.button(label='🖼️ 匯入照片',style=discord.ButtonStyle.primary,row=0)
    async def photos(self,i,b):await i.response.send_modal(PoolModal('photos'))
    @discord.ui.button(label='🌏 姓氏／名字庫',style=discord.ButtonStyle.secondary,row=0)
    async def names(self,i,b):await i.response.send_modal(PoolModal('names'))
    @discord.ui.button(label='🎭 職業庫',style=discord.ButtonStyle.secondary,row=0)
    async def jobs(self,i,b):await i.response.send_modal(PoolModal('jobs'))
    @discord.ui.button(label='🎲 批次生成',style=discord.ButtonStyle.success,row=1)
    async def batch(self,i,b):await i.response.send_modal(BatchModal())
    @discord.ui.button(label='📊 庫存統計',style=discord.ButtonStyle.secondary,row=1)
    async def stats(self,i,b):
        d=store.inventory(i.guild_id);cfg=store.get_config(i.guild_id)
        await i.response.send_message(f'🚶 **路人資料庫**\n未使用照片：{d["photos"]} 張'+(' ⚠️ 庫存僅剩 5 張或更少' if d['photos']<=5 else '')+f'\n國籍數：{d["nations"]}，姓名詞條：{d["names"]}，職業數：{d["jobs"]}\n已建立：{d["existing"]} 人\n排行榜：{"允許" if cfg["allow_ranking"] else "不允許"}',ephemeral=True)
    @discord.ui.button(label='🏆 切換排行榜資格',style=discord.ButtonStyle.secondary,row=1)
    async def rank(self,i,b):
        cfg=store.get_config(i.guild_id);enabled=not bool(cfg['allow_ranking']);store.set_ranking(i.guild_id,enabled)
        await i.response.send_message(f'✅ 路人角現在{"允許" if enabled else "不允許"}參與排行榜。這不會刪除現有角色或數值。',ephemeral=True)
    @discord.ui.button(label='◀️ 返回內容建立',style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,b):
        from modules.content_builder import ContentView
        await i.response.edit_message(content='✍️ **內容建立中心**',view=ContentView(i))
