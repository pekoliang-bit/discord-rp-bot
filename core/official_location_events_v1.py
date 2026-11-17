"""官方地點隨機劇情事件：只讀 JSON、限已核准的官方行動。

預設事件停用；在設定機率／冷卻與核准啟用前不會抽取。
同一行動只抽一次、每筆事件只可由執行者完成一次選擇。
銀行、醫院、商城專用服務需要各自的正式成功交易入口，不能冒用 /行動。
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from pathlib import Path
from core.database import transaction

SOURCE = Path(__file__).resolve().parent.parent / 'assets' / 'location_events' / 'official_random_events.json'
OFFICIAL_GUILD = '1544051203493601380'


def _stamp():
    return datetime.now(timezone.utc).isoformat()


@lru_cache(maxsize=1)
def load():
    data = json.loads(SOURCE.read_text(encoding='utf-8-sig'))
    if data.get('scope') != 'official_locations_only':
        raise ValueError('隨機事件資料必須只涵蓋官方地點')
    ids, locations = set(), {}
    for event in data.get('events', []):
        event_id = str(event.get('event_id', ''))
        key = str(event.get('location_key', ''))
        if not event_id or event_id in ids or not key or event.get('official_only') is not True:
            raise ValueError('官方隨機事件 ID 或官方標示無效')
        ids.add(event_id)
        if not isinstance(event.get('choices'), list) or len(event['choices']) != 2:
            raise ValueError('每則官方事件必須有兩個玩家選項')
        if any(not isinstance(c.get('key'), str) or not c.get('response') for c in event['choices']):
            raise ValueError('事件選擇內容不完整')
        effects = event.get('effects') or {}
        if any(effects.get(k) not in (None, 0, [], {}) for k in ('coins', 'attribute_points', 'items', 'influence_index')):
            raise ValueError('尚未核准的官方劇情事件不得發放獎勵')
        locations.setdefault(key, []).append(event)
    return data, locations


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_official_location_event_rolls_v1 (
        guild_id TEXT NOT NULL, request_key TEXT NOT NULL,
        character_id INTEGER NOT NULL, location_key TEXT NOT NULL,
        event_id TEXT, created_at TEXT NOT NULL,
        displayed_at TEXT,
        PRIMARY KEY(guild_id,request_key))""")
    cols = {r[1] for r in db.execute('PRAGMA table_info(rp_official_location_event_rolls_v1)')}
    if 'displayed_at' not in cols:
        db.execute('ALTER TABLE rp_official_location_event_rolls_v1 ADD COLUMN displayed_at TEXT')
    db.execute("""CREATE TABLE IF NOT EXISTS rp_official_location_event_choices_v1 (
        guild_id TEXT NOT NULL, request_key TEXT NOT NULL,
        character_id INTEGER NOT NULL, event_id TEXT NOT NULL,
        choice_key TEXT NOT NULL, response TEXT NOT NULL,
        chosen_at TEXT NOT NULL, PRIMARY KEY(guild_id,request_key))""")


def _official_location(guild_id, location_key):
    # 以已核准官方地點資料核實識別碼；不接受玩家自建場所。
    from core.official_birthday_gifts_v1 import location
    return location(guild_id, location_key)


def roll_after_action(guild_id, character_id, location_key, request_key, *, channel_id=None):
    """必須於 execute_action 成功後呼叫，核實同一筆已提交行動。"""
    gid, cid, key = str(guild_id), int(character_id), str(location_key)
    request = str(request_key)
    official=_official_location(gid, key)
    if not request or not official or channel_id is None or str(channel_id)!=str(official.get('channel_id')):
        return None
    config, grouped = load()
    policy = config.get('trigger_policy') or {}
    if config.get('default_enabled') is not True:
        return None
    rate = policy.get('chance_percent')
    cooldown = policy.get('cooldown_hours')
    if type(rate) is not int or not (0 <= rate <= 100) or type(cooldown) is not int or cooldown < 0:
        return None  # 機率與冷卻尚未核准，不猜數字
    with transaction() as db:
        setup(db)
        executed = db.execute("""SELECT character_id, location_key FROM rp_location_action_events
            WHERE guild_id=? AND request_key=?""", (gid, request)).fetchone()
        if not executed or int(executed['character_id']) != cid or str(executed['location_key']) != key:
            return None
        previous = db.execute("""SELECT event_id FROM rp_official_location_event_rolls_v1
            WHERE guild_id=? AND request_key=?""", (gid, request)).fetchone()
        if previous:
            return event_detail(gid, request, db=db)
        candidates = [e for e in grouped.get(key, [])
                      if e.get('enabled') is True and e.get('trigger') == 'approved_location_action_completed']
        selected = None
        if candidates and rate:
            # 冷卻只根據已成功顯示的事件，整個伺服器內同角色跨地點共用。
            last = db.execute("""SELECT displayed_at FROM rp_official_location_event_rolls_v1
                WHERE guild_id=? AND character_id=? AND event_id IS NOT NULL
                  AND displayed_at IS NOT NULL
                ORDER BY displayed_at DESC LIMIT 1""", (gid,cid)).fetchone()
            in_cooldown = bool(last and cooldown and
                datetime.now(timezone.utc) - datetime.fromisoformat(last['displayed_at']) < timedelta(hours=cooldown))
            # 未完成顯示的事件預留一小段時間避免同時請求多抽；
            # 發送失敗不消耗冷卻，之後仍可重新抽取。
            pending = db.execute("""SELECT 1 FROM rp_official_location_event_rolls_v1
                WHERE guild_id=? AND character_id=? AND event_id IS NOT NULL
                  AND displayed_at IS NULL AND created_at>?
                LIMIT 1""",(gid,cid,(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat())).fetchone()
            if not in_cooldown and not pending and secrets.randbelow(100) < rate:
                selected=candidates[secrets.randbelow(len(candidates))]
        db.execute("""INSERT INTO rp_official_location_event_rolls_v1
            (guild_id,request_key,character_id,location_key,event_id,created_at)
            VALUES (?,?,?,?,?,?)""", (gid,request,cid,key,selected['event_id'] if selected else None,_stamp()))
        return event_detail(gid, request, db=db) if selected else None


def mark_displayed(guild_id, request_key, character_id):
    """Discord 已成功送出事件畫面後才記錄冷卻；重複通知不可刷新時間。"""
    gid, request, cid = str(guild_id), str(request_key), int(character_id)
    with transaction() as db:
        setup(db)
        row = db.execute("""SELECT event_id,displayed_at FROM rp_official_location_event_rolls_v1
            WHERE guild_id=? AND request_key=? AND character_id=?""",(gid,request,cid)).fetchone()
        if row is None or row['event_id'] is None:
            raise ValueError('沒有可標記的官方地點事件')
        if row['displayed_at'] is not None:
            return False
        # 同角色的其他地點已成功發出事件時，不允許重複累積冷卻。
        db.execute("""UPDATE rp_official_location_event_rolls_v1 SET displayed_at=?
            WHERE guild_id=? AND request_key=? AND character_id=? AND displayed_at IS NULL""",
            (_stamp(),gid,request,cid))
        return True


def event_detail(guild_id, request_key, *, db=None):
    if db is None:
        with transaction() as connection:
            return event_detail(guild_id,request_key,db=connection)
    setup(db)
    row=db.execute("""SELECT character_id,location_key,event_id FROM rp_official_location_event_rolls_v1
        WHERE guild_id=? AND request_key=?""",(str(guild_id),str(request_key))).fetchone()
    if not row or not row['event_id']:
        return None
    _, grouped=load()
    for item in grouped.get(str(row['location_key']), []):
        if item['event_id']==row['event_id']:
            return {**item, 'character_id':int(row['character_id']),'request_key':str(request_key)}
    return None


def choose(guild_id, request_key, character_id, user_id, choice_key):
    gid,cid=str(guild_id),int(character_id)
    with transaction() as db:
        setup(db)
        char=db.execute('SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?', (gid,cid)).fetchone()
        if not char or char['status']!='active' or str(char['owner_id'])!=str(user_id):
            raise PermissionError('僅原角色持有人可以選擇事件結果')
        event=event_detail(gid,request_key,db=db)
        if not event or event['character_id'] != cid:
            raise ValueError('事件不存在或不屬於此角色')
        old=db.execute("""SELECT choice_key,response FROM rp_official_location_event_choices_v1
            WHERE guild_id=? AND request_key=?""",(gid,str(request_key))).fetchone()
        if old:
            return {'choice_key':old['choice_key'],'response':old['response'],'duplicate':True}
        match=next((c for c in event['choices'] if c['key']==str(choice_key)),None)
        if match is None:
            raise ValueError('不是本事件提供的選項')
        response=str(match['response'])
        db.execute("""INSERT INTO rp_official_location_event_choices_v1
            VALUES (?,?,?,?,?,?,?)""",(gid,str(request_key),cid,event['event_id'],str(choice_key),response,_stamp()))
        return {'choice_key':str(choice_key),'response':response,'duplicate':False}
