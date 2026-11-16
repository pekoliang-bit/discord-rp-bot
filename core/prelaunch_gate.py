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


LOCKED_TEXT = ('🔒 本群尚未對一般玩家正式開放。\n'
               '群主及具管理權限的人員可使用完整的管理／設定功能，'
               '先完成頻道綁定、世界觀與各系統檢查。\n'
               '一般玩家需等待群主在 /官方 → 🚪 正式開群 確認後才能使用。')


def is_management_actor(interaction):
    """Only server owner or Discord-level administrative privilege bypasses launch.

    Ordinary members, including users with only moderator/reviewer bot roles,
    remain subject to the player launch lock.  Guild owner always bypasses.
    """
    guild = getattr(interaction, 'guild', None)
    actor = getattr(interaction, 'user', None)
    if guild is None or actor is None:
        return False
    if str(getattr(actor, 'id', '')) == str(getattr(guild, 'owner_id', '')):
        return True
    permissions = getattr(actor, 'guild_permissions', None)
    return bool(getattr(permissions, 'administrator', False))


def is_beta_player(interaction):
    """內測玩家僅能在封群期間進入遊玩指令；官方操作另須管理權限。"""
    member = getattr(interaction, 'user', None)
    if getattr(interaction, 'guild', None) is None or member is None:
        return False
    return any(getattr(role, 'name', '') == '內測玩家'
               for role in getattr(member, 'roles', ()))


def may_use_system(interaction):
    """Launch protects players, not server owners/administrators."""
    return is_open(getattr(interaction, 'guild_id', None)) or is_management_actor(interaction) or is_beta_player(interaction)


def allow_component(interaction, item=None, modal=None):
    """Keep old buttons and modals available to administrators before launch.

    Per-feature permission checks within each view/modal remain authoritative;
    this only controls whether launch status blocks a UI interaction.
    """
    return may_use_system(interaction)
