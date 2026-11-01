"""第26階段：核准規則數值的嚴格驗證；不進行實際結算。"""
from __future__ import annotations
import math
from modules.faction_action_settlement_guard import REQUIRED_POLICY_KEYS

def validate_action_policy(values):
    if not isinstance(values, dict):
        raise ValueError('規則必須是字典。')
    missing=[key for key in REQUIRED_POLICY_KEYS if key not in values]
    if missing:
        raise ValueError('缺少正式規則：'+', '.join(missing))
    validated={}
    for key in REQUIRED_POLICY_KEYS:
        value=values[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError(f'{key} 必須是有限數值。')
        if value<0:
            raise ValueError(f'{key} 不能為負數。')
        validated[key]=float(value)
    for key in ('full_block_probability','discovery_probability'):
        if validated[key]>100:
            raise ValueError(f'{key} 不可超過 100%。')
    duration=values['effect_duration_seconds']
    if duration<=0 or int(duration)!=duration:
        raise ValueError('效果期限必須是正整數秒。')
    if validated['effect_strength']<=0:
        raise ValueError('效果強度必須大於零。')
    validated['effect_duration_seconds']=int(duration)
    return validated
