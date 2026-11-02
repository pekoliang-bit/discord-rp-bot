"""將已封存排行榜的前十名放進既有通知佇列。重試依 dedupe_key 去重。"""
from __future__ import annotations
from core.database import get_connection, transaction
from modules.leaderboard_storage import initialize
from modules.leaderboard_settlement_notifications import queue_period_summaries

def deliver_period(guild_id, cycle, index):
    initialize()
    with get_connection() as db:
        row = db.execute('SELECT state FROM lb_v11_periods WHERE guild_id=? AND cycle=? AND idx=?', (str(guild_id), cycle, index)).fetchone()
    if row is None or row['state'] != 'snapshotted':
        return {'status': 'not_ready', 'queued': []}
    queued = queue_period_summaries(guild_id, cycle, index)
    with transaction() as db:
        for board in queued:
            db.execute("INSERT INTO lb_v11_delivery(guild_id,cycle,idx,board,kind,entity_key,state) VALUES(?,?,?,?,?,'','queued') ON CONFLICT(guild_id,cycle,idx,board,kind,entity_key) DO UPDATE SET state='queued',updated_utc=CURRENT_TIMESTAMP", (str(guild_id), cycle, index, board, 'main'))
    return {'status': 'queued', 'queued': queued}
