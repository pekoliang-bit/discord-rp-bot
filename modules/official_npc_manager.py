"""官方 NPC 管理：只管理已登記的官方 NPC，不變更角色歸屬。"""
from __future__ import annotations

import sqlite3
import discord
from discord.ext import commands
from core.database import get_connection, transaction


def is_owner(i: discord.Interaction) -> bool:
    return i.guild is not None and i.guild.owner_id == i.user.id


async def send(i: discord.Interaction, text: str):
    if i.response.is_done():
        await i.followup.send(text[:1900], ephemeral=True)
    else:
        await i.response.send_message(text[:1900], ephemeral=True)


def safe(value):
    return discord.utils.escape_mentions(str(value))


def get_npc(gid, npc_id):
    with get_connection() as db:
        row = db.execute(
            """SELECT id,name,owner_id,status,character_type,created_at
               FROM characters WHERE guild_id=? AND id=? AND character_type='official_npc'""",
            (str(gid), int(npc_id)),
        ).fetchone()
    if row is None:
        raise ValueError('找不到這名官方 NPC。')
    return dict(row)


def list_npcs(gid):
    with get_connection() as db:
        rows = db.execute(
            """SELECT id,name,owner_id,status FROM characters
               WHERE guild_id=? AND character_type='official_npc' AND status!='deleted'
               ORDER BY id DESC LIMIT 35""",
            (str(gid),),
        ).fetchall()
    if not rows:
        return '目前沒有已登記的官方 NPC。請先使用 `/角色單登記` 登記官方 NPC。'
    labels = {'approved':'已核准','active':'已啟用','retired':'已停用','draft':'草稿',
              'review_1':'一審中','review_2':'二審中'}
    return '\n'.join(
        f"#{row['id']}｜{safe(row['name'])}｜{labels.get(row['status'],row['status'])}｜擁有者 <@{row['owner_id']}>"
        for row in rows
    )


def update_npc(gid, npc_id, *, name=None, status=None):
    if name is None and status is None:
        raise ValueError('沒有要修改的資料。')
    with transaction() as db:
        row = db.execute(
            """SELECT id,status FROM characters WHERE guild_id=? AND id=?
               AND character_type='official_npc'""",
            (str(gid), int(npc_id)),
        ).fetchone()
        if row is None:
            raise ValueError('找不到這名官方 NPC。')
        if row['status'] not in ('active', 'approved', 'retired'):
            raise ValueError('此 NPC 尚未通過登記／審核，不能在此啟用或修改。')
        if name is not None:
            name = name.strip()
            if not 1 <= len(name) <= 100:
                raise ValueError('名稱長度必須介於 1～100 字。')
            db.execute('UPDATE characters SET name=?,version=version+1 WHERE guild_id=? AND id=?',
                       (name, str(gid), int(npc_id)))
        if status is not None:
            if status not in ('active', 'retired'):
                raise ValueError('只能設定為「啟用」或「停用」。')
            db.execute('UPDATE characters SET status=?,version=version+1 WHERE guild_id=? AND id=?',
                       (status, str(gid), int(npc_id)))


class NpcDetail(discord.ui.Modal, title='👑 查看官方 NPC'):
    npc_id = discord.ui.TextInput(label='角色編號', max_length=15)

    async def on_submit(self, i):
        if not is_owner(i):
            return await send(i, '⛔ 僅限群主操作。')
        try:
            row = get_npc(i.guild_id, int(self.npc_id.value))
            with get_connection() as db:
                sheet = db.execute(
                    """SELECT sheet_url,registered_by FROM character_sheet_links
                       WHERE guild_id=? AND character_id=?""",
                    (str(i.guild_id), row['id']),
                ).fetchone() if _table_exists(db, 'character_sheet_links') else None
                stories = db.execute(
                    """SELECT id,title,status FROM rp_stories
                       WHERE guild_id=? AND character_id=? ORDER BY id DESC LIMIT 5""",
                    (str(i.guild_id), row['id']),
                ).fetchall() if _table_exists(db, 'rp_stories') else []
            txt = (f"👑 **{safe(row['name'])}**（#{row['id']}）\n"
                   f"狀態：{row['status']}\n角色歸屬：<@{row['owner_id']}>\n"
                   f"角色單：{sheet['sheet_url'] if sheet else '尚未建立連結紀錄'}\n"
                   f"最近的劇情：" + ('、'.join(f"#{s['id']} {safe(s['title'])}" for s in stories) if stories else '無'))
            await send(i, txt)
        except (ValueError, sqlite3.Error) as exc:
            await send(i, f'⚠️ {exc}')


def _table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


class NpcRename(discord.ui.Modal, title='✏️ 修改官方 NPC 名稱'):
    npc_id = discord.ui.TextInput(label='官方 NPC 角色編號', max_length=15)
    new_name = discord.ui.TextInput(label='新名稱', max_length=100)

    async def on_submit(self, i):
        if not is_owner(i):
            return await send(i, '⛔ 僅限群主操作。')
        try:
            update_npc(i.guild_id, int(self.npc_id.value), name=str(self.new_name.value))
            await send(i, f'✅ 官方 NPC #{self.npc_id.value} 名稱已更新。角色擁有者不變。')
        except (ValueError, sqlite3.Error) as exc:
            await send(i, f'⚠️ {exc}')


class NpcState(discord.ui.Modal, title='⚙️ 啟用／停用官方 NPC'):
    npc_id = discord.ui.TextInput(label='官方 NPC 角色編號', max_length=15)
    action = discord.ui.TextInput(label='操作：啟用 或 停用', max_length=2)

    async def on_submit(self, i):
        if not is_owner(i):
            return await send(i, '⛔ 僅限群主操作。')
        try:
            action = str(self.action.value).strip()
            if action not in ('啟用','停用'):
                raise ValueError('操作只能填「啟用」或「停用」。')
            update_npc(i.guild_id, int(self.npc_id.value), status='active' if action=='啟用' else 'retired')
            await send(i, f'✅ 官方 NPC #{self.npc_id.value} 已{action}。不影響角色歸屬或既有劇情進度。')
        except (ValueError, sqlite3.Error) as exc:
            await send(i, f'⚠️ {exc}')


class OfficialNpcView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.user_id = i.user.id
        self.guild_id = i.guild_id

    async def interaction_check(self, i):
        if i.user.id != self.user_id or i.guild_id != self.guild_id or not is_owner(i):
            await send(i, '⛔ 這不是你的管理面板。')
            return False
        return True

    @discord.ui.button(label='📋 官方 NPC 清單', style=discord.ButtonStyle.primary, row=0)
    async def listing(self, i, button):
        await send(i, list_npcs(i.guild_id))

    @discord.ui.button(label='🔎 查看角色資料', style=discord.ButtonStyle.secondary, row=0)
    async def detail(self, i, button):
        await i.response.send_modal(NpcDetail())

    @discord.ui.button(label='✏️ 修改 NPC 名稱', style=discord.ButtonStyle.secondary, row=1)
    async def rename(self, i, button):
        await i.response.send_modal(NpcRename())

    @discord.ui.button(label='⚙️ 啟用／停用', style=discord.ButtonStyle.secondary, row=1)
    async def toggle(self, i, button):
        await i.response.send_modal(NpcState())

    @discord.ui.button(label='↩️ 返回內容建立', style=discord.ButtonStyle.secondary, row=2)
    async def back(self, i, button):
        from modules.content_builder import ContentView
        await i.response.edit_message(content='✍️ **內容建立中心**', view=ContentView(i))


async def setup(bot: commands.Bot):
    # 只有 UI 元件，保留入口避免自動載入時拋 NoEntryPointError。
    pass
