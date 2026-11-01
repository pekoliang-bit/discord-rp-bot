"""第26階段：受信任內部調用的限時效果彙整。"""
from __future__ import annotations
from modules.faction_action_effects import list_active_effects

def summarize_active_effects(guild_id,target_character_id,*,at=None):
    effects=list_active_effects(guild_id,target_character_id,at=at)
    summary={}
    for item in effects:
        key=str(item['effect_key'])
        summary[key]=summary.get(key,0)+int(item['effect_value'])
    return {'target_character_id':int(target_character_id),
            'effect_totals':summary,'effect_count':len(effects),
            'source':'recorded_active_effects',
            'note':'此函式只查詢效果，真正生效仍需玩法計算器接入。'}
