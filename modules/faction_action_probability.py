"""Phase 23: pure, side-effect-free sabotage probability calculation.

Percent points: 40 + (attacker - defender) * ability_weight
              + funding_level * funding_weight - defense_points.
Weights must be approved and supplied by the caller; this module does not authorize actions.
"""
from __future__ import annotations
import math

BASE_RATE = 40.0
MIN_RATE = 5.0
MAX_RATE = 90.0
LEVEL_COSTS = {1: 10_000, 2: 50_000, 3: 100_000}


def _number(value, name: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} 必須是有限數值。')
    if nonnegative and value < 0:
        raise ValueError(f'{name} 不可為負數。')
    return float(value)


def calculate_success_probability(*, attacker_ability: float, target_resistance: float,
                                  level: int, ability_weight: float,
                                  funding_weight: float, defense_reduction: float = 0) -> dict:
    """Return an auditable simulation result. No randomness, database, or payments."""
    if isinstance(level, bool) or level not in LEVEL_COSTS:
        raise ValueError('行動等級必須為 1、2、3。')
    attacker = _number(attacker_ability, '發動方能力', nonnegative=True)
    resistance = _number(target_resistance, '目標防備', nonnegative=True)
    weight = _number(ability_weight, '能力係數', nonnegative=True)
    funding = _number(funding_weight, '投入係數', nonnegative=True)
    defense = _number(defense_reduction, '防護減免', nonnegative=True)
    ability_delta = (attacker - resistance) * weight
    funding_delta = level * funding
    raw = BASE_RATE + ability_delta + funding_delta - defense
    return {'level': level, 'cost': LEVEL_COSTS[level],
            'base_rate': BASE_RATE, 'ability_adjustment': ability_delta,
            'funding_adjustment': funding_delta, 'defense_adjustment': defense,
            'raw_rate': raw, 'success_probability': max(MIN_RATE, min(MAX_RATE, raw)),
            'is_simulation': True, 'execution_enabled': False}
