"""玩家唯一 /任務 入口：在本人角色論壇的帖子中操作，正式訊息保留。"""
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
        super().__init__(placeholder='選擇角色', options=[discord.SelectOption(label=str(c['name'])[:80],value=str(c['id']),description="本人已啟用角色") for c in characters[:25]])
    async def callback(self,i):
        selected = int(self.values[0])
        if not any(c['id']==selected for c in _thread_owned_characters(i)):
            await i.response.send_message('該角色不屬於此私人論壇，請重新使用 /任務。',ephemeral=False)
            return
        await show_character(i,selected)

class ProductionButton(discord.ui.Button):
    """在正式 /任務 面板中開啟原有完整作品製作中心。"""
    def __init__(self):
        super().__init__(label="🎬 作品製作中心", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        try:
            from core.permissions import Actor
            from modules.production_center import Home, initialize as initialize_productions
            initialize_productions()
            actor = Actor.from_interaction(interaction)
            await interaction.response.send_message(
                "🎬 **作品製作中心**\n建立企劃、報名演藝職位、管理招募與完工驗證。",
                view=Home(actor), ephemeral=False,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception("開啟作品製作中心失敗")
            if interaction.response.is_done():
                await interaction.followup.send("作品製作中心暫時無法開啟。", ephemeral=False)
            else:
                await interaction.response.send_message("作品製作中心暫時無法開啟。", ephemeral=False)


class TaskView(discord.ui.View):
    def __init__(self,uid,characters):
        super().__init__(timeout=300)
        self.uid=uid
        self.add_item(TaskPick(characters))
        self.add_item(ProductionButton())
    async def interaction_check(self,i):
        if i.user.id!=self.uid:
            await i.response.send_message('請自行使用 /任務',ephemeral=False)
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
            return await i.response.send_message(str(e)[:180],ephemeral=False)
        except Exception:
            return await i.response.send_message('暫時無法接取，請稍後再試。',ephemeral=False)
        if isinstance(accepted,str) and accepted.startswith('applied:'):
            return await i.response.send_message('✅ 常駐申請已送交官方審核，申請編號 #'+accepted.split(':',1)[1]+'；審核通過才會占用席位。',ephemeral=False)
        await i.response.send_message('✅ 接取成功。請在本人角色論壇發表劇情內容，再使用 `/任務` 選擇已接任務回報。' if accepted else '已接取過此通告，不會重複建立紀錄。',ephemeral=False)

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
        from core.work_schedule import task_slot
        from datetime import datetime
        from zoneinfo import ZoneInfo
        lines=[]
        for r in page:
            schedule=task_slot(self.gid,r['id'])
            date_line=(f"\n📅 正式檔期：{datetime.fromisoformat(schedule['starts_at']).astimezone(ZoneInfo('Asia/Taipei')).strftime('%m/%d %H:%M')} ～ {datetime.fromisoformat(schedule['ends_at']).astimezone(ZoneInfo('Asia/Taipei')).strftime('%m/%d %H:%M')}（台灣時間）" if schedule else "\n📅 未設定正式檔期（目前不鎖日曆）")
            lines.append(f"**#{r['id']}** {escape_markdown(r['title'])}（{r['subtype'] or '一般'}）\n最低接取條件：{public_minimums(self.gid,r)}"+date_line)
        text='\n'.join(lines)
        return (f'🎬 通告 {self.page+1}/{(len(self.rows)+4)//5} 頁\n'+text)[:1900]
    async def interaction_check(self,i):
        if i.user.id!=self.uid:
            await i.response.send_message('請使用自己的任務面板',ephemeral=False)
            return False
        return True

class KindPick(discord.ui.Select):
    def __init__(self,gid,cid,choices):
        super().__init__(placeholder='選擇任務分類',options=[discord.SelectOption(label=KINDS[k],value=k) for k in choices])
        self.gid,self.cid=gid,cid
    async def callback(self,i):
        try:rows=board(self.gid,self.cid,i.user.id,self.values[0])
        except ValueError as e:return await i.response.send_message(str(e)[:180],ephemeral=False)
        except Exception:return await i.response.send_message('讀取任務失敗，請稍後再試。',ephemeral=False)
        if not rows:return await i.response.send_message('目前沒有符合資格且已發布的任務。',ephemeral=False)
        view=ListingsView(i.user.id,self.gid,self.cid,rows)
        await i.response.send_message(view.summary(),view=view,ephemeral=False)

class ReportButton(discord.ui.Button):
    def __init__(self, gid, cid):
        super().__init__(label='📨 回報已接任務', style=discord.ButtonStyle.success, row=1)
        self.gid, self.cid = gid, cid

    async def callback(self, i):
        with get_connection() as db:
            rows = [dict(r) for r in db.execute('''SELECT p.task_id,d.title
                FROM rp_task_progress p JOIN rp_task_definitions d
                ON d.id=p.task_id AND d.guild_id=p.guild_id
                WHERE p.guild_id=? AND p.character_id=? AND p.status='accepted'
                ORDER BY p.accepted_at DESC LIMIT 25''', (str(self.gid), int(self.cid)))]
        if not rows:
            await i.response.send_message('目前沒有可回報的已接取任務。', ephemeral=False)
            return
        await i.response.send_message('📨 選擇要回報的通告（需先在本帖發布內容）：',
            view=ReportTaskView(i.user.id, i.guild_id, self.cid, rows), ephemeral=False)


class KindsView(discord.ui.View):
    def __init__(self,uid,gid,cid,kinds):
        super().__init__(timeout=300)
        self.uid=uid
        self.add_item(KindPick(gid,cid,kinds))
        self.add_item(ProductionButton())
        self.add_item(ReportButton(gid,cid))
    async def interaction_check(self,i):
        if i.user.id!=self.uid or not _thread_owned_characters(i):
            await i.response.send_message('僅能在自己的角色論壇使用此面板。',ephemeral=True)
            return False
        return True

TUTORIAL_COMMANDS = (
    '查看','登記','公司','商城','申請','銀行','陣營','網路',
    '劇情','傳聞','娛樂城','惡意','任務','排行','總覽','抽獎'
)


class NewbieGuideView(discord.ui.View):
    """四頁導覽，每頁四個指令；已完成狀態與獎勵收據每次重新讀取。"""
    def __init__(self, guild_id, user_id, character_id, *, page=0):
        super().__init__(timeout=600)
        self.gid = str(guild_id)
        self.uid = str(user_id)
        self.cid = int(character_id)
        self.page = max(0, min(3, int(page)))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page >= 3

    def content(self):
        from modules.task_newbie_rewards import REWARDS, EXPLANATIONS, status
        completed = progress(self.gid, self.cid)
        receipts = status(self.gid, self.cid)
        lines = [
            f'🎓 **新手任務指南｜第 {self.page+1}/4 頁**',
            f'完成進度：{completed["done"]}/{completed["total"]}',
            '操作對應指令，完成正式紀錄後會自動入帳。獎勵只能領取一次。',
            ''
        ]
        for command in TUTORIAL_COMMANDS[self.page*4:(self.page+1)*4]:
            complete = command in completed['completed']
            info = REWARDS.get(command)
            reward = info[2] if info else '無額外獎勵'
            receipt = receipts.get(command)
            state = ('✅ 已完成／已發獎' if receipt and receipt['status']=='done' else
                     '⏳ 已完成／待補發' if complete else '⬜ 尚未完成')
            lines.extend((f'**/{command}**｜{state}',
                          f'• 操作：{EXPLANATIONS[command]}。',
                          f'• 獎勵：{reward}。', ''))
        thread = bot_thread_id(self.gid, self.cid)
        lines.append('完成紀錄請以本人「機器人使用」帖子為準。'
                     + (f' <#{thread}>' if thread else ' 尚未綁定帖子，請通知管理員。'))
        return '\n'.join(lines)[:1900]

    async def interaction_check(self, interaction):
        if (str(interaction.guild_id)!=self.gid or str(interaction.user.id)!=self.uid
                or not any(c['id']==self.cid for c in _thread_owned_characters(interaction))):
            await interaction.response.send_message('請在本人角色論壇重新使用 /任務。', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='◀ 上一頁',style=discord.ButtonStyle.secondary,row=0)
    async def previous(self,i,button):
        v=NewbieGuideView(self.gid,self.uid,self.cid,page=self.page-1)
        await i.response.edit_message(content=v.content(),view=v)

    @discord.ui.button(label='下一頁 ▶',style=discord.ButtonStyle.primary,row=0)
    async def next_page(self,i,button):
        v=NewbieGuideView(self.gid,self.uid,self.cid,page=self.page+1)
        await i.response.edit_message(content=v.content(),view=v)

    @discord.ui.button(label='🔄 更新獎勵進度',style=discord.ButtonStyle.secondary,row=1)
    async def refresh(self,i,button):
        try:
            from modules.task_newbie_rewards import retry_completed
            retry_completed(self.gid,self.cid,self.uid)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('新手獎勵補發異常')
        await i.response.edit_message(content=self.content(),view=self)


async def show_character(i,cid):
    if not character_owner(i.guild_id,cid,i.user.id):
        return await i.response.send_message('無權使用此角色',ephemeral=False)
    # 新手獎勵發放中斷後，可以在本人任務面板重新補發。
    try:
        from modules.task_newbie_rewards import retry_completed
        retry_completed(i.guild_id,cid,i.user.id)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('任務面板補發新手獎勵失敗')
    p=progress(i.guild_id,cid)
    if not p['graduated']:
        # 新手探索任務保留公開帖子歷史，不用把十六項說明擠進一則訊息。
        guide = NewbieGuideView(i.guild_id,i.user.id,cid)
        await i.response.send_message(guide.content(),view=guide,ephemeral=False)
        thread=bot_thread_id(i.guild_id,cid)
        if thread and str(i.channel_id)==thread:
            try: record_success(i.guild_id,cid,i.user.id,i.channel_id,'任務')
            except Exception:
                import logging
                logging.getLogger(__name__).exception('新手「任務」完成紀錄失敗')
        return
    available=[]
    for kind in KINDS:
        if kind in ('limited','special'):
            try:
                if not board(i.guild_id,cid,i.user.id,kind):continue
            except Exception:continue
        available.append(kind)
    await i.response.send_message(f'📋 任務中心',view=KindsView(i.user.id,i.guild_id,cid,available),ephemeral=False)

def _thread_owned_characters(i):
    # 本人角色論壇的固定帖子或本人新建帖子
    if not isinstance(i.channel, discord.Thread) or i.guild_id is None:
        return []
    parent_id = getattr(getattr(i.channel, 'parent', None), 'id', None)
    with get_connection() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='character_forum_links'").fetchone()
        if not exists:
            return []
        rows = db.execute('''SELECT c.id,c.name,f.forum_id,f.bot_id,f.story_id
            FROM characters c JOIN character_forum_links f
            ON f.guild_id=c.guild_id AND f.character_id=c.id
            WHERE c.guild_id=? AND c.owner_id=? AND c.status='active'
              AND c.character_type='player' ORDER BY c.id''',
            (str(i.guild_id), str(i.user.id))).fetchall()
    matched = []
    for r in rows:
        fixed = str(i.channel_id) in (str(r['bot_id']), str(r['story_id']))
        created_here = (parent_id is not None
                        and str(parent_id) == str(r['forum_id'])
                        and str(getattr(i.channel, 'owner_id', None)) == str(i.user.id))
        if fixed or created_here:
            matched.append({'id': int(r['id']), 'name': r['name']})
    return matched


class ReportTaskSelect(discord.ui.Select):
    def __init__(self, character_id, tasks):
        options = [discord.SelectOption(label=str(r['title'])[:95], value=str(r['task_id']))
                   for r in tasks[:25]]
        super().__init__(placeholder='選擇已接取且待回報的通告', options=options, row=0)
        self.character_id = int(character_id)
        self.task_ids = {int(r['task_id']) for r in tasks[:25]}

    async def callback(self, interaction):
        task_id = int(self.values[0])
        if task_id not in self.task_ids:
            await interaction.response.send_message('任務選項已失效，請重新開啟。', ephemeral=False)
            return
        await interaction.response.defer(ephemeral=False, thinking=True)
        try:
            from modules.task_evidence import find_thread_evidence
            with get_connection() as db:
                active = db.execute('''SELECT accepted_at FROM rp_task_progress
                    WHERE guild_id=? AND character_id=? AND task_id=? AND status='accepted'
                    ORDER BY accepted_at DESC LIMIT 1''',
                    (str(interaction.guild_id), self.character_id, task_id)).fetchone()
            if not active:
                raise ValueError('此通告已不在待回報狀態。')
            evidence = await find_thread_evidence(interaction, accepted_at=active['accepted_at'])
            personal_forum_id = None
            if (getattr(interaction.channel,'owner_id',None)==interaction.user.id
                    and getattr(interaction.channel,'parent',None) is not None):
                personal_forum_id = interaction.channel.parent.id
            report(interaction.guild_id, self.character_id, interaction.user.id,
                   task_id, interaction.channel_id, evidence=evidence,
                   verified_personal_forum_id=personal_forum_id)
        except ValueError as exc:
            return await interaction.followup.send(str(exc)[:1800], ephemeral=False)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('選單送出任務回報失敗')
            return await interaction.followup.send('任務回報發生異常，請聯絡管理員。', ephemeral=False)
        await interaction.followup.send('✅ 任務回報已送交官方審核，結果與獎勵以審核紀錄為準。', ephemeral=False)


class ReportTaskView(discord.ui.View):
    def __init__(self, user_id, guild_id, character_id, tasks):
        super().__init__(timeout=300)
        self.uid, self.gid = int(user_id), int(guild_id)
        self.add_item(ReportTaskSelect(character_id, tasks))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.uid or interaction.guild_id != self.gid:
            await interaction.response.send_message('請自行使用 /任務 開啟你的回報選單。', ephemeral=True)
            return False
        if not any(r['id'] == self.children[0].character_id for r in _thread_owned_characters(interaction)):
            await interaction.response.send_message('此帖子不屬於你的角色論壇。', ephemeral=True)
            return False
        return True


class TasksCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='任務', description='在本人角色論壇查看及回報任務')
    @app_commands.guild_only()
    async def tasks(self, i: discord.Interaction):
        # 只有本人角色論壇的固定帖子或本人新建立的帖子可使用。
        characters = _thread_owned_characters(i)
        if not characters:
            await i.response.send_message('請到自己角色的「機器人使用」帖子，或在自己角色論壇新建帖子後使用 /任務。', ephemeral=False)
            return
        initialize()
        seed_newbie(i.guild_id)
        seed_screen_tasks(i.guild_id)
        seed_variety_tasks(i.guild_id)
        seed_commercial_tasks(i.guild_id)
        if len(characters) > 1:
            await i.response.send_message('請先選擇要操作的角色：', view=TaskView(i.user.id, characters), ephemeral=False)
            return
        await show_character(i, characters[0]['id'])


async def setup(bot):
    await bot.add_cog(TasksCenter(bot))
