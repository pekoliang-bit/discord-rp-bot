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
        return characters.query(guild_id,board,limit)
    if board=='works':return works.query(guild_id,limit)
    if board=='endorsements':return endorsements.query(guild_id,limit)
    if board in ('wealth','cp','shops','industry','groups'):
        return batch3.query(guild_id,{'industry':'business','groups':'group'}.get(board,board),limit)
    if board=='factions':return factions.query(guild_id,limit)
    if board=='social':return social.query(guild_id,limit)
    if board=='newcomer':return newcomer.query(guild_id,limit)
    return [],'unavailable'
