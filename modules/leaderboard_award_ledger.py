
"""排行榜獎勵待派發帳本：僅封存計算結果，不會改動資產。"""
import json
from core.database import transaction
from modules.leaderboard_storage import initialize
from modules.leaderboard_reward_rules import calculate
from modules.leaderboard_milestone_service import stage_milestones

def stage_rewards(guild_id,cycle,index,board,ranked_rows):
    if cycle != '30d': return 0
    initialize()
    count=0
    with transaction() as db:
        for row in ranked_rows:
            rank=int(row['rank'])
            reward=calculate(board,rank)
            state='unconfigured' if reward is None else 'pending_adapter'
            payload='{}' if reward is None else json.dumps(reward,ensure_ascii=False)
            db.execute("""INSERT OR IGNORE INTO lb_v11_awards(guild_id,cycle,idx,board,entity_key,rank,reward_json,state)
                          VALUES(?,?,?,?,?,?,?,?)""",(str(guild_id),cycle,index,board,str(row['entity_key']),rank,payload,state))
            count+=1
    # Award rows have been committed; calculate milestones separately to avoid nested transactions.
    # Snapshot data must already be committed by the settlement service.
    if ranked_rows:
        stage_milestones(guild_id, index, board, ranked_rows)
    return count

def record_unconfigured(guild_id,cycle,index,board,entity_key,rank):
    return stage_rewards(guild_id,cycle,index,board,[{'entity_key':entity_key,'rank':rank}])

def claim(guild_id,cycle,index,board,entity_key):
    raise NotImplementedError('尚未完成角色/組織獎勵入帳適配器，禁止直接派發')
