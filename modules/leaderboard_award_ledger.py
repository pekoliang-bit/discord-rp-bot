"""正式獎勵記錄；沒有核准獎勵設定時只記錄不發獎。"""
import json
from core.database import transaction
from modules.leaderboard_storage import initialize

def record_unconfigured(guild_id,cycle,index,board,entity_key,rank):
    initialize()
    with transaction() as db:
        db.execute("INSERT OR IGNORE INTO lb_v11_awards(guild_id,cycle,idx,board,entity_key,rank,reward_json,state) VALUES(?,?,?,?,?,?,?,'unconfigured')",(str(guild_id),cycle,index,board,str(entity_key),rank,'{}'))
    return 'unconfigured'

def claim(guild_id,cycle,index,board,entity_key):
    """保留給未來已核准的獎勵適配器；本版不執行轉帳。"""
    raise NotImplementedError('獎勵種類與金額尚未核准，不可自動發獎')
