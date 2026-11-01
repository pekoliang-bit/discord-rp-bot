"""第26階段：即時結算預檢報告；不提供執行授權或扣款。"""
from __future__ import annotations
from modules.faction_action_final_gate import inspect_final_gate
from modules.faction_action_rule_validation import validate_action_policy
from modules.faction_action_policy_config import read_policy

def build_snapshot(guild_id,proposal_id):
    policy=read_policy(guild_id)
    gate=inspect_final_gate(guild_id,proposal_id)
    issues=list(gate['issues'])
    try:
        validate_action_policy(policy['values'])
    except ValueError as exc:
        issues.append(str(exc))
    return {'guild_id':str(guild_id),'proposal_id':int(proposal_id),
            'policy_revision':int(policy['revision']),
            'quoted_cost':int(gate['quoted_cost']),
            'checks_passed':not issues,'issues':issues,
            'can_execute':False,'notice':'檢查結果不是付款憑證；須由正式執行器於同一交易重新驗證。'}
