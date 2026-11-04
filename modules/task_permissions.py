"""Task-specific authorized managers. Owner can grant or revoke; no new slash commands."""
from __future__ import annotations
from core.database import transaction,get_connection

def init_permissions():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_task_manager_grants (
          guild_id TEXT NOT NULL,user_id TEXT NOT NULL, granted_by TEXT NOT NULL,
          granted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(guild_id,user_id))""")

def has_permission(guild_id,user_id,owner_id):
    if str(user_id)==str(owner_id):return True
    init_permissions()
    with get_connection() as db:
        return db.execute('SELECT 1 FROM rp_task_manager_grants WHERE guild_id=? AND user_id=?',(str(guild_id),str(user_id))).fetchone() is not None

def grant(guild_id,owner_id,actor_id,target_id,enabled):
    if str(actor_id)!=str(owner_id):raise PermissionError('只有群主能授權任務管理員')
    init_permissions()
    if str(target_id)==str(owner_id):raise ValueError('群主無須額外授權')
    with transaction() as db:
        if enabled:db.execute('INSERT OR REPLACE INTO rp_task_manager_grants(guild_id,user_id,granted_by) VALUES (?,?,?)',(str(guild_id),str(target_id),str(actor_id)))
        else:db.execute('DELETE FROM rp_task_manager_grants WHERE guild_id=? AND user_id=?',(str(guild_id),str(target_id)))

def authorized(i):
    return bool(i.guild and i.user and has_permission(i.guild_id,i.user.id,i.guild.owner_id))
