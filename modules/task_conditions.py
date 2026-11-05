"""通告條件篩選：每種作品獨立的 1~5 級模板，管理員可於資料庫覆寫。"""
from __future__ import annotations
import json
from core.database import get_connection, transaction

SUBTYPES = ('電影','電視劇','短劇','MV','配音','幕後製作','訪談','競技綜藝','音樂綜藝','生活綜藝','真人秀','代言','商業短工')
# 數值只作為暫定模板，正式門檻需由使用者審核後發布。
# 已確認的五級影視主技能梯度。實際每個職位的門檻由任務 requirements 覆寫。
SCREEN_MAIN_SKILL = (400, 1000, 2500, 5000, 10000)
SCREEN_EXTRA_SKILL = (25, 50, 100, 200, 400)
SCREEN_LEAD_CAFE = {
    '電影': (2, 5, 8, 12, 16),
    '電視劇': (1, 4, 7, 10, 14),
    'MV': (1, 3, 6, 9, 13),
}
DEFAULT_PROFILES = {}  # 不替其他未審核類型自行產生條件

def set_profile(guild_id,subtype,level,rules):
    if not subtype.strip() or not 1<=int(level)<=5 or not isinstance(rules,dict):
        raise ValueError('條件模板格式不正確')
    with transaction() as db:
        db.execute('''INSERT INTO rp_task_level_profiles(guild_id,subtype,level,rules_json)
          VALUES (?,?,?,?) ON CONFLICT(guild_id,subtype,level) DO UPDATE SET rules_json=excluded.rules_json''',
          (str(guild_id),subtype,int(level),json.dumps(rules,ensure_ascii=False)))

def get_profile(guild_id,subtype,level):
    if not 1<=int(level)<=5:raise ValueError('等級必須是 1~5')
    with get_connection() as db:
        row=db.execute('SELECT rules_json FROM rp_task_level_profiles WHERE guild_id=? AND subtype=? AND level=?',
                       (str(guild_id),subtype,int(level))).fetchone()
    if row:return json.loads(row['rules_json'])
    if subtype in SCREEN_LEAD_CAFE:
        # 各職位具體條件在任務定義中；此處只提供已批准五級基準。
        return {'min_cafe_level': SCREEN_LEAD_CAFE[subtype][int(level)-1],
                'min_attributes': {'演繹': SCREEN_MAIN_SKILL[int(level)-1]}}
    # 綜藝每份通告有明確職位門檻，不用影視主演模板蓋過飛行嘉賓。
    if subtype in ('訪談','競技綜藝','音樂綜藝','生活綜藝','真人秀','商業短工'):
        return {}
    # 沒有經批准的模板不能假裝門檻為零；預留示範並要求正式上架前設定。
    raise ValueError(f'{subtype} 第 {level} 級尚未設定正式門檻')

def evaluate(rules,character):
    """character: {'occupation':str,'cafe_level':int,'attributes':dict,'age':int,...}。"""
    missing=[]
    if not isinstance(rules, dict): return {'eligible':False,'missing':['條件設定格式錯誤']}
    jobs=rules.get('occupations') or []
    if not isinstance(jobs,list): return {'eligible':False,'missing':['職業條件設定錯誤']}
    if jobs and character.get('occupation') not in jobs:missing.append('職業不符')
    allowed=rules.get('allowed_character_ids') or []
    if allowed and int(character.get('character_id') or 0) not in allowed:missing.append('非指定受邀角色')
    maximum=rules.get('max_age')
    if maximum is not None and int(character.get('age') or 0)>int(maximum):missing.append(f'年齡不得超過 {maximum}')
    for field,label in [('cafe_level','咖位'),('age','年齡')]:
        minimum=rules.get('min_'+field)
        if minimum is not None and int(character.get(field) or 0)<int(minimum):
            missing.append(f'{label}需達 {minimum}')
    for attr,minimum in (rules.get('min_attributes') or {}).items():
        value=(character.get('attributes') or {}).get(attr,0)
        if int(value)<int(minimum):missing.append(f'{attr}需達 {minimum}')
    return {'eligible':not missing,'missing':missing}


def public_minimums(guild_id, task):
    """Player-facing minimum requirements; never disclose admin preset level."""
    import json
    rules=json.loads(task.get('requirements_json') or '{}')
    if task['kind'] in ('screen','variety','endorsement'):
        try:
            base=get_profile(guild_id,task['subtype'],task['level'])
        except ValueError:
            return '尚未設定正式最低條件'
        rules={**base,**rules}
    bits=[]
    if rules.get('min_cafe_level') is not None:
        bits.append('咖位 LV'+str(rules['min_cafe_level'])+' 以上')
    if rules.get('min_age') is not None: bits.append('年齡 '+str(rules['min_age'])+' 以上')
    if rules.get('max_age') is not None: bits.append('年齡 '+str(rules['max_age'])+' 以下')
    for name,minimum in (rules.get('min_attributes') or {}).items():
        bits.append(f'{name} {minimum} 以上')
    if rules.get('occupations'):
        bits.append('職業：'+'／'.join(rules['occupations'][:6]))
    if rules.get('allowed_character_ids'):
        bits.append('限定邀請角色')
    return '、'.join(bits) if bits else '符合已發布的基本接取條件'
