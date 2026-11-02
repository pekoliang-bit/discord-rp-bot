"""結算快照。由受控工作程序呼叫；不自行註冊 slash 指令。"""
from core.database import transaction,get_connection
from modules.leaderboard_storage import initialize,audit
from modules.leaderboard_snapshot import collect
from modules.leaderboard_cycle import iso

def settle(guild_id,cycle,index,end_utc):
    if cycle not in ('14d','30d') or index<1:raise ValueError('無效結算週期')
    initialize();snapshots,unavailable=collect(guild_id)
    with transaction() as db:
        existing=db.execute('SELECT state FROM lb_v11_periods WHERE guild_id=? AND cycle=? AND idx=?',(str(guild_id),cycle,index)).fetchone()
        if existing and existing['state']=='snapshotted':return {'status':'already','unavailable':unavailable}
        db.execute("INSERT OR IGNORE INTO lb_v11_periods(guild_id,cycle,idx,end_utc) VALUES(?,?,?,?)",(str(guild_id),cycle,index,iso(end_utc)))
        for board,rows in snapshots.items():
            for rank,entity,name,score in rows:
                db.execute('INSERT OR IGNORE INTO lb_v11_snapshots(guild_id,cycle,idx,board,rank,entity_key,display_name,score) VALUES(?,?,?,?,?,?,?,?)',(str(guild_id),cycle,index,board,rank,entity,name,score))
        db.execute("UPDATE lb_v11_periods SET state='snapshotted' WHERE guild_id=? AND cycle=? AND idx=?",(str(guild_id),cycle,index))
    audit(guild_id,'snapshot',{'cycle':cycle,'index':index,'unavailable':unavailable})
    return {'status':'snapshotted','boards':len(snapshots),'unavailable':unavailable}
