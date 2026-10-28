"""內容建立中心：草稿、編輯、發布、下架；由 /官方 進入。"""
from __future__ import annotations
import sqlite3
import discord
from discord.ext import commands
from core.database import transaction,get_connection

TYPES={'story':'📖 劇情','product':'🛍️ 商品企劃','lottery':'🎰 抽獎獎單','prize':'🏆 獎項','event':'🎪 活動','npc':'👑 官方 NPC','achievement':'🎖️ 成就稱號','world':'🌍 世界觀','mission':'📜 任務'}

def init_db():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS official_contents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,
          kind TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','archived')),
          creator_id TEXT NOT NULL,editor_id TEXT NOT NULL,
          message_id TEXT,channel_id TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_official_contents ON official_contents(guild_id,status,id)')

def owner(i):
    return bool(i.guild and i.user.id==i.guild.owner_id)

def create(gid,uid,kind,title,body):
    init_db()
    if kind not in TYPES: raise ValueError('未知內容類型')
    title=title.strip();body=body.strip()
    if not 1<=len(title)<=100 or not 1<=len(body)<=3500:raise ValueError('標題 1～100 字；內容 1～3500 字')
    with transaction() as db:
        cur=db.execute('INSERT INTO official_contents(guild_id,kind,title,body,creator_id,editor_id) VALUES(?,?,?,?,?,?)',(str(gid),kind,title,body,str(uid),str(uid)))
        return cur.lastrowid

def list_items(gid):
    init_db()
    with get_connection() as db:
        rows=db.execute('SELECT id,kind,title,status FROM official_contents WHERE guild_id=? ORDER BY id DESC LIMIT 20',(str(gid),)).fetchall()
    if not rows:return '目前沒有內容。'
    return '\n'.join(f"#{r['id']}｜{TYPES.get(r['kind'],r['kind'])}｜{r['title'][:35]}｜{r['status']}" for r in rows)

def read(gid,cid):
    init_db()
    with get_connection() as db:r=db.execute('SELECT * FROM official_contents WHERE guild_id=? AND id=?',(str(gid),cid)).fetchone()
    if r is None:raise ValueError('找不到這筆內容')
    return dict(r)

def update(gid,uid,cid,title,body):
    row=read(gid,cid)
    if row['status']!='draft':raise ValueError('只有草稿可編輯；請先下架後再修改')
    if not 1<=len(title.strip())<=100 or not 1<=len(body.strip())<=3500:raise ValueError('字數不符合限制')
    with transaction() as db:
        db.execute("UPDATE official_contents SET title=?,body=?,editor_id=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=? AND status='draft'",(title.strip(),body.strip(),str(uid),str(gid),cid))

def set_status(gid,uid,cid,status,channel_id=None,message_id=None):
    if status not in ('draft','published','archived'):raise ValueError('狀態錯誤')
    with transaction() as db:
        db.execute('UPDATE official_contents SET status=?,editor_id=?,channel_id=?,message_id=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?',(status,str(uid),str(channel_id) if channel_id else None,str(message_id) if message_id else None,str(gid),cid))

class NewContent(discord.ui.Modal,title='新增官方內容草稿'):
    heading=discord.ui.TextInput(label='標題',max_length=100)
    details=discord.ui.TextInput(label='內容／規則',style=discord.TextStyle.paragraph,max_length=3500)
    def __init__(self,kind):super().__init__();self.kind=kind
    async def on_submit(self,i):
        if not owner(i):await i.response.send_message('僅限群主。',ephemeral=True);return
        try:
            cid=create(i.guild_id,i.user.id,self.kind,str(self.heading.value),str(self.details.value));msg=f'✅ 已建立草稿 #{cid}。可從「內容管理」發布。'
        except ValueError as e:msg=f'⚠️ {e}'
        await i.response.send_message(msg,ephemeral=True)

class EditContent(discord.ui.Modal,title='編輯內容草稿'):
    cid=discord.ui.TextInput(label='內容編號',max_length=12)
    heading=discord.ui.TextInput(label='新標題',max_length=100)
    details=discord.ui.TextInput(label='新內容（完整替換）',style=discord.TextStyle.paragraph,max_length=3500)
    async def on_submit(self,i):
        if not owner(i):await i.response.send_message('僅限群主。',ephemeral=True);return
        try:update(i.guild_id,i.user.id,int(self.cid.value),str(self.heading.value),str(self.details.value));msg='✅ 草稿修改成功。'
        except (ValueError,sqlite3.Error) as e:msg=f'⚠️ {e}'
        await i.response.send_message(msg,ephemeral=True)

class PublishContent(discord.ui.Modal,title='發布／下架官方內容'):
    cid=discord.ui.TextInput(label='內容編號',max_length=12)
    action=discord.ui.TextInput(label='操作：發布 或 下架',max_length=2)
    channel=discord.ui.TextInput(label='發布到頻道 ID（發布時必填）',required=False,max_length=22)
    async def on_submit(self,i):
        if not owner(i):await i.response.send_message('僅限群主。',ephemeral=True);return
        await i.response.defer(ephemeral=True)
        try:
            cid=int(self.cid.value);r=read(i.guild_id,cid);act=str(self.action.value).strip()
            if act=='下架':
                # 下架記錄；已發布的 Discord 訊息不自動刪除，避免誤刪內容。
                set_status(i.guild_id,i.user.id,cid,'archived',r['channel_id'],r['message_id']);msg='✅ 已標記下架。原頻道訊息仍保留，需人工刪除。'
            elif act=='發布':
                if r['status']!='draft':raise ValueError('只有草稿能發布')
                channel=await i.guild.fetch_channel(int(self.channel.value))
                if not isinstance(channel,(discord.TextChannel,discord.Thread)) or channel.guild.id!=i.guild_id:raise ValueError('需要本伺服器可發言的文字頻道或討論串')
                sent=await channel.send(f"**{TYPES[r['kind']]}｜{discord.utils.escape_mentions(r['title'])}**\n\n{discord.utils.escape_mentions(r['body'])}",allowed_mentions=discord.AllowedMentions.none())
                set_status(i.guild_id,i.user.id,cid,'published',channel.id,sent.id);msg=f'✅ 已發布 #{cid} 到 {channel.mention}'
            else:raise ValueError('操作只能輸入「發布」或「下架」')
        except (ValueError,discord.HTTPException,sqlite3.Error) as e:msg=f'⚠️ {e}'
        await i.followup.send(msg[:1900],ephemeral=True)

class ContentTypeSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='選擇要建立的內容類型',options=[discord.SelectOption(label=name,value=key) for key,name in TYPES.items()],row=0)
    async def callback(self,i):
        if not owner(i):await i.response.send_message('僅限群主建立。',ephemeral=True);return
        if self.values[0]=='lottery':
            from modules.lottery_admin_ui import LotteryAdminView
            await i.response.edit_message(content='🎟️ **抽獎獎單管理**',view=LotteryAdminView(i))
            return
        if self.values[0]=='story':
            from modules.story_builder import StoryBuilderView
            await i.response.edit_message(content='📖 **角色劇情編輯器**',view=StoryBuilderView(i))
            return
        await i.response.send_modal(NewContent(self.values[0]))

class ContentView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600);self.user_id=i.user.id;self.guild_id=i.guild_id;self.add_item(ContentTypeSelect())
        from modules.story_builder import StoryBuilderButton
        self.add_item(StoryBuilderButton())
        from modules.lottery_admin_ui import LotteryAdminButton
        self.add_item(LotteryAdminButton())
    async def interaction_check(self,i):
        if i.user.id!=self.user_id or i.guild_id!=self.guild_id or not owner(i):
            await i.response.send_message('這不是你的管理面板或權限不足。',ephemeral=True);return False
        return True
    @discord.ui.button(label='📋 內容清單',style=discord.ButtonStyle.primary,row=1)
    async def listing(self,i,b):await i.response.send_message(list_items(i.guild_id)[:1900],ephemeral=True)
    @discord.ui.button(label='✏️ 編輯草稿',style=discord.ButtonStyle.secondary,row=1)
    async def editing(self,i,b):await i.response.send_modal(EditContent())
    @discord.ui.button(label='📢 發布／下架',style=discord.ButtonStyle.success,row=1)
    async def publishing(self,i,b):await i.response.send_modal(PublishContent())
    @discord.ui.button(label='↩️ 返回官方',style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,b):
        from modules.official_center import OfficialHome
        await i.response.edit_message(content='🏛️ **官方管理中心**',view=OfficialHome(i))

class ContentButton(discord.ui.Button):
    def __init__(self):super().__init__(label='✍️ 內容建立',style=discord.ButtonStyle.primary,row=3)
    async def callback(self,i):
        if not owner(i):await i.response.send_message('只有群主可以建立官方內容。',ephemeral=True);return
        await i.response.edit_message(content='✍️ **內容建立中心**\n選擇類型建立草稿，或管理既有內容。',view=ContentView(i))

async def setup(bot:commands.Bot):init_db()
