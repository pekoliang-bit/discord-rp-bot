"""結算資料封存前的標準化：不把CP/商店/社群帳號誤當角色ID。"""
from __future__ import annotations
from modules.leaderboard_catalog import BOARDS
from modules.leaderboard_service import leaderboard

IDENTIFIERS = ('entity_key','character_id','pair_id','shop_id','company_id','group_id','faction_id','account_id','entity_id','id')
NAMES = ('character_name','display_name','name','title')

def collect(guild_id):
    result={};unavailable=[]
    for key,_ in BOARDS:
        rows,status=leaderboard(guild_id,key,limit=500)
        if status not in ('ready','empty'):
            unavailable.append(key);continue
        entries=[];seen=set()
        for row in rows:
            entity=next((str(row[k]) for k in IDENTIFIERS if row.get(k) is not None),'')
            if not entity or entity in seen:continue
            seen.add(entity)
            name=next((str(row[k]) for k in NAMES if row.get(k) is not None),entity)
            entries.append((int(row['rank']),entity,name,float(row['score'])))
        result[key]=entries
    return result,unavailable
