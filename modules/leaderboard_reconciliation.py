"""結算更正稽核入口：不直接修改歷史排名或擅自扣款。"""
from __future__ import annotations
from modules.leaderboard_storage import audit

def request_correction(guild_id, cycle, index, board, entity_key, reason):
    if not str(reason).strip():raise ValueError('必須提供更正原因')
    audit(guild_id,'correction_requested',{'cycle':cycle,'index':index,'board':board,'entity_key':str(entity_key),'reason':str(reason)[:1000]})
    return 'pending_review'
