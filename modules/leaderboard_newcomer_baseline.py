"""新人獎基準快照儲存：需正式啟用事件明確呼叫，不使用建立時間代替。"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from core.database import transaction, get_connection

def initialize():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS lb_v11_newcomer (
            guild_id TEXT NOT NULL, character_id TEXT NOT NULL,
            activated_utc TEXT NOT NULL, expires_utc TEXT NOT NULL,
            baseline_json TEXT NOT NULL, final_json TEXT,
            PRIMARY KEY(guild_id,character_id))''')

def register(guild_id, character_id, activated_at, official_stats):
    initialize()
    if activated_at.tzinfo is None:
        raise ValueError('啟用時間必須包含時區')
    activated = activated_at.astimezone(timezone.utc)
    if not isinstance(official_stats,dict) or not official_stats:
        raise ValueError('必須提供官方角色數值基準')
    clean = {str(k):float(v) for k,v in official_stats.items()}
    expires = activated+timedelta(days=30)
    with transaction() as db:
        db.execute('''INSERT OR IGNORE INTO lb_v11_newcomer
        (guild_id,character_id,activated_utc,expires_utc,baseline_json) VALUES(?,?,?,?,?)''',
        (str(guild_id),str(character_id),activated.isoformat(),expires.isoformat(),json.dumps(clean,ensure_ascii=False)))

def net_growth(guild_id,character_id,current_stats):
    initialize()
    with get_connection() as db:
        row=db.execute('SELECT baseline_json FROM lb_v11_newcomer WHERE guild_id=? AND character_id=?',(str(guild_id),str(character_id))).fetchone()
    if row is None:return None
    base=json.loads(row['baseline_json'])
    if set(base)!=set(current_stats):raise ValueError('新人獎數值欄位與啟用基準不一致')
    return sum(float(current_stats[k])-float(base[k]) for k in base)
