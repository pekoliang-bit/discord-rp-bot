"""整合第一至第四批資料來源；未知榜單安全返回 unavailable。"""
from __future__ import annotations
from modules import leaderboard_source_characters as characters
from modules import leaderboard_source_works as works
from modules import leaderboard_source_endorsements as endorsements
from modules import leaderboard_source_batch3 as batch3
from modules import leaderboard_source_factions as factions
from modules import leaderboard_source_social as social
from modules import leaderboard_source_newcomer as newcomer


def _filter(gid, result):
    from modules.extra_character_ranking_policy import filter_rows
    rows,status=result
    if status not in ("ready","empty"):return result
    filtered=filter_rows(gid,rows)
    if filtered and isinstance(filtered[0],dict) and "score" in filtered[0]:
        score=None;rank=0
        for pos,row in enumerate(filtered,1):
            if row["score"]!=score:rank=pos;score=row["score"]
            row["rank"]=rank
    return filtered,status if filtered else "empty"

def query(guild_id,board,limit=100):
    if board in ('impact','fans','antis','main_skill'):
        return _filter(guild_id,characters.query(guild_id,board,limit))
    if board=='works':return _filter(guild_id,works.query(guild_id,limit))
    if board=='endorsements':return _filter(guild_id,endorsements.query(guild_id,limit))
    if board in ('wealth','cp','shops','industry','groups'):
        return _filter(guild_id,batch3.query(guild_id,{'industry':'business','groups':'group'}.get(board,board),limit))
    if board=='factions':return _filter(guild_id,factions.query(guild_id,limit))
    if board=='social':return _filter(guild_id,social.query(guild_id,limit))
    if board=='newcomer':return _filter(guild_id,newcomer.query(guild_id,limit))
    return [],'unavailable'
