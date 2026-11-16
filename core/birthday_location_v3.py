"""角色週年地點福利：每地點每週年限購一次，買X送Y預付券可分次核銷。

僅支援價格已固定、已核准的場所行動；不得套用隨機定價、未核准或
有收入的行動。每次購買與核銷均與 SQLite 經濟交易原子化。
"""
from __future__ import annotations

import json
from datetime import timedelta, datetime, timezone

from core.database import transaction, get_or_create_account
from core.economy import _payment
from core import birthday_core_v3 as birthday
from core.birthday_config_v3 import settings


OFFERS = {
    '餐廳': (1, 1, '餐點買一送一'),
    '咖啡廳': (1, 1, '指定飲品買一送一'),
    '一般課程': (2, 1, '課程買二送一'),
    '高級課程': (5, 1, '課程買五送一'),
}


def setup(db):
    birthday.setup(db)
    db.execute('''CREATE TABLE IF NOT EXISTS rp_birthday_location_bundles_v3 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        location_key TEXT NOT NULL, action_fingerprint TEXT NOT NULL,
        action_name TEXT NOT NULL, offer_key TEXT NOT NULL,
        cycle INTEGER NOT NULL, unit_price INTEGER NOT NULL,
        purchased_sessions INTEGER NOT NULL, free_sessions INTEGER NOT NULL,
        remaining_sessions INTEGER NOT NULL, order_key TEXT NOT NULL,
        purchased_at TEXT NOT NULL,
        UNIQUE(guild_id,order_key),
        CHECK(remaining_sessions>=0)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_birthday_location_redemptions_v3 (
        guild_id TEXT NOT NULL, request_key TEXT NOT NULL,
        bundle_id INTEGER NOT NULL, character_id INTEGER NOT NULL,
        redeemed_at TEXT NOT NULL, PRIMARY KEY(guild_id,request_key)
    )''')


def _kind(location, action, guild_id):
    """地點可在設定中指定 birthday_offer_key；預設辨識常見場所。"""
    config=settings(guild_id)['location_offers']
    venue_rules=settings(guild_id).get('venue_rules',{})
    explicit=venue_rules.get(str(location.get('key')))
    if explicit=='停用':return None
    preferred=str(explicit or location.get('birthday_offer_key') or '').strip()
    label=str(location.get('name') or '')
    if preferred in OFFERS and preferred in config:
        return preferred
    if '餐廳' in label and '餐廳' in config:
        return '餐廳'
    if any(x in label for x in ('咖啡', '飲品')) and '咖啡廳' in config:
        return '咖啡廳'
    if action.get('category')=='課程':
        if '高級' in label and '高級課程' in config:
            return '高級課程'
        if '一般課程' in config:
            return '一般課程'
    return None


def offer_for(guild_id, location, action):
    """回傳可真正履約的單一課程/餐點組合；不自動發放無商品依據的禮品。"""
    kind=_kind(location,action,guild_id)
    if kind not in OFFERS or action.get('approved') is not True:
        return None
    if action.get('formal_notice') is True or int(action.get('reward_coins',0)):
        return None
    if any(s.get('mode')=='random' for s in (action.get('stat_rewards') or [])):
        return None
    if kind in ('餐廳','咖啡廳'):
        if action.get('category') not in ('活動',):
            return None
        if kind=='咖啡廳' and not any(t in str(action.get('name','')) for t in ('飲品','咖啡','茶','奶')):
            return None
    else:
        if action.get('category')!='課程':
            return None
    from modules.action_reward_rules import validate_action
    valid,_=validate_action(action)
    if not valid:
        return None
    points=sum(int(x.get('amount',0)) for x in (action.get('stat_rewards') or []))
    points+=int(action.get('attribute_increase',0))
    points+=sum(int(v) for v in (action.get('point_rewards') or {}).values())
    # 與 core.action_execution.execute_action 的實際固定價規則保持一致。
    price=int(action.get('cost_coins',0))
    if action.get('stat_rewards'):
        price=max(price,points*(500 if action.get('category')=='課程' else 300))
    if price<=0:
        return None
    buy,bonus,title=OFFERS[kind]
    from core.action_execution import _fingerprint
    return {'offer_key':kind,'title':title,'pay_count':buy,'bonus_count':bonus,
            'unit_price':price,'total_price':buy*price,
            'action_name':str(action.get('name','')),
            'action_fingerprint':_fingerprint(str(location.get('key') or location.get('channel_id')),action)}


def eligible(db,guild_id,character_id,location_key):
    info=birthday.status(db,str(guild_id),int(character_id))
    if not info or info['cycle']<1:
        return None
    end=info['current_at']+timedelta(hours=settings(guild_id)['location_hours'])
    if datetime.now(timezone.utc)>=end:
        return None
    prior=db.execute('''SELECT 1 FROM rp_birthday_location_uses_v3
        WHERE guild_id=? AND character_id=? AND cycle=? AND location_key=?''',
        (str(guild_id),int(character_id),info['cycle'],str(location_key))).fetchone()
    return info if not prior else None


def buy_bundle(guild_id,character_id,user_id,location,action,request_key):
    gid,cid=str(guild_id),int(character_id)
    key=str(location.get('key') or '').strip()
    if not key or not request_key:
        raise ValueError('地點或交易識別碼無效')
    offer=offer_for(gid,location,action)
    if not offer:
        raise ValueError('這項活動未開放有效的生日組合優惠')
    with transaction() as db:
        setup(db)
        owner=db.execute('SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?',(gid,cid)).fetchone()
        if not owner or owner['status']!='active' or str(owner['owner_id'])!=str(user_id):
            raise PermissionError('只能為自己的已啟用角色購買')
        prior=db.execute('''SELECT * FROM rp_birthday_location_bundles_v3
            WHERE guild_id=? AND order_key=?''',(gid,str(request_key))).fetchone()
        if prior:
            if int(prior['character_id'])!=cid:
                raise PermissionError('交易編號已被使用')
            return {'bundle_id':prior['id'],'status':'already','paid':prior['unit_price']*prior['purchased_sessions'],
                    'sessions':prior['purchased_sessions']+prior['free_sessions']}
        info=eligible(db,gid,cid,key)
        if info is None:
            raise ValueError('地點生日優惠已過期，或此地點在本週年已使用')
        total=int(offer['total_price'])
        src=get_or_create_account(db,gid,'character',cid,'wallet')
        dst=get_or_create_account(db,gid,'system','location_actions','treasury')
        _payment(db,gid,f'birthday-location:{gid}:{request_key}',src,dst,total,
                 f'生日地點優惠／{offer["title"]}／{offer["action_name"]}')
        marked=db.execute('''INSERT OR IGNORE INTO rp_birthday_location_uses_v3
            (guild_id,character_id,cycle,location_key,used_at) VALUES (?,?,?,?,?)''',
            (gid,cid,info['cycle'],key,datetime.now(timezone.utc).isoformat()))
        if marked.rowcount!=1:
            raise ValueError('此地點生日優惠已使用')
        amount=offer['pay_count']+offer['bonus_count']
        cur=db.execute('''INSERT INTO rp_birthday_location_bundles_v3
            (guild_id,character_id,location_key,action_fingerprint,action_name,offer_key,
            cycle,unit_price,purchased_sessions,free_sessions,remaining_sessions,order_key,purchased_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (gid,cid,key,offer['action_fingerprint'],offer['action_name'],offer['offer_key'],
             info['cycle'],offer['unit_price'],offer['pay_count'],offer['bonus_count'],
             amount,str(request_key),datetime.now(timezone.utc).isoformat()))
        return {'bundle_id':cur.lastrowid,'status':'purchased','paid':total,'sessions':amount}


def use_prepaid(db,guild_id,character_id,location_key,action,request_key,cost):
    """在正式 /行動 的單一交易中核銷。只有完全相同的核准行動才能抵扣。"""
    setup(db)
    gid,cid=str(guild_id),int(character_id)
    previous=db.execute('SELECT bundle_id FROM rp_birthday_location_redemptions_v3 WHERE guild_id=? AND request_key=?',
                        (gid,str(request_key))).fetchone()
    if previous:return True
    from core.action_execution import _fingerprint
    fp=_fingerprint(str(location_key),action)
    row=db.execute('''SELECT id,unit_price FROM rp_birthday_location_bundles_v3
      WHERE guild_id=? AND character_id=? AND location_key=? AND action_fingerprint=?
        AND remaining_sessions>0 ORDER BY purchased_at,id LIMIT 1''',
      (gid,cid,str(location_key),fp)).fetchone()
    if not row or int(row['unit_price'])!=int(cost) or cost<=0:
        return False
    updated=db.execute('UPDATE rp_birthday_location_bundles_v3 SET remaining_sessions=remaining_sessions-1 WHERE id=? AND remaining_sessions>0',(row['id'],))
    if updated.rowcount!=1:
        return False
    db.execute('INSERT INTO rp_birthday_location_redemptions_v3 VALUES (?,?,?,?,?)',
        (gid,str(request_key),int(row['id']),cid,datetime.now(timezone.utc).isoformat()))
    return True


def list_credits(guild_id,character_id):
    with transaction() as db:
        setup(db)
        return [dict(r) for r in db.execute('''SELECT location_key,action_name,
            SUM(remaining_sessions) AS remaining_sessions FROM rp_birthday_location_bundles_v3
            WHERE guild_id=? AND character_id=? AND remaining_sessions>0
            GROUP BY location_key,action_name''',(str(guild_id),int(character_id)))]
