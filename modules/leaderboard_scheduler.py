"""可供既有排程呼叫的週期檢查；預設不啟動、不追溯過期獎勵。"""
from modules.leaderboard_config import get_config
from modules.leaderboard_cycle import parse,utc_now,cycle_end
from modules.leaderboard_settlement import settle
from core.database import get_connection

def run_due(guild_id,now=None,max_periods=2):
    config=get_config(guild_id)
    if not config or not config['enabled']:return []
    anchor=parse(config['anchor_utc']);now=now or utc_now();out=[]
    for cycle,days in [('14d',14),('30d',30)]:
        due=max(0,int((now-anchor).total_seconds()//(days*86400)))
        with get_connection() as db:
            row=db.execute("SELECT COALESCE(MAX(idx),0) AS n FROM lb_v11_periods WHERE guild_id=? AND cycle=? AND state='snapshotted'",(str(guild_id),cycle)).fetchone()
        first=int(row['n'])+1
        for index in range(first,min(due,first+max_periods-1)+1):
            # 過期週期只能使用目前資料，不能偽裝為當時快照；跳過並交人工處理。
            end=cycle_end(anchor,days,index)
            if (now-end).total_seconds()>3600:out.append({'cycle':cycle,'index':index,'status':'requires_historical_recovery'});break
            out.append({'cycle':cycle,'index':index,**settle(guild_id,cycle,index,end)})
    return out
