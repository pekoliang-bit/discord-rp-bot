"""將查詢結果封存；無法計算的榜單不製造名次。"""
from modules.leaderboard_catalog import BOARDS
from modules.leaderboard_service import leaderboard

def collect(guild_id):
    result={};unavailable=[]
    for key,_ in BOARDS:
        rows,status=leaderboard(guild_id,key,limit=500)
        if status not in ('ready','empty'):
            unavailable.append(key);continue
        entries=[]
        for row in rows:
            entity=str(row.get('character_id',row.get('entity_id',row.get('id',''))))
            if not entity:continue
            entries.append((int(row['rank']),entity,str(row.get('character_name',row.get('name',entity))),float(row['score'])))
        result[key]=entries
    return result,unavailable
