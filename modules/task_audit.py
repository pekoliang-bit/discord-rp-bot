"""Durable audit trail for administrative task edits, separated from player reward ledger."""
from __future__ import annotations
import json
from core.database import transaction,get_connection

def init_audit():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_task_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,
          actor_id TEXT NOT NULL,action TEXT NOT NULL,task_id INTEGER,
          details_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute('CREATE INDEX IF NOT EXISTS idx_rp_task_audit ON rp_task_audit(guild_id,id DESC)')

def log(guild_id,actor_id,action,task_id=None,details=None):
    init_audit()
    with transaction() as db:
        db.execute('INSERT INTO rp_task_audit(guild_id,actor_id,action,task_id,details_json) VALUES (?,?,?,?,?)',
                   (str(guild_id),str(actor_id),str(action),task_id,json.dumps(details or {},ensure_ascii=False)))

def recent(guild_id,limit=20):
    init_audit()
    with get_connection() as db:
        return [dict(r) for r in db.execute('SELECT * FROM rp_task_audit WHERE guild_id=? ORDER BY id DESC LIMIT ?', (str(guild_id),min(100,max(1,int(limit)))))]
