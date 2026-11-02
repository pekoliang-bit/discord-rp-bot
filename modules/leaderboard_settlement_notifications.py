"""沿用既有通知佇列；每榜前十含並列。"""
from core.database import get_connection
from modules.leaderboard_storage import initialize
from modules.leaderboard_catalog import BOARD_NAMES
from modules.leaderboard_notifications import queue_rank_summary

def queue_period_summaries(guild_id,cycle,index):
    initialize();sent=[]
    with get_connection() as db:
        period=db.execute('SELECT state FROM lb_v11_periods WHERE guild_id=? AND cycle=? AND idx=?',(str(guild_id),cycle,index)).fetchone()
        if not period or period['state']!='snapshotted':raise ValueError('結算尚未封存')
        boards=db.execute('SELECT DISTINCT board FROM lb_v11_snapshots WHERE guild_id=? AND cycle=? AND idx=?',(str(guild_id),cycle,index)).fetchall()
        data={r['board']:db.execute('SELECT rank,display_name,score FROM lb_v11_snapshots WHERE guild_id=? AND cycle=? AND idx=? AND board=? AND rank<=10 ORDER BY rank,entity_key',(str(guild_id),cycle,index,r['board'])).fetchall() for r in boards}
    for board,rows in data.items():
        lines=[f"#{r['rank']} {r['display_name']}｜{r['score']:g}" for r in rows]
        if queue_rank_summary(guild_id,f'{cycle}:{index}',cycle,BOARD_NAMES.get(board,board),lines):sent.append(board)
    return sent
