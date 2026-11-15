"""Per-guild opt-in launch gate; missing record always means CLOSED."""
from __future__ import annotations
import sqlite3
from core.database import get_connection, transaction

DDL = """CREATE TABLE IF NOT EXISTS rp_guild_launch_v1 (
 guild_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
 opened_by TEXT, opened_at TEXT,
 CHECK(enabled IN (0,1))
)"""


def is_open(guild_id):
    if guild_id is None:
        return False
    with get_connection() as db:
        try:
            row = db.execute('SELECT enabled FROM rp_guild_launch_v1 WHERE guild_id=?',
                             (str(guild_id),)).fetchone()
        except sqlite3.OperationalError as exc:
            if 'no such table' in str(exc):
                return False
            raise
        return bool(row and row['enabled'] == 1)


def set_open(guild_id, actor_id, guild_owner_id, enabled=True):
    if str(actor_id) != str(guild_owner_id):
        raise PermissionError('只有 Discord 伺服器擁有者能更改正式開群狀態。')
    if not guild_id:
        raise ValueError('請在 Discord 伺服器內操作。')
    with transaction() as db:
        db.execute(DDL)
        db.execute('''INSERT INTO rp_guild_launch_v1(guild_id,enabled,opened_by,opened_at)
          VALUES (?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)
          ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled,
          opened_by=excluded.opened_by,opened_at=excluded.opened_at''',
          (str(guild_id),int(bool(enabled)),str(actor_id),int(bool(enabled))))
    return bool(enabled)


LOCKED_TEXT = ('🔒 本群尚未正式開啟機器人功能。\n'
               '目前僅開放管理員透過 /登記 匯入「玩家 NPC／官方 NPC」人設。\n'
               '群主可使用 /官方 → 🚪 正式開群 進行檢查及確認開啟。')

NPC_ITEMS = frozenset({'NPCRegisterButton','NPCImportKindSelect','NPCOwnerMemberSelect'})
NPC_MODALS = frozenset({'ImportNPCModal'})
LAUNCH_ITEMS = frozenset({'LaunchAuditButton','LaunchConfirmButton'})


def allow_component(interaction, item=None, modal=None):
    if is_open(getattr(interaction,'guild_id',None)):
        return True
    guild = getattr(interaction,'guild',None)
    actor = getattr(interaction,'user',None)
    if guild is None or actor is None:
        return False
    owner = int(actor.id) == int(guild.owner_id)
    admin = owner or bool(getattr(getattr(actor,'guild_permissions',None),'administrator',False))
    if modal is not None:
        return admin and type(modal).__name__ in NPC_MODALS
    if item is None:
        return False
    # Discord's @ui.button decorator creates a generic Button rather than a
    # named subclass. Authorize only the two owner-mode buttons in this view.
    parent=getattr(item,'view',None)
    if admin and type(parent).__name__=='NPCImportOwnerModeView':
        return getattr(item,'label',None) in ('🙋 本人登記','👥 管理員代登記')
    kind = type(item).__name__
    if kind in LAUNCH_ITEMS:
        return owner
    if kind in NPC_ITEMS:
        return admin
    return False
