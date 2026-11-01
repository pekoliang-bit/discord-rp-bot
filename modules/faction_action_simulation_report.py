"""第24階段：純模擬結果稽核，驗證所有預覽數值與結果一致性。"""
from __future__ import annotations
import math
from modules.faction_action_simulation import preview_action


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} 必須是有限數字。')
    return float(value)


def generate_report(*, guild_id, proposal_id, policy_revision, config,
                    attacker_ability, target_resistance, level,
                    block_roll, sabotage_roll, discovery_roll):
    """Return an audit-ready preview; caller MUST NOT treat it as execution authorization."""
    gid, pid, revision = str(guild_id), int(proposal_id), int(policy_revision)
    if pid <= 0 or revision <= 0:
        raise ValueError('提案或規則版本不合法。')
    for label, val in [('發動能力',attacker_ability),('防禦能力',target_resistance)]:
        if _finite(val,label)<0:
            raise ValueError(f'{label}不可為負數。')
    result = preview_action(config=config,
        attacker_ability=attacker_ability, target_resistance=target_resistance,
        level=level, block_roll=block_roll,
        sabotage_roll=sabotage_roll, discovery_roll=discovery_roll)
    if not result.get('is_simulation') or result.get('execution_enabled') is not False:
        raise ValueError('模擬函式回傳不安全的結果。')
    return {
        'guild_id':gid, 'proposal_id':pid, 'policy_revision':revision,
        'quoted_cost':result['probability']['cost'],
        'outcome':result['defense']['outcome'],
        'source_exposed_on_failure':result['source_exposed_on_failure'],
        'calculation':result['probability'],
        'defense':result['defense'],
        'rolls':{'block':block_roll,'sabotage':sabotage_roll,'discovery':discovery_roll},
        'is_simulation':True,'execution_enabled':False,
        'note':'此報告只供檢查；不含資金轉移、正式隨機判定或角色效果。'
    }
