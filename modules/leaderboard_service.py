"""排行榜 V11 統一讀取入口：接入四批來源，僅唯讀，不發獎、不結算。"""
from __future__ import annotations
import sqlite3
from core.database import get_connection
from modules.leaderboard_catalog import BOARD_NAMES,ATTRIBUTE_ALIASES,SKILL_ALIASES
from modules.leaderboard_source_router import query as source_query
from modules.leaderboard_source_common import table_columns,ranked


def available_boards():
    return list(BOARD_NAMES.items())


def leaderboard(guild_id:int,board:str,*,limit:int=100):
    if board not in BOARD_NAMES:raise ValueError('未知排行榜')
    limit=max(1,min(int(limit),500))
    if board not in ATTRIBUTE_ALIASES and board not in SKILL_ALIASES:
        try:rows,status=source_query(guild_id,board,limit)
        except (sqlite3.OperationalError,sqlite3.DatabaseError,KeyError,TypeError,ValueError):
            return [],'unavailable'
        if status!='ready':return rows,status
        # UI 統一採 character_name 欄位；作品/商鋪等亦可能提供 name。
        for row in rows:
            row.setdefault('character_name',str(row.get('name',row.get('id','未命名'))))
        return rows,'ready'
    if board in ATTRIBUTE_ALIASES:
        table,key,value,aliases='attributes','name','value',ATTRIBUTE_ALIASES[board]
    else:
        table,key,value,aliases='character_skills','skill_name','skill_value',SKILL_ALIASES[board]
    with get_connection() as db:
        if not {'guild_id','id','name','status'}<=table_columns(db,'characters') or not {'guild_id','character_id',key,value}<=table_columns(db,table):
            return [],'unavailable'
        marks=','.join('?' for _ in aliases)
        rows=db.execute(f"""SELECT c.id AS character_id,c.name AS character_name,MAX(t.{value}) AS score
            FROM characters c JOIN {table} t ON t.guild_id=c.guild_id AND t.character_id=c.id
            WHERE c.guild_id=? AND c.status='active' AND t.{key} IN ({marks})
            GROUP BY c.id,c.name""",(str(guild_id),*aliases)).fetchall()
    return (ranked([dict(r) for r in rows],limit),'ready') if rows else ([], 'empty')
