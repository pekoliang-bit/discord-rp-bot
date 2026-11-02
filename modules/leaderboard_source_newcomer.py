"""新人獎：只檢查正式啟用基準及快照條件，不以 created_at 冒充。"""
from __future__ import annotations
from core.database import get_connection
from modules.leaderboard_source_common import table_columns

EXCLUDED={'money','cash','bank','items','works','contracts'}

def inspect(guild_id=None):
    with get_connection() as db:
        character_cols=sorted(table_columns(db,'characters'))
        tables=[r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        candidate={t:sorted(table_columns(db,t)) for t in tables if any(x in t for x in ('activation','newcomer','snapshot'))}
    return {'status':'mapping_required','character_columns':character_cols,'candidate_tables':candidate,
            'rule':'正式啟用起30天；所有合格角色數值的淨變化直接加總；到期保留成績等待下次30天大結算',
            'warning':'不可用角色 created_at 或成就首次穿戴時間代替正式啟用時間'}

def query(guild_id,limit=100):
    return [],'unavailable'
