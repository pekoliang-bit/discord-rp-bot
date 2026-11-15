# 每個行動場所獨立設定寵物事件機率及事件池；未設定時使用可調預設。
from __future__ import annotations
import json
from core.database import transaction

DEFAULT = {'chance':5, 'pool':['stray_cat','stray_dog','missing_pet','nothing']}
ALLOWED = {'stray_cat','stray_dog','missing_pet','nothing'}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_location_events_v21 (
      guild_id TEXT NOT NULL, location_key TEXT NOT NULL,
      chance INTEGER NOT NULL DEFAULT 5, pool_json TEXT NOT NULL,
      PRIMARY KEY(guild_id,location_key), CHECK(chance BETWEEN 0 AND 100))''')


def get_rules(guild_id, location_key):
    with transaction() as db:
        initialize(db)
        row=db.execute('SELECT chance,pool_json FROM rp_pet_location_events_v21 WHERE guild_id=? AND location_key=?',
                       (str(guild_id),str(location_key))).fetchone()
        if row:
            try: pool=json.loads(row['pool_json'])
            except (TypeError,ValueError): pool=[]
            return {'chance':int(row['chance']),'pool':[x for x in pool if x in ALLOWED]}
    return {'chance':DEFAULT['chance'],'pool':list(DEFAULT['pool'])}


def configure(guild_id,location_key,chance,pool):
    chance=int(chance)
    if not 0<=chance<=100 or not isinstance(pool,list) or not pool or any(x not in ALLOWED for x in pool):
        raise ValueError('地點機率需為0～100，事件池必須使用允許的事件名稱')
    with transaction() as db:
        initialize(db)
        db.execute('''INSERT INTO rp_pet_location_events_v21(guild_id,location_key,chance,pool_json)
        VALUES(?,?,?,?) ON CONFLICT(guild_id,location_key) DO UPDATE SET
        chance=excluded.chance,pool_json=excluded.pool_json''',
        (str(guild_id),str(location_key),chance,json.dumps(pool,ensure_ascii=False)))
    return {'chance':chance,'pool':pool}
