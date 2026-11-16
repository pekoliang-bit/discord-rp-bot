"""內測獨立測試帳本：僅記錄模擬交易及模擬行動，不碰正式帳戶、點數、排名、劇情。"""
from __future__ import annotations

import json
import secrets
from core.database import transaction


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_beta_sandbox_events_v1 (
        guild_id TEXT NOT NULL, request_key TEXT NOT NULL,
        character_id INTEGER NOT NULL, category TEXT NOT NULL,
        payload_json TEXT NOT NULL, result_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,request_key)
    )''')


def is_beta(db,guild_id,character_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_beta_characters'").fetchone():
        return False
    return db.execute('''SELECT 1 FROM rp_beta_characters
        WHERE guild_id=? AND character_id=?''',(str(guild_id),int(character_id))).fetchone() is not None


def simulate(guild_id,user_id,character_id,category,request_key,payload,result):
    """請求編號去重；交易全部留沙盒，永不增加正式錢包及屬性。"""
    gid,cid,uid=str(guild_id),int(character_id),str(user_id)
    if not request_key or len(str(request_key))>200:raise ValueError('無效的測試請求代碼')
    with transaction() as db:
        setup(db)
        if not is_beta(db,gid,cid):raise PermissionError('這不是內測角色')
        char=db.execute('SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?',(gid,cid)).fetchone()
        if char is None or str(char['owner_id'])!=uid or char['status']!='active':
            raise PermissionError('只能操作自己已啟用的測試角色')
        old=db.execute('SELECT character_id,category,result_json FROM rp_beta_sandbox_events_v1 WHERE guild_id=? AND request_key=?',(gid,str(request_key))).fetchone()
        if old:
            if int(old['character_id'])!=cid or str(old['category'])!=str(category):
                raise PermissionError('同一測試請求不可用於不同角色或操作')
            return dict(json.loads(old['result_json']),duplicated=True)
        db.execute('''INSERT INTO rp_beta_sandbox_events_v1
           (guild_id,request_key,character_id,category,payload_json,result_json)
           VALUES (?,?,?,?,?,?)''',(gid,str(request_key),cid,str(category),
            json.dumps(payload,ensure_ascii=False),json.dumps(result,ensure_ascii=False)))
        return dict(result,duplicated=False)


def simulate_location_action(guild_id,user_id,character_id,location_key,action,request_key):
    """正式行動的沙盒預覽；不扣無限測試金幣／行動值，也不寫正式統計。"""
    rolled=[]
    for item in action.get('stat_rewards') or []:
        amt=int(item.get('amount',0)) if item.get('mode')=='fixed' else secrets.randbelow(15)+1
        rolled.append({'name':str(item['name']),'amount':amt})
    result={'character_id':int(character_id),'action_name':str(action.get('name','測試行動')),
        'cost_coins':int(action.get('cost_coins',0)),
        'reward_coins':int(action.get('reward_coins',0)),
        'action_points_spent':int(action.get('action_points_cost',0)),
        'free_action_points_spent':int(action.get('action_points_cost',0)),
        'paid_action_points_spent':0,'birthday_credit_used':False,
        'attribute_increase':int(action.get('attribute_increase',0)),
        'rolled_stats':rolled,'point_rewards':action.get('point_rewards') or {},
        'test_only':True}
    return simulate(guild_id,user_id,character_id,'location_action',request_key,
                    {'location_key':location_key,'action':action},result)
