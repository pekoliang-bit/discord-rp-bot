"""保留近12個月完整快照；舊期保留前十與正式獎勵、稽核紀錄。"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from core.database import transaction
from modules.leaderboard_storage import initialize

def prune_details(guild_id, now=None):
    initialize()
    now = now or datetime.now(timezone.utc)
    cutoff = (now-timedelta(days=365)).isoformat(timespec='seconds')
    with transaction() as db:
        cur = db.execute('''DELETE FROM lb_v11_snapshots
          WHERE guild_id=? AND rank>10 AND EXISTS (
          SELECT 1 FROM lb_v11_periods p WHERE p.guild_id=lb_v11_snapshots.guild_id
          AND p.cycle=lb_v11_snapshots.cycle AND p.idx=lb_v11_snapshots.idx
          AND p.end_utc < ?)''',(str(guild_id),cutoff))
        return cur.rowcount
