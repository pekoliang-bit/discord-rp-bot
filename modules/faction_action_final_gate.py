"""第25階段：跨模組正式執行前總閘；只讀、永不直接付款。"""
from __future__ import annotations
from modules.faction_action_policy_config import read_policy
from modules.faction_action_settlement_guard import inspect_settlement
from modules.faction_action_treasury_guard import inspect_proposal_treasury


def inspect_final_gate(guild_id, proposal_id):
    policy = read_policy(guild_id)
    checks = inspect_settlement(guild_id, proposal_id, approved_policy=policy['values'])
    treasury = inspect_proposal_treasury(guild_id, proposal_id)
    issues = list(checks['issues'])
    if not treasury['sufficient']:
        issues.append('陣營金庫餘額不足。')
    if int(checks['quoted_cost']) != int(treasury['quoted_cost']):
        issues.append('審核報價與金庫報價不一致。')
    return {'guild_id':str(guild_id),'proposal_id':int(proposal_id),
            'policy_revision':int(policy['revision']), 'quoted_cost':int(treasury['quoted_cost']),
            'checks_passed':not issues, 'issues':issues,
            'execution_enabled':False,
            'reason':'正式效果強度、概率參數及原子結算尚未完成核定與端到端驗收。'}
