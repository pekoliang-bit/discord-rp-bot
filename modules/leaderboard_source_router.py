"""整合第一至第四批資料來源；未知榜單安全返回 unavailable。"""
from __future__ import annotations
from modules import leaderboard_source_characters as characters
from modules import leaderboard_source_works as works
from modules import leaderboard_source_endorsements as endorsements
from modules import leaderboard_source_batch3 as batch3
from modules import leaderboard_source_factions as factions
from modules import leaderboard_source_social as social
from modules import leaderboard_source_newcomer as newcomer


def query(guild_id,board,limit=100):
    if board in ('impact','fans','antis','main_skill'):
        result=characters.query(guild_id,board,limit)
    elif board=='works':result=works.query(guild_id,limit)
    elif board=='endorsements':result=endorsements.query(guild_id,limit)
    elif board in ('wealth','cp','shops','industry','groups'):
        result=batch3.query(guild_id,{'industry':'business','groups':'group'}.get(board,board),limit)
    elif board=='factions':result=factions.query(guild_id,limit)
    elif board=='social':result=social.query(guild_id,limit)
    elif board=='newcomer':result=newcomer.query(guild_id,limit)
    else:return [],'unavailable'
    if not isinstance(result,tuple) or len(result)!=2 or not isinstance(result[0],list):
        return result
    rows,state=result
    from core.database import get_connection
    from core.beta_sandbox_v1 import is_beta
    with get_connection() as db:
        filtered=[]
        for row in rows:
            ids=[]
            for key in ('character_id','a','b'):
                if isinstance(row,dict) and str(row.get(key,'')).isdigit():
                    ids.append(int(row[key]))
            # Non-character leaderboards have IDs for businesses, not characters.
            if not ids or not any(is_beta(db,guild_id,cid) for cid in ids):
                filtered.append(row)
    if filtered and filtered!=rows and 'rank' in filtered[0]:
        from modules.leaderboard_source_common import ranked
        filtered=ranked(filtered,limit)
    return filtered,state
