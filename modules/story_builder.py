"""角色互動劇情編輯器：獨立模組，供 /官方 → 內容建立 調用。"""
from __future__ import annotations

import json
import sqlite3
import discord
from discord.ext import commands
from core.database import get_connection, transaction


def owner(i):
    return bool(i.guild and i.guild.owner_id == i.user.id)


def init_db():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS rp_stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('main','side')),
            title TEXT NOT NULL,
            prerequisite_json TEXT NOT NULL DEFAULT '{}',
            rewards_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','ready','archived')),
            creator_id TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(guild_id,character_id) REFERENCES characters(guild_id,id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_story_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL REFERENCES rp_stories(id) ON DELETE CASCADE,
            segment_no INTEGER NOT NULL CHECK(segment_no > 0),
            speaker TEXT NOT NULL,
            dialogue TEXT NOT NULL,
            choices_json TEXT NOT NULL DEFAULT '[]',
            next_segment INTEGER,
            UNIQUE(story_id,segment_no)
        )''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_rp_stories_guild ON rp_stories(guild_id,character_id,status)')


def characters(gid):
    with get_connection() as db:
        rows = db.execute("SELECT id,name,character_type FROM characters WHERE guild_id=? AND status IN ('approved','active') ORDER BY id DESC LIMIT 25",(str(gid),)).fetchall()
    return [dict(r) for r in rows]


def story(gid,sid):
    with get_connection() as db:
        row=db.execute('SELECT s.*,c.name AS character_name FROM rp_stories s JOIN characters c ON c.id=s.character_id AND c.guild_id=s.guild_id WHERE s.guild_id=? AND s.id=?',(str(gid),int(sid))).fetchone()
    if row is None: raise ValueError('找不到這篇劇情。')
    return dict(row)


def segments(sid):
    with get_connection() as db:
        return [dict(r) for r in db.execute('SELECT * FROM rp_story_segments WHERE story_id=? ORDER BY segment_no',(int(sid),)).fetchall()]


def story_list(gid):
    with get_connection() as db:
        return [dict(r) for r in db.execute('SELECT s.id,s.kind,s.title,s.status,c.name AS character_name FROM rp_stories s JOIN characters c ON c.id=s.character_id AND c.guild_id=s.guild_id WHERE s.guild_id=? ORDER BY s.id DESC LIMIT 25',(str(gid),)).fetchall()]


def create_story(gid,uid,cid,kind,title):
    if kind not in ('main','side'): raise ValueError('劇情類型不正確。')
    if not 1<=len(title.strip())<=100:raise ValueError('標題需為 1～100 字。')
    with transaction() as db:
        c=db.execute("SELECT id FROM characters WHERE guild_id=? AND id=? AND status IN ('approved','active')",(str(gid),int(cid))).fetchone()
        if c is None:raise ValueError('角色不存在，或尚未通過二審。')
        cur=db.execute('INSERT INTO rp_stories(guild_id,character_id,kind,title,creator_id) VALUES(?,?,?,?,?)',(str(gid),int(cid),kind,title.strip(),str(uid)))
        return cur.lastrowid


def save_segment(gid,sid,no,speaker,dialogue,choices,next_no):
    if not 1<=int(no)<=999:raise ValueError('段落編號限 1～999。')
    if not 1<=len(speaker.strip())<=80:raise ValueError('角色名稱限 1～80 字。')
    if not 1<=len(dialogue.strip())<=1600:raise ValueError('劇情文字限 1～1600 字。')
    if len(choices)>3:raise ValueError('每段最多三個選項。')
    for choice in choices:
        if not 1<=len(choice['text'])<=100:raise ValueError('選項限 1～100 字。')
        if choice['target'] is not None and not 1<=choice['target']<=999:raise ValueError('跳轉段落編號不正確。')
    if next_no is not None and not 1<=next_no<=999:raise ValueError('下一段編號不正確。')
    with transaction() as db:
        r=db.execute('SELECT status FROM rp_stories WHERE guild_id=? AND id=?',(str(gid),int(sid))).fetchone()
        if not r:raise ValueError('劇情不存在。')
        if r['status']!='draft':raise ValueError('請先撤回成草稿，再修改段落。')
        db.execute('''INSERT INTO rp_story_segments(story_id,segment_no,speaker,dialogue,choices_json,next_segment)
          VALUES(?,?,?,?,?,?) ON CONFLICT(story_id,segment_no) DO UPDATE SET
          speaker=excluded.speaker,dialogue=excluded.dialogue,choices_json=excluded.choices_json,next_segment=excluded.next_segment''',
          (int(sid),int(no),speaker.strip(),dialogue.strip(),json.dumps(choices,ensure_ascii=False),next_no))
        db.execute('UPDATE rp_stories SET updated_at=CURRENT_TIMESTAMP WHERE id=?',(int(sid),))


def validate(gid,sid):
    row=story(gid,sid)
    parts=segments(sid)
    errors=[]
    lookup={x['segment_no']:x for x in parts}
    if 1 not in lookup:errors.append('缺少第 1 段。')
    if not parts:errors.append('尚無任何劇情段落。')
    for p in parts:
        try: choices=json.loads(p['choices_json'])
        except (ValueError,TypeError):errors.append(f"第 {p['segment_no']} 段選項資料損壞");continue
        for c in choices:
            target=c.get('target') if isinstance(c,dict) else None
            if target is not None and target not in lookup:errors.append(f"第 {p['segment_no']} 段有選項指向不存在的第 {target} 段")
        nxt=p['next_segment']
        if nxt is not None and nxt not in lookup:errors.append(f"第 {p['segment_no']} 段下一段 #{nxt} 不存在")
        if nxt==p['segment_no'] or any(c.get('target')==p['segment_no'] for c in choices if isinstance(c,dict)):
            errors.append(f"第 {p['segment_no']} 段直接跳回自己")
    # 只有標記為 ready 的劇情可供下一階段攻略系統讀取。
    return row,parts,errors


def set_status(gid,sid,status):
    if status not in ('draft','ready','archived'):raise ValueError('狀態不正確。')
    if status=='ready':
        _,_,errors=validate(gid,sid)
        if errors:raise ValueError('目前條件尚未齊全：\n'+'\n'.join(errors[:8]))
    with transaction() as db:
        result=db.execute('UPDATE rp_stories SET status=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?',(status,str(gid),int(sid)))
        if result.rowcount!=1:raise ValueError('找不到劇情。')


async def respond(i,text):
    if i.response.is_done():await i.followup.send(text[:1900],ephemeral=True)
    else:await i.response.send_message(text[:1900],ephemeral=True)


class NewStory(discord.ui.Modal,title='建立角色劇情'):
    heading=discord.ui.TextInput(label='劇情標題',max_length=100)
    def __init__(self,cid,kind):super().__init__();self.cid=cid;self.kind=kind
    async def on_submit(self,i):
        if not owner(i):return await respond(i,'⛔ 僅群主可編輯。')
        try:
            sid=create_story(i.guild_id,i.user.id,self.cid,self.kind,str(self.heading.value))
            await respond(i,f'✅ 已建立劇情 #{sid}。請從「編輯段落」新增第 1 段。')
        except (ValueError,sqlite3.Error) as e:await respond(i,f'⚠️ {e}')


class CharacterPicker(discord.ui.Select):
    def __init__(self,gid,kind):
        chars=characters(gid);self.kind=kind
        options=[discord.SelectOption(label=f"#{r['id']} {r['name']}"[:100],value=str(r['id'])) for r in chars]
        if not options:options=[discord.SelectOption(label='尚無已審核角色',value='none')]
        super().__init__(placeholder='選擇要建立劇情的角色',options=options,disabled=not bool(chars))
    async def callback(self,i):
        if not owner(i):return await respond(i,'⛔ 僅群主可編輯。')
        await i.response.send_modal(NewStory(int(self.values[0]),self.kind))


class CharacterPickerView(discord.ui.View):
    def __init__(self,i,kind):
        super().__init__(timeout=300);self.uid=i.user.id;self.gid=i.guild_id;self.add_item(CharacterPicker(i.guild_id,kind))
    async def interaction_check(self,i):
        if i.user.id!=self.uid or i.guild_id!=self.gid or not owner(i):
            await respond(i,'⛔ 此操作不屬於你。');return False
        return True


class SegmentModal(discord.ui.Modal,title='新增／覆蓋劇情段落'):
    sid=discord.ui.TextInput(label='劇情編號',max_length=12)
    number=discord.ui.TextInput(label='段落編號（從 1 開始）',max_length=3)
    speaker=discord.ui.TextInput(label='說話角色',max_length=80)
    dialogue=discord.ui.TextInput(label='劇情內容',style=discord.TextStyle.paragraph,max_length=1600)
    options=discord.ui.TextInput(label='選項：每行 選項文字 | 跳轉段落（可留空）',style=discord.TextStyle.paragraph,required=False,max_length=900,placeholder='向她道歉 | 3\n開個玩笑 | 4\n保持沉默')
    async def on_submit(self,i):
        if not owner(i):return await respond(i,'⛔ 僅群主可編輯。')
        try:
            opts=[]
            for line in str(self.options.value or '').splitlines():
                if not line.strip():continue
                text,*target=line.split('|',1)
                target_no=int(target[0].strip()) if target and target[0].strip() else None
                opts.append({'text':text.strip(),'target':target_no})
            # 沒有跳轉的選項會由攻略執行器自動進入下一段。
            save_segment(i.guild_id,int(self.sid.value),int(self.number.value),str(self.speaker.value),str(self.dialogue.value),opts,None)
            await respond(i,f'✅ 第 {self.number.value} 段已保存。未設定分支的選項會依序進入下一段。')
        except (ValueError,sqlite3.Error) as e:await respond(i,f'⚠️ {e}')


class StoryIdModal(discord.ui.Modal,title='劇情檢視／狀態管理'):
    sid=discord.ui.TextInput(label='劇情編號',max_length=12)
    action=discord.ui.TextInput(label='輸入：查看／就緒／草稿／封存',max_length=2)
    async def on_submit(self,i):
        if not owner(i):return await respond(i,'⛔ 僅群主可編輯。')
        try:
            sid=int(self.sid.value);act=str(self.action.value).strip()
            if act in ('就緒','草稿','封存'):
                set_status(i.guild_id,sid,{'就緒':'ready','草稿':'draft','封存':'archived'}[act])
                return await respond(i,f'✅ 劇情 #{sid} 已設為「{act}」。就緒只表示編輯檢查通過，玩家攻略功能尚待串接。')
            if act!='查看':raise ValueError('操作只能填查看／就緒／草稿／封存。')
            s,parts,errors=validate(i.guild_id,sid)
            lines=[f"📖 #{sid}｜{s['title']}｜{s['character_name']}｜{s['status']}"]
            for p in parts:
                lines.append(f"\n【{p['segment_no']}】{p['speaker']}：{p['dialogue'][:180]}")
                for ch in json.loads(p['choices_json']):lines.append(f"　▾ {ch['text']} → {ch['target'] or '下一段'}")
            if errors:lines.append('\n⚠️ '+ '；'.join(errors[:6]))
            await respond(i,'\n'.join(lines))
        except (ValueError,sqlite3.Error) as e:await respond(i,f'⚠️ {e}')


class StoryBuilderView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id
    async def interaction_check(self,i):
        if i.user.id!=self.uid or i.guild_id!=self.gid or not owner(i):
            await respond(i,'⛔ 此面板不屬於你。');return False
        return True
    @discord.ui.button(label='📖 新主線',style=discord.ButtonStyle.primary,row=0)
    async def main(self,i,b):await i.response.send_message('選擇角色：',view=CharacterPickerView(i,'main'),ephemeral=True)
    @discord.ui.button(label='🌿 新支線',style=discord.ButtonStyle.primary,row=0)
    async def side(self,i,b):await i.response.send_message('選擇角色：',view=CharacterPickerView(i,'side'),ephemeral=True)
    @discord.ui.button(label='✍️ 編輯段落',style=discord.ButtonStyle.success,row=0)
    async def edit(self,i,b):await i.response.send_modal(SegmentModal())
    @discord.ui.button(label='📚 劇情清單',style=discord.ButtonStyle.secondary,row=1)
    async def listing(self,i,b):
        rows=story_list(i.guild_id)
        await respond(i,'\n'.join(f"#{r['id']}｜{r['character_name']}｜{'主線' if r['kind']=='main' else '支線'}｜{r['title']}｜{r['status']}" for r in rows) if rows else '目前沒有劇情。')
    @discord.ui.button(label='🔎 檢視／就緒／封存',style=discord.ButtonStyle.secondary,row=1)
    async def status(self,i,b):await i.response.send_modal(StoryIdModal())
    @discord.ui.button(label='↩️ 返回內容建立',style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,b):
        from modules.content_builder import ContentView
        await i.response.edit_message(content='✍️ **內容建立中心**',view=ContentView(i))


class StoryBuilderButton(discord.ui.Button):
    def __init__(self):super().__init__(label='📖 劇情編輯器',style=discord.ButtonStyle.primary,row=2)
    async def callback(self,i):
        if not owner(i):return await respond(i,'⛔ 僅群主可編輯劇情。')
        init_db()
        await i.response.edit_message(content='📖 **角色劇情編輯器**\n建立主線／支線，編輯段落與分支；「就緒」不代表正式上線。',view=StoryBuilderView(i))


async def setup(bot:commands.Bot):
    init_db()
