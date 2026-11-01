"""Phase 23: pure defense interception simulation, not a live game executor.

The caller provides approved percentages, along with explicit sample rolls in [0, 100).
Full interception is checked first; otherwise the reduced sabotage probability is tested.
"""
from __future__ import annotations
import math


def _percentage(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(f'{field} 必須介於 0～100。')
    return float(value)


def _roll(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value < 100:
        raise ValueError(f'{field} 必須介於 0（含）～100（不含）。')
    return float(value)


def simulate_interception(*, sabotage_success_probability, full_block_probability,
                          block_roll, sabotage_roll):
    """All rolls must be supplied explicitly; no random draw or outcome persistence."""
    attack_p = _percentage(sabotage_success_probability, '暗害成功率')
    block_p = _percentage(full_block_probability, '完全攔截率')
    block_r = _roll(block_roll, '攔截測試值')
    attack_r = _roll(sabotage_roll, '暗害測試值')
    intercepted = block_r < block_p
    succeeded = not intercepted and attack_r < attack_p
    return {'intercepted': intercepted, 'sabotage_succeeded': succeeded,
            'outcome': 'blocked' if intercepted else 'success' if succeeded else 'failed',
            'is_simulation': True, 'execution_enabled': False}
