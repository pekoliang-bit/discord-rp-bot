"""玩家唯一 /任務 入口。所有結果採私人回覆，不重複註冊指令。"""
from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from core.database import get_connection
from modules.task_repository import initialize
from modules.task_newbie import character_owner, progress, bot_thread_id, record_success
from modules.task_defaults import seed_newbie
from modules.task_screen_seed import seed_screen_tasks
from modules.task_variety_seed import seed_variety_tasks
from modules.task_commercial_seed import seed_commercial_tasks
from modules.task_service import board, accept, report

KINDS={'daily':'☀️ 日常任務','screen':'🎬 影視通告','variety':'🎤 綜藝通告',
       'endorsement':'📣 代言通告','limited':'⏱️ 限時通告','special':'✨ 特殊通告'}

def owned(gid,uid):
    with get_connection() as db:
        return [dict(r) for r in db.execute("SELECT id,name FROM characters WHERE guild_id=? AND owner_id=? AND status IN ('active','approved') AND character_type='player' ORDER BY id",(str(gid),str(uid)))]

class TaskPick(discord.ui.Select):
    def __init__(self,characters):
        super().__init__(placeholder='選擇角色', options=[discord.SelectOption(label=str(c['name'])[:80],value=str(c['id']),description=f"角色 #{c['id']}") for c in characters[:25]])
    async def callback(self,i):
        await show_character(i,int(self.values[0]))

class TaskView(discord.ui.View):
    def __init__(self,uid,characters):
        super().__init__(timeout=300)
        self.uid=uid
        self.add_item(TaskPick(characters))
    async def interaction_check(self,i):
        if i.user.id!=self.uid:
            await i.response.send_message('請自行使用 /任務',ephemeral=True)
            return False
        return True

class AcceptButton(discord.ui.Button):
    def __init__(self,gid,cid,task):
        super().__init__(label='接取 '+str(task['title'])[:60],style=discord.ButtonStyle.primary)
        self.gid,self.cid,self.task_id=gid,cid,int(task['id'])
    async def callback(self,i):
        try:
            accepted=accept(self.gid,self.cid,i.user.id,self.task_id)
        except ValueError as e:
            return await i.response.send_message(str(e)[:180],ephemeral=True)
        except Exception:
            return await i.response.send_message('暫時無法接取，請稍後再試。',ephemeral=True)
        if isinstance(accepted,str) and accepted.startswith('applied:'):
            return await i.response.send_message('✅ 常駐申請已送交官方審核，申請編號 #'+accepted.split(':',1)[1]+'；審核通過才會占用席位。',ephemeral=True)
        await i.response.send_message('✅ 接取成功。請在角色論壇的「劇情討論」帖子發表演出內容，再於該帖子使用 `/任務` 填寫角色及任務編號回報。' if accepted else '已接取過此通告，不會重複建立紀錄。',ephemeral=True)

class PageButton(discord.ui.Button):
    def __init__(self,label,direction):
        super().__init__(label=label,style=discord.ButtonStyle.secondary)
        self.direction=direction
    async def callback(self,i):
        view=self.view
        if not isinstance(view,ListingsView):return
        view.page=max(0,min((len(view.rows)-1)//5,view.page+self.direction))
        view.populate()
        await i.response.edit_message(content=view.summary(),view=view)

class ListingsView(discord.ui.View):
    def __init__(self,uid,gid,cid,rows):
        super().__init__(timeout=300)
        self.uid,self.gid,self.cid,self.rows,self.page=uid,gid,cid,rows,0
        self.populate()
    def populate(self):
        self.clear_items()
        for task in self.rows[self.page*5:self.page*5+5]:self.add_item(AcceptButton(self.gid,self.cid,task))
        if len(self.rows)>5:
            self.add_item(PageButton('上一頁',-1))
            self.add_item(PageButton('下一頁',1))
    def summary(self):
        from modules.task_conditions import public_minimums
        from discord.utils import escape_markdown
        page=self.rows[self.page*5:self.page*5+5]
        text='\n'.join(f"**#{r['id']}** {escape_markdown(r['title'])}（{r['subtype'] or '一般'}）\n最低接取條件：{public_minimums(self.gid,r)}" for r in page)
        return (f'🎬 通告 {self.page+1}/{(len(self.rows)+4)//5} 頁\n'+text)[:1900]
    async def interaction_check(self,i):
        if i.user.id!=self.uid:
            await i.response.send_message('請使用自己的任務面板',ephemeral=True)
            return False
        return True

class KindPick(discord.ui.Select):
    def __init__(self,gid,cid,choices):
        super().__init__(placeholder='選擇任務分類',options=[discord.SelectOption(label=KINDS[k],value=k) for k in choices])
        self.gid,self.cid=gid,cid
    async def callback(self,i):
        try:rows=board(self.gid,self.cid,i.user.id,self.values[0])
        except ValueError as e:return await i.response.send_message(str(e)[:180],ephemeral=True)
        except Exception:return await i.response.send_message('讀取任務失敗，請稍後再試。',ephemeral=True)
        if not rows:return await i.response.send_message('目前沒有符合資格且已發布的任務。',ephemeral=True)
        view=ListingsView(i.user.id,self.gid,self.cid,rows)
        await i.response.send_message(view.summary(),view=view,ephemeral=True)

class KindsView(discord.ui.View):
    def __init__(self,uid,gid,cid,kinds):
        super().__init__(timeout=300)
        self.uid=uid
        self.add_item(KindPick(gid,cid,kinds))
    async def interaction_check(self,i):
        if i.user.id!=self.uid:
            await i.response.send_message('這不是你的面板',ephemeral=True)
            return False
        return True

async def show_character(i,cid):
    if not character_owner(i.guild_id,cid,i.user.id):
        return await i.response.send_message('無權使用此角色',ephemeral=True)
    p=progress(i.guild_id,cid)
    if not p['graduated']:
        thread=bot_thread_id(i.guild_id,cid)
        if not p['graduated']:
            desc=f"🎓 新手指令探索 {p['done']}/{p['total']}\n尚未完成："+'、'.join('/'+n for n in p['remaining'])
            desc+=f'\n請在 <#{thread}> 操作。' if thread else '\n未找到專屬「機器人使用」帖子，請聯絡管理員。'
            await i.response.send_message(desc[:1900],ephemeral=True)
            if thread and str(i.channel_id)==thread:
                try: record_success(i.guild_id,cid,i.user.id,i.channel_id,'任務')
                except Exception: pass
            return
    available=[]
    for kind in KINDS:
        if kind in ('limited','special'):
            try:
                if not board(i.guild_id,cid,i.user.id,kind):continue
            except Exception:continue
        available.append(kind)
    await i.response.send_message(f'📋 角色 #{cid} 的任務中心',view=KindsView(i.user.id,i.guild_id,cid,available),ephemeral=True)

class TasksCenter(commands.Cog):
    def __init__(self,bot):self.bot=bot
    @app_commands.command(name='任務',description='開啟任務中心；在指定討論串回報已接通告')
    @app_commands.guild_only()
    @app_commands.describe(角色編號='要操作的本人角色編號',任務編號='要回報的已接任務編號')
    async def tasks(self,i:discord.Interaction,角色編號:int|None=None,任務編號:int|None=None):
        from modules.task_event_bridge import require_tutorial_channel
        allowed, tutorial_character = await require_tutorial_channel(i, '任務')
        if not allowed:
            return
        if tutorial_character is not None:
            if 角色編號 is not None and 角色編號 != tutorial_character:
                return await i.response.send_message('請使用自己機器人使用帖子所屬角色編號。', ephemeral=True)
            if 任務編號 is not None:
                return await i.response.send_message('新手階段不可回報正式任務。', ephemeral=True)
            角色編號 = tutorial_character
        initialize();seed_newbie(i.guild_id);seed_screen_tasks(i.guild_id);seed_variety_tasks(i.guild_id);seed_commercial_tasks(i.guild_id)
        if 角色編號 is None:
            if 任務編號 is not None:
                return await i.response.send_message('回報任務時須填寫角色編號。',ephemeral=True)
            cs=owned(i.guild_id,i.user.id)
            if not cs:return await i.response.send_message('請先啟用角色',ephemeral=True)
            return await i.response.send_message('選擇要查看任務的角色：',view=TaskView(i.user.id,cs),ephemeral=True)
        if 任務編號 is not None:
            # Discord 讀取留言歷史可能需要超過 3 秒，先 ACK 避免互動逾時。
            await i.response.defer(ephemeral=True, thinking=True)
            try:
                from modules.task_evidence import find_thread_evidence
                with get_connection() as db:
                    active=db.execute('''SELECT accepted_at FROM rp_task_progress WHERE
                        guild_id=? AND character_id=? AND task_id=? AND status='accepted'
                        ORDER BY accepted_at DESC LIMIT 1''',
                        (str(i.guild_id),int(角色編號),int(任務編號))).fetchone()
                if not active:raise ValueError('找不到可回報的已接取任務')
                evidence=await find_thread_evidence(i,accepted_at=active['accepted_at'])
                report(i.guild_id,角色編號,i.user.id,任務編號,i.channel_id,evidence=evidence)
            except ValueError as e:
                return await i.followup.send(str(e)[:1800],ephemeral=True)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('任務回報失敗')
                return await i.followup.send('任務回報失敗，請聯絡管理員檢查紀錄。',ephemeral=True)
            return await i.followup.send('✅ 留言證據已記錄，回報已送交官方審核；獎勵尚未發放。',ephemeral=True)
        await show_character(i,角色編號)

async def setup(bot):
    await bot.add_cog(TasksCenter(bot))
