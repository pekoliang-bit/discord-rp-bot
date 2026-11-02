"""唯一正式 /任務 指令；新手進度及角色任務看板。"""
from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from core.database import get_connection
from modules.task_repository import initialize
from modules.task_newbie import character_owner, progress, bot_thread_id, record_success
from modules.task_defaults import seed_newbie
from modules.task_service import board,accept,report

KINDS={'daily':'☀️ 日常任務','screen':'🎬 影視通告','variety':'🎤 綜藝通告','endorsement':'📣 代言通告','limited':'⏱️ 限時通告','special':'✨ 特殊通告'}

def owned(gid,uid):
    with get_connection() as db:
        return [dict(r) for r in db.execute("SELECT id,name FROM characters WHERE guild_id=? AND owner_id=? AND status IN ('active','approved') AND character_type='player' ORDER BY id",(str(gid),str(uid)))]

class TaskPick(discord.ui.Select):
    def __init__(self,characters):
        super().__init__(placeholder='選擇角色',options=[discord.SelectOption(label=str(c['name'])[:80],value=str(c['id']),description=f"角色 #{c['id']}") for c in characters[:25]])
    async def callback(self,i):await show_character(i,int(self.values[0]))

class TaskView(discord.ui.View):
    def __init__(self,uid,characters):
        super().__init__(timeout=300);self.uid=uid;self.add_item(TaskPick(characters))
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('請自行使用 /任務',ephemeral=True);return False
        return True

class KindPick(discord.ui.Select):
    def __init__(self,gid,cid,choices):
        super().__init__(placeholder='選擇通告分類',options=[discord.SelectOption(label=KINDS[x],value=x) for x in choices]);self.gid=gid;self.cid=cid
    async def callback(self,i):
        try: rows=board(self.gid,self.cid,i.user.id,self.values[0])
        except Exception as e:return await i.response.send_message(str(e)[:180],ephemeral=True)
        if not rows:return await i.response.send_message('目前沒有符合資格且已發布的通告。',ephemeral=True)
        await i.response.send_message('\n'.join(f"#{r['id']} {discord.utils.escape_markdown(r['title'])}" for r in rows[:20])[:1800],ephemeral=True)

class KindsView(discord.ui.View):
    def __init__(self,uid,gid,cid,kinds):
        super().__init__(timeout=300);self.uid=uid;self.add_item(KindPick(gid,cid,kinds))
    async def interaction_check(self,i):
        if i.user.id!=self.uid:await i.response.send_message('這不是你的面板',ephemeral=True);return False
        return True

async def show_character(i,cid):
    if not character_owner(i.guild_id,cid,i.user.id):return await i.response.send_message('無權使用此角色',ephemeral=True)
    p=progress(i.guild_id,cid)
    if not p['graduated']:
        thread=bot_thread_id(i.guild_id,cid)
        if thread and str(i.channel_id)==thread:record_success(i.guild_id,cid,i.user.id,i.channel_id,'任務');p=progress(i.guild_id,cid)
        desc=f"🎓 新手探索 {p['done']}/{p['total']}\n未完成："+'、'.join('/'+n for n in p['remaining'])
        if thread:desc+=f'\n請在 <#{thread}> 操作新手指令。'
        else:desc+='\n尚未綁定角色的機器人使用帖子，請聯絡管理員。'
        return await i.response.send_message(desc[:1900],ephemeral=True)
    available=[]
    for kind in KINDS:
        try:
            if kind in ('limited','special') and not board(i.guild_id,cid,i.user.id,kind):continue
            available.append(kind)
        except Exception:continue
    await i.response.send_message(f'📋 角色 #{cid} 的任務中心',view=KindsView(i.user.id,i.guild_id,cid,available),ephemeral=True)

class TasksCenter(commands.Cog):
    def __init__(self,bot):self.bot=bot
    @app_commands.command(name='任務',description='開啟任務中心；指定角色及任務編號可回報')
    @app_commands.guild_only()
    @app_commands.describe(角色編號='需要操作的角色編號',任務編號='已接取、需要回報的通告編號')
    async def tasks(self,i:discord.Interaction,角色編號:int|None=None,任務編號:int|None=None):
        initialize();seed_newbie(i.guild_id)
        if 角色編號 is None:
            cs=owned(i.guild_id,i.user.id)
            if not cs:return await i.response.send_message('請先啟用角色',ephemeral=True)
            return await i.response.send_message('選擇要查看任務的角色：',view=TaskView(i.user.id,cs),ephemeral=True)
        if 任務編號 is not None:
            try:report(i.guild_id,角色編號,i.user.id,任務編號,i.channel_id)
            except ValueError as e:return await i.response.send_message(str(e),ephemeral=True)
            return await i.response.send_message('已回報，等待任務審核；尚未發放獎勵。',ephemeral=True)
        return await show_character(i,角色編號)

async def setup(bot):await bot.add_cog(TasksCenter(bot))
