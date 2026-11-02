"""陣營榜唯讀介面：使用既有 faction_ledger 當期正式積分。"""
from __future__ import annotations
from core.database import get_connection
from modules.leaderboard_source_common import table_columns, ranked


def query(guild_id, limit=100):
    with get_connection() as db:
        required={'faction_groups':{'guild_id','id','name'},'faction_periods':{'guild_id','sequence'},'faction_ledger':{'guild_id','faction_id','period','points'}}
        if any(not cols <= table_columns(db,table) for table,cols in required.items()):
            return [],'unavailable'
        period=db.execute('SELECT sequence FROM faction_periods WHERE guild_id=?',(str(guild_id),)).fetchone()
        if period is None:
            return [],'unavailable'  # 只讀，不觸發 faction_core._roll 寫入
        rows=db.execute("""SELECT f.id,f.name,COALESCE(SUM(CASE WHEN l.points>0 THEN l.points ELSE 0 END),0) AS score
            FROM faction_groups f LEFT JOIN faction_ledger l ON l.guild_id=f.guild_id
            AND l.faction_id=f.id AND l.period=? WHERE f.guild_id=? GROUP BY f.id,f.name""",
            (period['sequence'],str(guild_id))).fetchall()
    data=[{'id':r['id'],'character_name':r['name'],'score':int(r['score'])} for r in rows]
    return (ranked(data,limit),'ready') if data else ([], 'empty')


def internal_rank(guild_id,faction_id,limit=100):
    """內部榜須由既有陣營權限介面檢查使用者資格後才可呼叫。"""
    with get_connection() as db:
        if any(not cols <= table_columns(db,t) for t,cols in {
            'faction_periods':{'guild_id','sequence'},'faction_ledger':{'guild_id','faction_id','period','character_id','points'},
            'characters':{'guild_id','id','name'}}.items()):return [],'unavailable'
        period=db.execute('SELECT sequence FROM faction_periods WHERE guild_id=?',(str(guild_id),)).fetchone()
        if not period:return [],'unavailable'
        rows=db.execute("""SELECT c.id AS character_id,c.name AS character_name,SUM(l.points) AS score
            FROM faction_ledger l JOIN characters c ON c.guild_id=l.guild_id AND c.id=l.character_id
            WHERE l.guild_id=? AND l.faction_id=? AND l.period=? AND l.points>0
            GROUP BY c.id,c.name""",(str(guild_id),int(faction_id),period['sequence'])).fetchall()
    return (ranked([dict(r) for r in rows],limit),'ready') if rows else ([], 'empty')
