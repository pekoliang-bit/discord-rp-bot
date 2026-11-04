"""Review submitted tasks; approval records completion, never claims to have paid rewards."""
from __future__ import annotations
from core.database import transaction,get_connection
from modules.task_repository import initialize
from modules.task_permissions import has_permission
from modules.task_audit import log

def pending(guild_id,limit=20):
    initialize()
    with get_connection() as db:
        return [dict(row) for row in db.execute("""SELECT p.guild_id,p.character_id,p.task_id,p.cycle_key,p.accepted_at,
        d.title FROM rp_task_progress p JOIN rp_task_definitions d ON d.id=p.task_id AND d.guild_id=p.guild_id
        WHERE p.guild_id=? AND p.status='submitted' ORDER BY p.accepted_at LIMIT ?""", (str(guild_id),min(50,max(1,int(limit)))))]

def decide(guild_id,actor_id,owner_id,character_id,task_id,cycle_key,approved):
    if not has_permission(guild_id,actor_id,owner_id):raise PermissionError('沒有任務審核權限')
    initialize()
    with transaction() as db:
        changed=db.execute("""UPDATE rp_task_progress SET status=?, completed_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END
        WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key=? AND status='submitted' """,
        ('completed' if approved else 'accepted',int(bool(approved)),str(guild_id),int(character_id),int(task_id),str(cycle_key)))
        if changed.rowcount!=1:raise ValueError('任務不存在、已審核或狀態不符')
    log(guild_id,actor_id,'approve' if approved else 'reject',task_id,{'character_id':character_id,'cycle_key':cycle_key})
    return True
