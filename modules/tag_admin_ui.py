"""官方標籤庫管理：分類下拉選擇、批次新增與效果候選紀錄。"""
from __future__ import annotations
import re
import sqlite3
import discord
from discord.ext import commands
from core.database import transaction, get_connection
from modules.tag_library import tags, seed

CATEGORIES = ('大眾印象', '人物形象', '人物氣質', '藝能與舞台', '能力與才藝',
              '職涯與資歷', '粉絲與社交', '商務與代言', '爭議與公關', '其他')


def owner(i):
    return bool(i.guild and i.guild.owner_id == i.user.id)


def init_custom_effects(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_tag_effect_proposals (
        guild_id TEXT NOT NULL, tag_name TEXT NOT NULL,
        category TEXT NOT NULL, effect_text TEXT NOT NULL DEFAULT '',
        context_text TEXT NOT NULL DEFAULT '', relative_percent INTEGER,
        status TEXT NOT NULL DEFAULT 'pending', updated_by TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, tag_name))""")


def parse_effect(text):
    """僅讀取單個明確的百分比；這是候選值，不會自動生效。"""
    matches = re.findall(r'(?<!\d)([+＋\-−])\s*(\d{1,3})\s*%', str(text))
    if len(matches) != 1:
        return None
    sign, number = matches[0]
    amount = int(number) * (-1 if sign in ('-', '−') else 1)
    if abs(amount) > 100:
        raise ValueError('候選加成不得超過 ±100%；請人工審核')
    return amount


class AddTags(discord.ui.Modal, title='批次新增標籤與加成'):
    lines = discord.ui.TextInput(label='每行：名稱｜加成內容', style=discord.TextStyle.paragraph,
        max_length=3500, placeholder='實力派｜戲劇邀約核實後 +5%\n冷面美人｜純形象，無加成')
    context = discord.ui.TextInput(label='適用條件（選填）', required=False,
        style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, category):
        super().__init__()
        if category not in CATEGORIES:
            raise ValueError('未知分類')
        self.category = category

    async def on_submit(self, i):
        if not owner(i):
            await i.response.send_message('僅群主可操作。', ephemeral=True)
            return
        parsed = {}
        for line in str(self.lines).splitlines():
            if not line.strip():
                continue
            name, sep, effect = line.partition('｜')
            name, effect = name.strip(), (effect.strip() if sep else '')
            if not name or len(name) > 40:
                await i.response.send_message('每個標籤名稱必須介於 1～40 字。', ephemeral=True)
                return
            try:
                amount = parse_effect(effect) if effect else None
            except ValueError as exc:
                await i.response.send_message(str(exc), ephemeral=True)
                return
            parsed[name] = (effect, amount)
        if not parsed:
            await i.response.send_message('至少填寫一個標籤。', ephemeral=True)
            return
        seed(i.guild_id)
        with transaction() as db:
            init_custom_effects(db)
            for name, (effect, amount) in parsed.items():
                db.execute('INSERT OR IGNORE INTO rp_tag_library(guild_id,name,category) VALUES(?,?,?)',
                           (str(i.guild_id), name, '大眾印象'))
                # 細分類與文字效果記在提案表；標籤主目錄維持大眾印象，以供玩家選用。
                db.execute("""INSERT INTO rp_tag_effect_proposals
                    (guild_id,tag_name,category,effect_text,context_text,relative_percent,status,updated_by)
                    VALUES(?,?,?,?,?,?,'pending',?)
                    ON CONFLICT(guild_id,tag_name) DO UPDATE SET
                    effect_text=excluded.effect_text, context_text=excluded.context_text,
                    relative_percent=excluded.relative_percent, status='pending',
                    updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP""",
                    (str(i.guild_id), name, self.category, effect,
                     str(self.context).strip(), amount, str(i.user.id)))
        await i.response.send_message(
            f'✅ 登記 {len(parsed)} 個標籤。效果為待核准候選，不會直接改變角色數值。',
            ephemeral=True)


class CategorySelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='選擇標籤內部分類', row=0,
                         options=[discord.SelectOption(label=c, value=c) for c in CATEGORIES])

    async def callback(self, i):
        await i.response.send_modal(AddTags(self.values[0]))


class TagCategoryView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=300)
        self.uid, self.gid = i.user.id, i.guild_id
        self.add_item(CategorySelect())

    async def interaction_check(self, i):
        if not owner(i) or i.user.id != self.uid or i.guild_id != self.gid:
            await i.response.send_message('沒有權限。', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='⬅️ 返回標籤管理', style=discord.ButtonStyle.secondary, row=1)
    async def back(self, i, button):
        await i.response.edit_message(content='🏷️ 官方標籤庫管理', view=TagAdminView(i))


class EditTag(discord.ui.Modal, title='修改標籤'):
    old = discord.ui.TextInput(label='既有標籤名稱', max_length=40)
    new = discord.ui.TextInput(label='新名稱（停用時可留空）', required=False, max_length=40)
    action = discord.ui.TextInput(label='操作：更名／啟用／停用', max_length=2)

    async def on_submit(self, i):
        if not owner(i):
            await i.response.send_message('僅群主可操作。', ephemeral=True)
            return
        action, old, new = str(self.action).strip(), str(self.old).strip(), str(self.new).strip()
        if action not in ('更名', '啟用', '停用') or (action == '更名' and not new):
            await i.response.send_message('操作或名稱無效。', ephemeral=True)
            return
        try:
            with transaction() as db:
                if action == '更名':
                    cur = db.execute('UPDATE rp_tag_library SET name=? WHERE guild_id=? AND name=?',
                                     (new, str(i.guild_id), old))
                    init_custom_effects(db)
                    db.execute('UPDATE OR IGNORE rp_tag_effect_proposals SET tag_name=? WHERE guild_id=? AND tag_name=?',
                               (new, str(i.guild_id), old))
                else:
                    cur = db.execute('UPDATE rp_tag_library SET enabled=? WHERE guild_id=? AND name=?',
                                     (1 if action == '啟用' else 0, str(i.guild_id), old))
        except sqlite3.Error as exc:
            await i.response.send_message(f'⚠️ {exc}', ephemeral=True)
            return
        await i.response.send_message('✅ 已更新。' if cur.rowcount else '找不到標籤。', ephemeral=True)


class TagAdminView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.uid, self.gid = i.user.id, i.guild_id
        seed(i.guild_id)

    async def interaction_check(self, i):
        if not owner(i) or i.user.id != self.uid or i.guild_id != self.gid:
            await i.response.send_message('沒有權限。', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='➕ 新增標籤／效果', style=discord.ButtonStyle.primary, row=0)
    async def add(self, i, b):
        await i.response.edit_message(content='🏷️ 先選分類，再輸入標籤名稱與加成條件。',
                                      view=TagCategoryView(i))

    @discord.ui.button(label='📋 標籤清單', style=discord.ButtonStyle.secondary, row=0)
    async def listing(self, i, b):
        with get_connection() as db:
            data = db.execute('SELECT name,category,enabled FROM rp_tag_library WHERE guild_id=? ORDER BY id LIMIT 45',
                              (str(i.guild_id),)).fetchall()
        content = '\n'.join(f"{x['name']}（{x['category']}）{'啟用' if x['enabled'] else '停用'}" for x in data)
        await i.response.send_message((content or '目前無標籤')[:1850], ephemeral=True)

    @discord.ui.button(label='✏️ 更名／啟停', style=discord.ButtonStyle.secondary, row=1)
    async def edit(self, i, b):
        await i.response.send_modal(EditTag())

    @discord.ui.button(label='⬅️ 返回內容建立', style=discord.ButtonStyle.secondary, row=2)
    async def back(self, i, b):
        from modules.content_builder import ContentView
        await i.response.edit_message(content='✍️ 內容建立中心', view=ContentView(i))


async def setup(bot: commands.Bot):
    pass
