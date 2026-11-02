
"""排行榜安全讀取層：僅查詢，不建立或變更資料表。"""
from __future__ import annotations
import sqlite3
from core.database import get_connection
from modules.leaderboard_catalog import BOARD_NAMES, ATTRIBUTE_ALIASES, SKILL_ALIASES


def available_boards():
    return list(BOARD_NAMES.items())


def leaderboard(guild_id: int, board: str, *, limit: int = 100):
    """回傳 (rows, status)；status=ready/unavailable/empty。

    只有欄位來源可明確對照的屬性、技能榜可直接查詢。
    同分並列、後續跳號；以 character_id 作穩定次序，不作同分決勝。
    """
    from modules.extra_character_store import initialize as _initialize_extras
    _initialize_extras()
    if board not in BOARD_NAMES:
        raise ValueError('未知排行榜')
    if board in ATTRIBUTE_ALIASES:
        table, key, value, aliases = 'attributes','name','value',ATTRIBUTE_ALIASES[board]
    elif board in SKILL_ALIASES:
        table, key, value, aliases = 'character_skills','skill_name','skill_value',SKILL_ALIASES[board]
    else:
        return [], 'unavailable'
    placeholders = ','.join('?' for _ in aliases)
    sql = f"""SELECT c.id AS character_id,c.name AS character_name,
                 MAX(t.{value}) AS score
              FROM characters c JOIN {table} t
                ON t.guild_id=c.guild_id AND t.character_id=c.id
              WHERE c.guild_id=? AND c.status='active' AND (COALESCE(json_extract(c.settings_json,'$.character_category'),'') != 'extra' OR COALESCE((SELECT allow_ranking FROM extra_settings WHERE guild_id=c.guild_id),1)=1)
                AND t.{key} IN ({placeholders})
              GROUP BY c.id,c.name ORDER BY score DESC,c.id ASC"""
    try:
        with get_connection() as db:
            data = [dict(r) for r in db.execute(sql,(str(guild_id),*aliases)).fetchall()]
    except sqlite3.OperationalError:
        return [], 'unavailable'
    if not data:
        return [], 'empty'
    prev = None
    rank = 0
    for i, item in enumerate(data,1):
        if item['score'] != prev:
            rank = i
        item['rank'] = rank
        prev = item['score']
    return data[:max(1,min(int(limit),500))], 'ready'
