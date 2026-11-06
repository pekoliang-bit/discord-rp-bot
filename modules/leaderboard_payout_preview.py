"""排行榜獎勵預覽：可選擇帶入資料庫作真實收款帳戶只讀檢查。"""
from __future__ import annotations
import json
from modules.leaderboard_payout_readiness import inspect


def preview_award(award_row,*,db=None):
    row=dict(award_row)
    try:reward=json.loads(row['reward_json'])
    except (KeyError,TypeError,ValueError):reward=None
    board=str(row.get('board',''));key=str(row.get('entity_key',''))
    result=inspect(board,key,reward,db=db,guild_id=row.get('guild_id') if db is not None else None)
    issues=list(result['issues'])
    if row.get('cycle')!='30d':issues.append('14天小結算不能派發獎勵')
    if not isinstance(reward,dict) or reward.get('rank')!=row.get('rank'):
        issues.append('獎勵與封存名次不一致')
    if row.get('state') not in ('ready','approved'):
        issues.append('封存獎勵尚未完成審核')
    if db is None:issues.append('未核對實際收款帳戶')
    return {'guild_id':str(row.get('guild_id','')),'cycle':row.get('cycle'),
            'index':row.get('idx'),'board':board,'entity_key':key,'rank':row.get('rank'),
            'reward':reward,'recipient':result['recipient'],'can_pay':not issues,
            'issues':list(dict.fromkeys(issues))}
