""""正式開群前的批次角色標記刪除管理面板。

安全策略：只更新角色為 deleted，保留關聯資料與操作稽核；不執行危險的連鎖硬刪除。
僅群主或 Administrator 可用；已正式開群後完全禁止。
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)
PAGE_SIZE = 20
MAX_BATCH = 100

DDL = """CREATE TABLE IF NOT EXISTS rp_prelaunch_character_deletions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 guild_id TEXT NOT NULL,
 character_id INTEGER NOT NULL,
 character_name TEXT NOT NULL,
 owner_id TEXT NOT NULL,
 old_status TEXT NOT NULL,
 actor_id TEXT NOT NULL,
 deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE (guild_id, character_id)
)"""


def _setup(db):
    db.execute(DDL)


def _assert_prelaunch(db, guild_id):
    try:
        row = db.execute(
            'SELECT enabled FROM rp_guild_launch_v1 WHERE guild_id=?',
            (str(guild_id),),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc).lower():
            raise
        row = None
    if row and int(row['enabled']) != 0:
        raise ValueError('本伺服器已正式開群，開群前角色清理功能已永久鎖定。')


def list_page(guild_id: int | str, page: int = 0):
    """僅顯示尚未刪除的角色；各種角色類別均可明確勾選。"""
    gid = str(guild_id)
    with get_connection() as db:
        _assert_prelaunch(db, gid)
        count = db.execute(
            "SELECT COUNT(*) AS n FROM characters WHERE guild_id=? AND status<>'deleted'",
            (gid,),
        ).fetchone()['n']
        total_pages = max(1, (int(count) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(int(page), total_pages - 1))
        rows = db.execute(
            """SELECT id,name,owner_id,character_type,status,created_at
               FROM characters WHERE guild_id=? AND status<>'deleted'
               ORDER BY id ASC LIMIT ? OFFSET ?""",
            (gid, PAGE_SIZE, page * PAGE_SIZE),
        ).fetchall()
    return [dict(r) for r in rows], page, total_pages, int(count)


def delete_selected(guild_id: int | str, actor_id: int | str, selected: list[int]):
    """原子批次標記刪除。先檢查全批是否有效；任何錯誤全部回滾。"""
    gid, actor = str(guild_id), str(actor_id)
    ids = [int(x) for x in selected]
    if not ids or len(ids) > MAX_BATCH or len(ids) != len(set(ids)) or any(x <= 0 for x in ids):
        raise ValueError(f'請選擇1～{MAX_BATCH}個不重複角色。')
    placeholders = ','.join('?' for _ in ids)
    with transaction() as db:
        _assert_prelaunch(db, gid)
        _setup(db)
        rows = db.execute(
            f"""SELECT id,name,owner_id,status FROM characters
                WHERE guild_id=? AND status<>'deleted' AND id IN ({placeholders})""",
            (gid, *ids),
        ).fetchall()
        if len(rows) != len(ids):
            raise ValueError('所選角色已變更、已被刪除或不屬於本伺服器。請重新整理後再操作。')
        # 已產生轉正草稿的測試角色，需先處理草稿的關聯。
        try:
            linked = db.execute(
                f"""SELECT character_id FROM rp_beta_characters
                    WHERE guild_id=? AND draft_id IS NOT NULL AND character_id IN ({placeholders})""",
                (gid, *ids),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if 'no such table' not in str(exc).lower():
                raise
            linked = []
        if linked:
            raise ValueError('所選角色含已建立轉正草稿的測試角，為避免資料錯置，本次整批未刪除。')
        for row in rows:
            db.execute(
                """INSERT INTO rp_prelaunch_character_deletions
                   (guild_id,character_id,character_name,owner_id,old_status,actor_id)
                   VALUES (?,?,?,?,?,?)""",
                (gid, row['id'], row['name'], row['owner_id'], row['status'], actor),
            )
            db.execute(
                """UPDATE characters SET status='deleted',version=version+1
                   WHERE guild_id=? AND id=? AND status<>'deleted'""",
                (gid, row['id']),
            )
            # Deleted official NPCs must be able to register from the same sheet again.
            # Preserve old links/templates in an audit table before freeing unique keys.
            sheet = db.execute(
                'SELECT message_id FROM character_sheet_links WHERE guild_id=? AND character_id=?',
                (gid, row['id']),
            ).fetchone()
            if sheet:
                from modules.npc_history_v23 import release_deleted_official_sheet, init as npc_init
                npc_init(db)
                release_deleted_official_sheet(db, gid, sheet['message_id'])
            # 測試角色不再可編輯／領取。移除其 ACTIVE 映射，不清除沙盒歷史。
            try:
                db.execute(
                    'DELETE FROM rp_beta_characters WHERE guild_id=? AND character_id=?',
                    (gid, row['id']),
                )
            except sqlite3.OperationalError as exc:
                if 'no such table' not in str(exc).lower():
                    raise
        return len(rows)


def is_staff(interaction: discord.Interaction) -> bool:
    guild, user = interaction.guild, interaction.user
    if guild is None or user is None:
        return False
    if user.id == guild.owner_id:
        return True
    permissions = getattr(user, 'guild_permissions', None)
    return bool(getattr(permissions, 'administrator', False))


def _display(rows, page, pages, total, selected):
    return (
        '🧹 **開群前角色清理**\n'
        '勾選角色後按「確認選取」，可翻頁繼續選擇，最後按「刪除所選」。\n'
        '⚠️ 此操作會將角色標記為 deleted，**不是清空資料庫**；保留關聯資料及刪除紀錄供稽核。'
        '正式開群後不可使用。\n'
        f'第 {page+1}/{pages} 頁｜有效角色 {total}｜已勾選 {len(selected)}（最多{MAX_BATCH}）\n'
        + ('\n'.join(
            f"`#{r['id']}` {discord.utils.escape_markdown(str(r['name']))[:70]}｜{r['character_type']}｜{r['status']}"
            for r in rows
        ) if rows else '目前沒有可清理的角色。')
    )[:3900]


class RoleSelect(discord.ui.Select):
    def __init__(self, rows, selected):
        opts = [discord.SelectOption(
            label=f"#{r['id']} {str(r['name'])}"[:100],
            value=str(r['id']),
            description=f"{r['character_type']}｜{r['status']}｜擁有者 {r['owner_id']}"[:100],
            default=int(r['id']) in selected,
        ) for r in rows]
        super().__init__(placeholder='勾選本頁要保留的選取項目', min_values=0,
                         max_values=len(opts), options=opts, row=0)

    async def callback(self, interaction):
        view: CleanupView = self.view
        page_ids = {int(r['id']) for r in view.rows}
        values = {int(x) for x in self.values}
        updated = (view.selected - page_ids) | values
        if len(updated) > MAX_BATCH:
            return await interaction.response.send_message(f'最多選{MAX_BATCH}個角色。', ephemeral=True)
        view.selected = updated
        await view.render(interaction)


class CleanupView(discord.ui.View):
    def __init__(self, interaction, rows, page, pages, total, selected=None):
        super().__init__(timeout=600)
        self.uid = interaction.user.id
        self.gid = interaction.guild_id
        self.rows = rows
        self.page = page
        self.pages = pages
        self.total = total
        self.selected = set(selected or ())
        if rows:
            self.add_item(RoleSelect(rows, self.selected))
        self.previous.disabled = page <= 0
        self.next_page.disabled = page >= pages - 1
        self.delete.disabled = not self.selected

    async def interaction_check(self, interaction):
        if (interaction.guild_id != self.gid or interaction.user.id != self.uid
                or not is_staff(interaction)):
            await interaction.response.send_message('⛔ 只有開啟此面板的群主／管理員可操作。', ephemeral=True)
            return False
        return True

    async def render(self, interaction, *, page=None):
        try:
            rows, p, pages, total = await asyncio.to_thread(
                list_page, self.gid, self.page if page is None else page,
            )
        except (ValueError, sqlite3.Error) as exc:
            return await interaction.response.edit_message(content=f'⚠️ {exc}', view=None)
        self.stop()
        new = CleanupView(interaction, rows, p, pages, total, self.selected)
        await interaction.response.edit_message(
            content=_display(rows, p, pages, total, new.selected), view=new,
        )

    @discord.ui.button(label='◀ 上一頁', style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction, button):
        await self.render(interaction, page=self.page-1)

    @discord.ui.button(label='下一頁 ▶', style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction, button):
        await self.render(interaction, page=self.page+1)

    @discord.ui.button(label='🗑️ 刪除所選', style=discord.ButtonStyle.danger, row=2)
    async def delete(self, interaction, button):
        chosen = sorted(self.selected)
        if not chosen:
            return await interaction.response.send_message('請先勾選角色。', ephemeral=True)
        await interaction.response.send_message(
            f'⚠️ **二次確認：將標記刪除 {len(chosen)} 個角色**\n'
            f'編號：{", ".join(map(str, chosen))[:1200]}\n'
            '角色將退出有效清單，關聯資料保留供稽核。此操作不能從面板還原。\n'
            'Discord 舊頻道不會自動刪除；重新登記請發送一則新的角色單訊息。',
            view=ConfirmCleanup(self.uid, self.gid, chosen), ephemeral=True,
        )

    @discord.ui.button(label='🔄 重新整理', style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction, button):
        await self.render(interaction)


class ConfirmCleanup(discord.ui.View):
    def __init__(self, uid, gid, ids):
        super().__init__(timeout=180)
        self.uid, self.gid, self.ids = uid, gid, tuple(ids)
        self.done = False

    async def interaction_check(self, interaction):
        if interaction.guild_id != self.gid or interaction.user.id != self.uid or not is_staff(interaction):
            await interaction.response.send_message('⛔ 沒有操作權限。', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='確認標記刪除', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.done:
            return await interaction.response.send_message('此操作已執行。', ephemeral=True)
        self.done = True
        await interaction.response.defer(ephemeral=True)
        try:
            count = await asyncio.to_thread(delete_selected, self.gid, self.uid, list(self.ids))
            for item in self.children:
                item.disabled = True
            await interaction.edit_original_response(content=(f'✅ 已標記刪除 {count} 個角色；稽核資料已保留。\n'
                     '請手動核對並刪除錯誤的 Discord 舊頻道，再用新角色單訊息重新登記。'), view=self)
        except (ValueError, sqlite3.Error) as exc:
            self.done = False
            await interaction.edit_original_response(content=f'⚠️ 未刪除任何角色：{exc}', view=None)
        except Exception:
            LOG.exception('開群前角色清理失敗')
            self.done = False
            await interaction.edit_original_response(content='⚠️ 系統異常，未確認刪除結果。請檢查 Railway Logs。', view=None)

    @discord.ui.button(label='取消', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content='已取消，未刪除任何角色。', view=None)


class PrelaunchCleanupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='🧹 開群前角色清理', style=discord.ButtonStyle.danger, row=2)

    async def callback(self, interaction):
        if not is_staff(interaction):
            return await interaction.response.send_message('⛔ 需要群主或 Administrator 權限。', ephemeral=True)
        try:
            rows, page, pages, total = await asyncio.to_thread(list_page, interaction.guild_id)
        except (ValueError, sqlite3.Error) as exc:
            return await interaction.response.send_message(f'⚠️ {exc}', ephemeral=True)
        await interaction.response.send_message(
            _display(rows, page, pages, total, set()),
            view=CleanupView(interaction, rows, page, pages, total), ephemeral=True,
        )
