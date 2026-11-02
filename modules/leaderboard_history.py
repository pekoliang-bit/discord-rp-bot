"""唯讀歷史查詢。正式 12 個月歸檔政策另需備份流程。"""
from core.database import get_connection
from modules.leaderboard_storage import initialize

def periods(guild_id,limit=30):
    initialize()
    with get_connection() as db:
        rows=db.execute('SELECT cycle,idx,end_utc,state FROM lb_v11_periods WHERE guild_id=? ORDER BY end_utc DESC LIMIT ?',(str(guild_id),max(1,min(limit,100)))).fetchall()
    return [dict(x) for x in rows]
def top(guild_id,cycle,index,board,max_rank=10):
    initialize()
    with get_connection() as db:
        rows=db.execute('SELECT rank,entity_key,display_name,score FROM lb_v11_snapshots WHERE guild_id=? AND cycle=? AND idx=? AND board=? AND rank<=? ORDER BY rank,entity_key',(str(guild_id),cycle,index,board,max_rank)).fetchall()
    return [dict(x) for x in rows]
