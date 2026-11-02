"""通告條件篩選：每種作品獨立的 1~5 級模板，管理員可於資料庫覆寫。"""
from __future__ import annotations
import json
from core.database import get_connection, transaction

SUBTYPES = ('電影','電視劇','短劇','MV','配音','幕後製作','訪談','競技綜藝','音樂綜藝','生活綜藝','真人秀','代言')
# 數值只作為暫定模板，正式門檻需由使用者審核後發布。
DEFAULT_PROFILES = {
 '電影': [(2,35,25),(4,60,45),(5,80,65),(7,105,90),(10,130,115)],
 '短劇': [(1,20,15),(2,35,25),(3,50,40),(4,65,55),(6,90,75)],
 '電視劇': [(1,30,25),(3,50,40),(4,70,55),(6,90,75),(8,115,95)],
}

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
    # 沒有經批准的模板不能假裝門檻為零；預留示範並要求正式上架前設定。
    raise ValueError(f'{subtype} 第 {level} 級尚未設定正式門檻')

def evaluate(rules,character):
    """character: {'occupation':str,'cafe_level':int,'attributes':dict,'age':int,...}。"""
    missing=[]
    if not isinstance(rules, dict): return {'eligible':False,'missing':['條件設定格式錯誤']}
    jobs=rules.get('occupations') or []
    if not isinstance(jobs,list): return {'eligible':False,'missing':['職業條件設定錯誤']}
    if jobs and character.get('occupation') not in jobs:missing.append('職業不符')
    for field,label in [('cafe_level','咖位'),('age','年齡')]:
        minimum=rules.get('min_'+field)
        if minimum is not None and int(character.get(field) or 0)<int(minimum):
            missing.append(f'{label}需達 {minimum}')
    for attr,minimum in (rules.get('min_attributes') or {}).items():
        value=(character.get('attributes') or {}).get(attr,0)
        if int(value)<int(minimum):missing.append(f'{attr}需達 {minimum}')
    return {'eligible':not missing,'missing':missing}
