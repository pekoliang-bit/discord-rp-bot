"""正式登記 CP 好感排行榜。只統計有效登記且雙方啟用的 CP，唯讀。"""
from __future__ import annotations
from core.database import get_connection
from modules.leaderboard_source_common import table_columns, ranked

REQUIRED_PAIRS={'guild_id','a','b'}
REQUIRED_SCORES={'guild_id','a','b','kind','points'}

def query(guild_id, limit=100):
    with get_connection() as db:
        if not REQUIRED_PAIRS <= table_columns(db,'rp_cp_pairs') or not REQUIRED_SCORES <= table_columns(db,'rp_relationship_scores'):
            return [], 'unavailable'
        if not {'id','name','guild_id','status'} <= table_columns(db,'characters'):
            return [], 'unavailable'
        rows=db.execute("""SELECT p.a,p.b,ca.name AS a_name,cb.name AS b_name,
             COALESCE(s.points,0) AS score
             FROM rp_cp_pairs p
             JOIN characters ca ON ca.guild_id=p.guild_id AND ca.id=p.a AND ca.status='active'
             JOIN characters cb ON cb.guild_id=p.guild_id AND cb.id=p.b AND cb.status='active'
             LEFT JOIN rp_relationship_scores s ON s.guild_id=p.guild_id AND s.a=p.a AND s.b=p.b AND s.kind='cp'
             WHERE p.guild_id=?""",(str(guild_id),)).fetchall()
    data=[{'id':f"{r['a']}:{r['b']}",'character_name':f"{r['a_name']} × {r['b_name']}",
           'a':r['a'],'b':r['b'],'score':int(r['score'])} for r in rows]
    return (ranked(data,limit),'ready') if data else ([], 'empty')
