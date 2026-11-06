"""排行榜獎勵預覽（唯讀）：含獲獎身分解析與週期驗證。"""
import json
from modules.leaderboard_payout_readiness import inspect
def preview_award(award_row):
    row=dict(award_row)
    try:reward=json.loads(row['reward_json'])
    except (KeyError,TypeError,ValueError):reward=None
    board=str(row.get('board',''));key=str(row.get('entity_key',''))
    result=inspect(board,key,reward);issues=list(result['issues'])
    if row.get('cycle')!='30d':issues.append('14天小結算不能派發獎勵')
    if not isinstance(reward,dict) or reward.get('rank')!=row.get('rank'):issues.append('獎勵與封存名次不一致')
    if row.get('state') not in ('ready','approved'):issues.append('封存獎勵尚未完成審核')
    return {'guild_id':str(row.get('guild_id','')),'cycle':row.get('cycle'),'index':row.get('idx'),'board':board,'entity_key':key,'rank':row.get('rank'),'reward':reward,'recipient':result['recipient'],'can_pay':not issues,'issues':list(dict.fromkeys(issues))}
