
"""網路影響榜：帳號級計分，未有核准欄位映射前禁止猜測。"""
from __future__ import annotations
from core.database import get_connection
from modules.leaderboard_source_common import table_columns

REQUIRED={'guild_id','id','name','followers'}

def inspect(guild_id=None):
    with get_connection() as db:
        tables=[r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        candidates={t:sorted(table_columns(db,t)) for t in tables if any(s in t.lower() for s in ('social','account','internet'))}
    return {'status':'mapping_required','candidate_tables':candidates,
            'formula':'30天流量40% + 30天互動30% + 當前追蹤數30%',
            'unit':'每個獨立社群帳號','note':'需確認帳號ID、時間窗、排除自刷及固定標準化基準'}

def query(guild_id,limit=100):
    return [],'unavailable'
