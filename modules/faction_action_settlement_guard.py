"""陣營第20階段：正式結算前安全閘（不扣款、不擲骰、不施加效果）。

只有完整審核、目標仍有效、雙方陣營合法且必要係數已經正式配置時，
才能由未來的結算器進一步處理；本模組永遠不執行交易結算。
"""
from __future__ import annotations

from modules.faction_action_readiness import inspect_action
from modules.faction_action_effects import (
    EFFECT_TYPES, is_same_type_cooldown_clear,
)

# 這些參數仍待正式核准。留空即禁止執行，不能用測試預設值放行。
REQUIRED_POLICY_KEYS = (
    'ability_weight', 'funding_weight', 'defense_reduction',
    'full_block_probability', 'discovery_probability',
    'effect_duration_seconds', 'effect_strength',
)


def inspect_settlement(guild_id, proposal_id, *, approved_policy=None, at=None):
    """回傳不含敏感結果的就緒報告。只讀，不產生副作用。"""
    base = inspect_action(guild_id, proposal_id)
    missing = [key for key in REQUIRED_POLICY_KEYS
               if not isinstance(approved_policy, dict) or approved_policy.get(key) is None]
    issues = []
    if not base['ready_for_policy']:
        issues.append('尚未取得兩位合法幹部共同批准。')
    if missing:
        issues.append('缺少正式數值設定：' + '、'.join(missing))
    if base['effect_key'] not in EFFECT_TYPES:
        issues.append('效果種類不合法。')
    if base['action_type'] == 'sabotage' and not is_same_type_cooldown_clear(
        guild_id, base['target_character_id'], base['effect_key'], at=at
    ):
        issues.append('目標角色 72 小時內已承受過相同類型暗害。')
    # 故意保持鎖定：正式結算器尚不存在，不得誤認檢查完成就能扣款。
    return {
        'proposal_id': int(proposal_id),
        'action_type': base['action_type'],
        'quoted_cost': base['quoted_cost'],
        'checks_passed': not issues,
        'issues': issues,
        'missing_policy_keys': missing,
        'execution_enabled': False,
        'blocker': '正式扣款及效果結算器尚未實作；本階段禁止執行。',
    }
