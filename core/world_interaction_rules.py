"""Guild-scoped pet and network-reaction rules, using existing world_configs.

No discord import, no background jobs, no player-data mutation.
"""
from __future__ import annotations
import json
import re
import copy
from core.database import get_connection

REACTION_DEFAULT = {
    'fans_up': '❤️ 😍 🥰 🫶 💗 💖 🥹',
    'fans_down': '💔 😒 🙄',
    'heat_up': '🔥 😮 😂 🤯 👀 ✨',
    'heat_down': '🥱 😴',
    'haters_up': '😡 👎 🤮 🤬 💀',
    'haters_down': '👍 🙏 🤝',
    'favor_up': '👍 🥹 🥰 🙌 💕',
    'favor_down': '👎 😡 🤮 🙄',
    'random_min': 1, 'random_max': 10, 'withdraw_penalty_chance': 20,
    'withdraw_penalty_min': 1, 'withdraw_penalty_max': 3,
}
PET_DEFAULT = {
    'health_max': 100, 'bond_max': 200, 'health_daily_loss': 2,
    'bond_daily_loss': 5, 'bond_inactive_12h_loss': 15,
    'sick_health_loss_min': 20, 'sick_health_loss_max': 40,
    'injured_health_loss_min': 15, 'injured_health_loss_max': 30,
    'untreated_24h_health_loss': 5, 'sick_daily_bond_loss': 10,
    'wrong_food_health_loss': 10, 'play_bond_gain': 10,
    'gift_bond_min': 5, 'gift_bond_max': 50,
    'daily_bonus_chance': 5, 'daily_bonus_min': 1, 'daily_bonus_max': 3,
    'pet_levels_enabled': False, 'pet_level_max': 20,
    'missing_risk_low': 1, 'missing_risk_mid': 3, 'missing_risk_high': 6,
    'walk_escape_chance': 1, 'critical_hours': 24,
    'disease_daily_chance':1, 'injury_daily_chance':1,
    'encounter_after_action_chance':5,
    'species': {'貓': 220000, '狗': 240000, '鸚鵡': 180000},
    'supplies': {'飼料': 50, '零食': 80, '玩具': 150, '健康檢查': 120, '看醫生': 300},
    'rarity_multipliers': {'common':100, 'rare':150, 'epic':300, 'legendary':500},
    'protected_species':['保育類','保護動物'],
}
# These defaults are implementation placeholders for values not yet confirmed by the owner.
# They are editable in /官方→世界觀; they are not a claim of previously approved prices.
NUMBER_LIMITS = {
    'health_max': (1,10000), 'bond_max':(1,10000),
    'daily_bonus_chance':(0,100), 'random_min':(1,100000), 'random_max':(1,100000),
    'withdraw_penalty_chance':(0,100), 'withdraw_penalty_min':(1,100000),
    'withdraw_penalty_max':(1,100000), 'pet_level_max':(1,1000),
}
CUSTOM = re.compile(r'<a?:[A-Za-z0-9_]{2,32}:\d{15,25}>')

def raw_world(guild_id):
    try:
        with get_connection() as db:
            row=db.execute('SELECT config_json FROM world_configs WHERE guild_id=?',(str(guild_id),)).fetchone()
            value=json.loads(row[0]) if row else {}
            return value if isinstance(value,dict) else {}
    except Exception as exc:
        if 'no such table' in str(exc): return {}
        raise

def pet_rules(guild_id):
    raw=raw_world(guild_id).get('pets',{})
    return {**copy.deepcopy(PET_DEFAULT),**copy.deepcopy(raw if isinstance(raw,dict) else {})}

def reaction_rules(guild_id):
    raw=raw_world(guild_id).get('reactions',{})
    return {**copy.deepcopy(REACTION_DEFAULT),**copy.deepcopy(raw if isinstance(raw,dict) else {})}

def split_emojis(text):
    """Accept space-separated emoji, custom Discord emoji, or adjacent Unicode graphemes."""
    try:
        import regex
        parts=regex.findall(r'<a?:[A-Za-z0-9_]{2,32}:\d{15,25}>|\X',str(text))
        return list(dict.fromkeys(p.strip() for p in parts if p.strip() and not p.isspace()))
    except ImportError:
        return list(dict.fromkeys(p for p in str(text).split() if p))

def validate_reactions(settings):
    values={**REACTION_DEFAULT,**settings}
    for k in ('random_min','random_max','withdraw_penalty_chance','withdraw_penalty_min','withdraw_penalty_max'):
        v=values[k]
        if type(v) is not int or not NUMBER_LIMITS[k][0]<=v<=NUMBER_LIMITS[k][1]:
            raise ValueError(f'{k} 超出允許範圍')
    if values['random_min']>values['random_max'] or values['withdraw_penalty_min']>values['withdraw_penalty_max']:
        raise ValueError('隨機最小值不能大於最大值')
    for side in ('fans','heat','haters','favor'):
        plus=set(split_emojis(values[side+'_up']))
        minus=set(split_emojis(values[side+'_down']))
        if plus & minus: raise ValueError(f'{side} 同一表符不可同時增減：{plus&minus}')
        if len(plus|minus)>100: raise ValueError('單一數值分類最多 100 種表符')
    return values

def reaction_effects(settings, emoji):
    values=validate_reactions(settings)
    effects={}
    for metric in ('fans','heat','haters','favor'):
        if emoji in split_emojis(values[metric+'_up']):effects[metric]=1
        elif emoji in split_emojis(values[metric+'_down']):effects[metric]=-1
    return effects or {'heat':1} # unconfigured emoji always +1 heat

def validate_pet(settings):
    result={**PET_DEFAULT,**settings}
    for key,default in PET_DEFAULT.items():
        if isinstance(default,bool):
            if type(result[key]) is not bool:raise ValueError(key+' 必須是布林值')
        elif isinstance(default,int):
            if type(result[key]) is not int or result[key]<0 or result[key]>10000:
                raise ValueError(key+' 必須為 0～10000 整數')
    for key in ('daily_bonus_chance','walk_escape_chance','missing_risk_low','missing_risk_mid','missing_risk_high','disease_daily_chance','injury_daily_chance','encounter_after_action_chance'):
        if not 0<=result[key]<=100:raise ValueError(key+' 機率只能是 0～100%')
    if result['daily_bonus_min']>result['daily_bonus_max']:raise ValueError('每日加成上下限不正確')
    if result['health_max']<=0 or result['bond_max']<=0:raise ValueError('健康與依賴上限必須大於0')
    if not isinstance(result['species'],dict):raise ValueError('物種價格表必須是物件')
    if not isinstance(result['protected_species'],list) or any(not isinstance(v,str) or not v for v in result['protected_species']):
        raise ValueError('保育物種清單格式錯誤')
    if not isinstance(result['supplies'],dict):raise ValueError('用品價格表必須是物件')
    if not isinstance(result['rarity_multipliers'],dict):raise ValueError('稀有度價格倍率必須是物件')
    for collection in ('species','supplies','rarity_multipliers'):
        if any(not isinstance(k,str) or not k or type(v) is not int or not 0<=v<=10**10 for k,v in result[collection].items()):
            raise ValueError(collection+' 需要非負整數價格')
    return result
