"""Phase 23: compose probability/interception simulations; never modify player state.

Rule coefficients MUST come from an approved configuration. This module intentionally
refuses absent fields and does not generate random outcomes or perform transfers.
"""
from __future__ import annotations
from modules.faction_action_probability import calculate_success_probability
from modules.faction_action_defense import simulate_interception

REQUIRED_CONFIG = ('ability_weight', 'funding_weight', 'defense_reduction',
                   'full_block_probability', 'discovery_probability')


def preview_action(*, config: dict, attacker_ability: float, target_resistance: float,
                   level: int, block_roll: float, sabotage_roll: float, discovery_roll: float):
    if not isinstance(config, dict):
        raise ValueError('缺少正式規則設定。')
    missing = [key for key in REQUIRED_CONFIG if key not in config or config[key] is None]
    if missing:
        raise ValueError('正式設定缺少：' + '、'.join(missing))
    # Validate discovery chance and roll even when sabotage is blocked.
    from modules.faction_action_defense import _percentage, _roll
    discovery_probability = _percentage(config['discovery_probability'], '暴露機率')
    discovery_roll = _roll(discovery_roll, '暴露測試值')
    probability = calculate_success_probability(
        attacker_ability=attacker_ability, target_resistance=target_resistance,
        level=level, ability_weight=config['ability_weight'],
        funding_weight=config['funding_weight'], defense_reduction=config['defense_reduction'])
    defense = simulate_interception(
        sabotage_success_probability=probability['success_probability'],
        full_block_probability=config['full_block_probability'],
        block_roll=block_roll, sabotage_roll=sabotage_roll)
    # Discovery only applies to an attempted sabotage that did not succeed.
    exposed = defense['outcome'] != 'success' and discovery_roll < discovery_probability
    return {'probability': probability, 'defense': defense,
            'source_exposed_on_failure': exposed,
            'is_simulation': True, 'execution_enabled': False,
            'notice': '模擬計算：不執行扣款、審核、負面效果或通知。'}
