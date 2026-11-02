"""14/30天結算；30天未配置獎勵時記錄不發獎，避免重複派發。"""
from __future__ import annotations
from core.database import transaction,get_connection
from modules.leaderboard_storage import initialize,audit
from modules.leaderboard_snapshot import collect
from modules.leaderboard_cycle import iso
from modules.leaderboard_award_policy import settle_without_prizes

def settle(guild_id,cycle,index,end_utc):
    if cycle not in ('14d','30d') or index<1:raise ValueError('無效結算週期')
    initialize()
    with get_connection() as db:
        old=db.execute('SELECT state FROM lb_v11_periods WHERE guild_id=? AND cycle=? AND idx=?',(str(guild_id),cycle,index)).fetchone()
    if old and old['state']=='snapshotted':
        if cycle == '30d':
            with get_connection() as db:
                saved = db.execute("""SELECT board,rank,entity_key FROM lb_v11_snapshots
                    WHERE guild_id=? AND cycle=? AND idx=?""",
                    (str(guild_id),cycle,index)).fetchall()
            grouped = {}
            for row in saved:
                grouped.setdefault(row['board'], []).append(
                    {'rank':row['rank'],'entity_key':row['entity_key']})
            for board, rows in grouped.items():
                settle_without_prizes(guild_id,cycle,index,board,rows)
        return {'status':'already','cycle':cycle,'index':index,'unavailable':[]}
    snapshots,unavailable=collect(guild_id)
    with transaction() as db:
        db.execute('INSERT OR IGNORE INTO lb_v11_periods(guild_id,cycle,idx,end_utc) VALUES(?,?,?,?)',(str(guild_id),cycle,index,iso(end_utc)))
        for board,rows in snapshots.items():
            for rank,entity,name,score in rows:
                db.execute('INSERT OR IGNORE INTO lb_v11_snapshots(guild_id,cycle,idx,board,rank,entity_key,display_name,score) VALUES(?,?,?,?,?,?,?,?)',(str(guild_id),cycle,index,board,rank,entity,name,score))
        db.execute("UPDATE lb_v11_periods SET state='snapshotted' WHERE guild_id=? AND cycle=? AND idx=?",(str(guild_id),cycle,index))
    if cycle=='30d':
        for board,rows in snapshots.items():
            settle_without_prizes(guild_id,cycle,index,board,[{'rank':rank,'entity_key':entity} for rank,entity,_,_ in rows])
    audit(guild_id,'snapshot',{'cycle':cycle,'index':index,'unavailable':unavailable})
    return {'status':'snapshotted','cycle':cycle,'index':index,'boards':len(snapshots),'unavailable':unavailable}
