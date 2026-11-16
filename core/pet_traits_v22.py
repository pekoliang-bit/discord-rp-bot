
"""Pet V2.2 behaviour engine. Provisional tunable balancing, approved architecture.

The original health/bond and ownership records remain authoritative. Traits are
private. Every mutation is guild/owner scoped and guarded by a DB transaction.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone

from core.database import transaction
from core import pets_v1 as pets
from core import pet_catalog_v21 as catalog

SPECIES_FIELDS = {
    '貓': (
        '破壞力', '耐心', '暴躁基值', '親人度', '好奇心',
        '獨立性', '狩獵本能', '警戒心', '學習力'
    ),
    '狗': (
        '破壞力', '耐心', '暴躁基值', '親人度', '活潑度',
        '服從性', '社交性', '警戒心', '學習力'
    ),
    '鸚鵡': (
        '破壞力', '耐心', '暴躁基值', '親人度', '好奇心',
        '社交需求', '模仿能力', '警戒心', '學習力'
    )
}

BASE = {
    '英國短毛貓': (30, 70, 25, 60, 45, 65, 50, 40, 60),
    '美國短毛貓': (45, 60, 30, 65, 70, 60, 75, 40, 70),
    '布偶貓': (25, 75, 20, 85, 50, 35, 45, 25, 65),
    '緬因貓': (45, 65, 25, 75, 70, 55, 70, 35, 75),
    '波斯貓': (25, 55, 30, 65, 40, 60, 35, 45, 50),
    '暹羅貓': (55, 45, 45, 80, 85, 40, 75, 45, 85),
    '曼赤肯貓': (40, 55, 30, 70, 75, 45, 60, 40, 65),
    '俄羅斯藍貓': (30, 65, 25, 65, 60, 65, 60, 65, 75),
    '柴犬': (50, 45, 40, 65, 70, 45, 50, 70, 70),
    '柯基': (55, 50, 35, 80, 85, 65, 70, 60, 75),
    '貴賓犬': (40, 65, 25, 85, 75, 80, 80, 45, 90),
    '博美犬': (40, 45, 40, 80, 75, 60, 60, 70, 75),
    '黃金獵犬': (45, 80, 20, 90, 75, 85, 90, 35, 85),
    '哈士奇': (85, 35, 40, 80, 95, 40, 80, 45, 75),
    '薩摩耶': (65, 55, 30, 85, 85, 60, 85, 45, 75),
    '邊境牧羊犬': (70, 70, 25, 80, 95, 90, 70, 60, 95),
    '德國牧羊犬': (65, 75, 30, 80, 80, 90, 60, 85, 90),
    '秋田犬': (60, 60, 35, 75, 65, 60, 45, 85, 75),
    '虎皮鸚鵡': (25, 55, 30, 70, 75, 85, 65, 55, 65),
    '玄鳳鸚鵡': (30, 65, 25, 80, 65, 85, 65, 55, 70),
    '牡丹鸚鵡': (55, 40, 45, 75, 80, 90, 45, 60, 75),
    '和尚鸚鵡': (65, 45, 45, 75, 85, 90, 85, 65, 85),
    '綠頰錐尾鸚鵡': (60, 45, 40, 85, 90, 90, 70, 50, 85),
    '非洲灰鸚鵡': (65, 60, 35, 75, 85, 85, 95, 70, 95)
}

SETTINGS = {
    'variation': 20,
    'fatigue_hourly_recovery': 5,
    'rest_bonus_6h': 10,
    'anger_hourly_recovery': 2,
    'daily_total_growth': 12,
    'daily_single_growth': 5,
    'daily_skill_growth': 20,
    'injury_daily_cap': 15,
    'destruction_daily_cap': 2,
    'quality_wear': {
        'basic': 1.0, 'premium': .8, 'luxury': .5,
        'T1': 1.5, 'T2': 1.0, 'T3': .75, 'T4': .5, 'T5': .3,
    },
    'wear_base': {
        'cage': 2,
        'bed': 2,
        'toilet': 2,
        'bowl': 1,
        'carrier': 3,
        'leash': 3,
        'toy': 4,
        'groom': 2,
        'daily': 2
    },
    'training': {
        '耐心培養': ('耐心', 10, 45, 5),
        '服從訓練': ('服從性', 10, 45, 5),
        '防止破壞': ('自制能力', 10, 45, 5),
        '情緒調節': ('情緒調節', 10, 45, 5),
        '環境適應': ('環境適應', 10, 45, 5),
        '益智訓練': ('學習力', 15, 35, 10),
        '親密互動': ('親人度', 6, 55, 0),
        '基礎指令': ('技能:基礎指令', 6, 55, 0),
        '進階技能': ('技能:進階技能', 15, 25, 20)
    }
}


def _utc():
    return datetime.now(timezone.utc)


def _stamp(t):
    return t.isoformat()


def _time(s):
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return _utc()


def _clamp(x, lo=0, hi=100):
    return max(lo, min(hi, int(x)))


def _rng(rng=None):
    return rng if rng is not None else random.SystemRandom()


def initialize(db):
    pets.initialize(db)

    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_traits_v22 (
      guild_id TEXT NOT NULL,
      pet_id INTEGER NOT NULL,
      breed TEXT NOT NULL,
      traits_json TEXT NOT NULL,
      anger INTEGER NOT NULL,
      fatigue INTEGER NOT NULL DEFAULT 0,
      self_control INTEGER NOT NULL DEFAULT 20,
      emotion_control INTEGER NOT NULL DEFAULT 20,
      adaptability INTEGER NOT NULL DEFAULT 20,
      last_recovery TEXT NOT NULL,
      created_at TEXT NOT NULL,
      PRIMARY KEY(guild_id,pet_id))''')

    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_training_v22 (
      guild_id TEXT NOT NULL,
      pet_id INTEGER NOT NULL,
      skill TEXT NOT NULL,
      experience INTEGER NOT NULL DEFAULT 0,
      mastery INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(guild_id,pet_id,skill))''')

    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_growth_v22 (
      guild_id TEXT NOT NULL,
      pet_id INTEGER NOT NULL,
      day TEXT NOT NULL,
      stat TEXT NOT NULL,
      amount INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(guild_id,pet_id,day,stat))''')

    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_items_v22 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      guild_id TEXT NOT NULL,
      pet_id INTEGER NOT NULL,
      owner_character_id INTEGER NOT NULL,
      code TEXT NOT NULL,
      durability INTEGER NOT NULL DEFAULT 100,
      warning_sent INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL)''')
    existing = {r[1] for r in db.execute('PRAGMA table_info(rp_pet_items_v22)')}
    if 'use_count' not in existing:
        db.execute('ALTER TABLE rp_pet_items_v22 ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0')

    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_incidents_v22 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      guild_id TEXT NOT NULL,
      pet_id INTEGER NOT NULL,
      character_id INTEGER NOT NULL,
      kind TEXT NOT NULL,
      health_loss INTEGER NOT NULL DEFAULT 0,
      detail_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL)''')

    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_notice_queue_v22 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      guild_id TEXT NOT NULL,
      pet_id INTEGER NOT NULL,
      character_id INTEGER NOT NULL,
      message TEXT NOT NULL,
      sent_at TEXT,
      created_at TEXT NOT NULL)''')


def _breed_label(db, gid, pet):
    code = pet['rarity']

    if code.startswith('v21:'):
        row = db.execute(
            '''SELECT label FROM rp_pet_catalog_v2
            WHERE guild_id=? AND kind=? AND code=?''',
            (gid, 'breed', code)
        ).fetchone()

        if row:
            return row['label']

        for candidate, species, name, price, active in catalog.BREEDS:
            if candidate == code:
                return name

    if pet['species'] in ('貓', '狗'):
        return '米克斯'

    return str(pet['species'])


def _create(db, gid, pet_id, rng=None, timestamp=None):
    old = db.execute(
        '''SELECT * FROM rp_pet_traits_v22
        WHERE guild_id=? AND pet_id=?''',
        (gid, pet_id)
    ).fetchone()

    if old:
        return old

    pet = pets._pet(db, gid, pet_id)
    species = pet['species']

    if species not in SPECIES_FIELDS:
        return None

    breed = _breed_label(db, gid, pet)
    base = BASE.get(breed, (50, 50, 35, 60, 60, 55, 55, 50, 65))
    rand = _rng(rng)

    values = {
        field: _clamp(
            value + rand.randint(
                -SETTINGS['variation'],
                SETTINGS['variation']
            )
        )
        for field, value in zip(SPECIES_FIELDS[species], base)
    }

    anger = _clamp(
        values['暴躁基值'] + rand.randint(-10, 10)
    )
    time = _stamp(timestamp or _utc())

    db.execute(
        '''INSERT OR IGNORE INTO rp_pet_traits_v22
        (guild_id,pet_id,breed,traits_json,anger,last_recovery,created_at)
        VALUES (?,?,?,?,?,?,?)''',
        (
            gid,
            pet_id,
            breed,
            json.dumps(values, ensure_ascii=False),
            anger,
            time,
            time
        )
    )

    return db.execute(
        '''SELECT * FROM rp_pet_traits_v22
        WHERE guild_id=? AND pet_id=?''',
        (gid, pet_id)
    ).fetchone()


def bootstrap(db, gid, pet_id, rng=None):
    initialize(db)
    return _create(db, str(gid), int(pet_id), rng)


def _recover(db, gid, pid, row, timestamp=None):
    timestamp = timestamp or _utc()
    previous = _time(row['last_recovery'])

    hours = max(
        0,
        int((timestamp - previous).total_seconds() // 3600)
    )

    if not hours:
        return row

    old_bucket = int(previous.timestamp() // 21600)
    new_bucket = int(
        (previous.timestamp() + 3600 * hours) // 21600
    )

    fatigue = _clamp(
        row['fatigue']
        - SETTINGS['fatigue_hourly_recovery'] * hours
        - SETTINGS['rest_bonus_6h'] * (new_bucket - old_bucket)
    )

    traits = json.loads(row['traits_json'])
    baseline = traits['暴躁基值']

    anger = max(
        baseline,
        _clamp(
            row['anger'] - SETTINGS['anger_hourly_recovery'] * hours
        )
    )

    from datetime import timedelta
    newtime = previous + timedelta(hours=hours)

    db.execute(
        '''UPDATE rp_pet_traits_v22
        SET fatigue=?,anger=?,last_recovery=?
        WHERE guild_id=? AND pet_id=?''',
        (
            fatigue,
            anger,
            _stamp(newtime),
            gid,
            pid
        )
    )

    return db.execute(
        '''SELECT * FROM rp_pet_traits_v22
        WHERE guild_id=? AND pet_id=?''',
        (gid, pid)
    ).fetchone()


def state(guild_id, pet_id, character_id, user_id):
    gid = str(guild_id)
    pid = int(pet_id)

    with transaction() as db:
        initialize(db)
        pets._actor(db, gid, character_id, user_id)
        pets._access(db, gid, pid, character_id)

        row = _recover(
            db, gid, pid, _create(db, gid, pid)
        )

        if row is None:
            raise ValueError('未支援的寵物物種')

        equipped = [
            dict(x)
            for x in db.execute(
                '''SELECT id,code,durability
                FROM rp_pet_items_v22
                WHERE guild_id=? AND pet_id=?
                ORDER BY id''',
                (gid, pid)
            )
        ]

        return {
            'name': pets._pet(db, gid, pid)['name'],
            'breed': row['breed'],
            'anger': row['anger'],
            'fatigue': row['fatigue'],
            'equipment': equipped,
            'traits_hidden': True
        }


def _log(db, gid, pid, cid, kind, loss=0, detail=None):
    db.execute(
        '''INSERT INTO rp_pet_incidents_v22
        (guild_id,pet_id,character_id,kind,health_loss,detail_json,created_at)
        VALUES (?,?,?,?,?,?,?)''',
        (
            gid, pid, cid, kind, loss,
            json.dumps(detail or {}, ensure_ascii=False),
            _stamp(_utc())
        )
    )


def _notice(db, gid, pid, cid, message):
    db.execute(
        '''INSERT INTO rp_pet_notice_queue_v22
        (guild_id,pet_id,character_id,message,created_at)
        VALUES (?,?,?,?,?)''',
        (
            gid, pid, cid, message, _stamp(_utc())
        )
    )


def attach_purchased(db, gid, pid, cid, items):
    # Initial pet gets one of each function; spares remain in inventory.

    # No copying or five-fold buff stacking.
    initialize(db)
    _create(db,str(gid),int(pid))
    allocated=set()
    for item in items:
        if item['kind']!='item': continue
        info=db.execute('''SELECT permanent,required_group,subcategory
          FROM rp_pet_catalog_v2 WHERE guild_id=? AND kind='item' AND code=?''',
          (str(gid),item['code'])).fetchone()
        if not info or not info['permanent']:continue
        group=(info['required_group'],info['subcategory'] or info['required_group'])
        if group in allocated:continue
        available=db.execute('''UPDATE rp_pet_inventory_v2 SET quantity=quantity-1
          WHERE guild_id=? AND character_id=? AND code=? AND quantity>0''',
          (str(gid),int(cid),item['code']))
        if available.rowcount!=1:continue
        db.execute('''INSERT INTO rp_pet_items_v22
          (guild_id,pet_id,owner_character_id,code,created_at)
          VALUES (?,?,?,?,?)''',
          (str(gid),int(pid),int(cid),item['code'],_stamp(_utc())))
        allocated.add(group)


def equip(guild_id, pid, cid, uid, code):
    gid = str(guild_id)
    pid = int(pid)
    cid = int(cid)

    with transaction() as db:
        initialize(db)
        pets._actor(db, gid, cid, uid)
        pets._access(db, gid, pid, cid)

        item = db.execute(
            '''SELECT permanent,species FROM rp_pet_catalog_v2
            WHERE guild_id=? AND kind=? AND code=?''',
            (gid, 'item', code)
        ).fetchone()

        if (
            not item
            or not item['permanent']
            or item['species'] != pets._pet(db, gid, pid)['species']
        ):
            raise ValueError('該用品不適合這隻寵物')

        result = db.execute(
            '''UPDATE rp_pet_inventory_v2
            SET quantity=quantity-1
            WHERE guild_id=? AND character_id=? AND code=?
            AND quantity>0''',
            (gid, cid, code)
        )

        if result.rowcount != 1:
            raise ValueError('背包內沒有此耐久用品')

        cur = db.execute(
            '''INSERT INTO rp_pet_items_v22
            (guild_id,pet_id,owner_character_id,code,created_at)
            VALUES (?,?,?,?,?)''',
            (gid, pid, cid, code, _stamp(_utc()))
        )

        return cur.lastrowid


def _assign_from_inventory(db, gid, pid, cid, group):
    # Automatically allocate ONE owner-held spare when the pet has no usable gear.

    # Quantity is never duplicated or assigned to multiple pets.
    pet=pets._pet(db,gid,pid)
    existing=db.execute('''SELECT 1 FROM rp_pet_items_v22 i
       JOIN rp_pet_catalog_v2 c ON c.guild_id=i.guild_id AND c.kind='item' AND c.code=i.code
       WHERE i.guild_id=? AND i.pet_id=? AND i.durability>0 AND c.required_group=?
       LIMIT 1''',(gid,pid,group)).fetchone()
    if existing: return
    stock=db.execute('''SELECT i.code FROM rp_pet_inventory_v2 i
       JOIN rp_pet_catalog_v2 c ON c.guild_id=i.guild_id AND c.kind='item' AND c.code=i.code
       WHERE i.guild_id=? AND i.character_id=? AND i.quantity>0
         AND c.permanent=1 AND c.required_group=? AND c.species=?
       ORDER BY i.code LIMIT 1''',(gid,cid,group,pet['species'])).fetchone()
    if not stock:return
    code=stock['code']
    moved=db.execute('''UPDATE rp_pet_inventory_v2 SET quantity=quantity-1
      WHERE guild_id=? AND character_id=? AND code=? AND quantity>0''',(gid,cid,code))
    if moved.rowcount!=1:return
    db.execute('''INSERT INTO rp_pet_items_v22
      (guild_id,pet_id,owner_character_id,code,created_at) VALUES (?,?,?,?,?)''',
      (gid,pid,cid,code,_stamp(_utc())))


def _wear(db, gid, pid, cid, action, traits, row, rng):
    mapping = {
        'play': 'toy',
        'walk': 'leash',
        'feed': 'bowl',
        'wrong_food': 'bowl',
        'train': 'toy'
    }

    group = mapping.get(action)

    if not group:
        return []

    _assign_from_inventory(db,gid,pid,cid,group)
    gear = db.execute(
        '''SELECT i.id,i.code,i.durability,i.warning_sent,i.use_count,
                  COALESCE(c.quality,'T2') AS quality
        FROM rp_pet_items_v22 i
        LEFT JOIN rp_pet_catalog_v2 c
          ON c.guild_id=i.guild_id AND c.kind='item' AND c.code=i.code
        WHERE i.guild_id=? AND i.pet_id=? AND i.durability>0
          AND (c.required_group=? OR (c.code IS NULL AND i.code LIKE ?))
        ORDER BY i.use_count DESC, i.durability ASC, i.created_at ASC, i.id ASC
        LIMIT 1''',
        (gid, pid, group, f'v21:%:{group}:%')
    ).fetchone()

    if not gear:
        return []

    quality = gear['quality']
    q = SETTINGS['quality_wear'].get(quality, 1)

    factor = max(
        .5,
        min(
            2.5,
            1
            + (traits['破壞力'] - 50) / 100
            - (traits['耐心'] - 50) / 200
        )
    )

    normal = max(
        1,
        math.ceil(
            SETTINGS['wear_base'].get(group, 2)
            * factor * q
        )
    )

    chance = max(
        0,
        min(
            25,
            2
            + .12 * traits['破壞力']
            + .08 * row['anger']
            - .08 * traits['耐心']
            - .10 * row['self_control']
        )
    )

    day = _utc().date().isoformat()

    count = db.execute(
        '''SELECT COUNT(*) FROM rp_pet_incidents_v22
        WHERE guild_id=? AND pet_id=? AND kind=?
        AND substr(created_at,1,10)=?''',
        (gid, pid, 'destroy', day)
    ).fetchone()[0]

    extra = (
        rng.randint(3, 25)
        if (
            count < SETTINGS['destruction_daily_cap']
            and rng.random() * 100 < chance
        )
        else 0
    )

    remaining = max(
        0, gear['durability'] - normal - extra
    )

    db.execute(
        '''UPDATE rp_pet_items_v22
        SET durability=?,warning_sent=?,use_count=use_count+1
        WHERE id=?''',
        (
            remaining,
            1 if remaining < 30 else gear['warning_sent'],
            gear['id']
        )
    )

    notes = []

    if (
        not gear['warning_sent']
        and remaining < 30
        and remaining > 0
    ):
        message = (
            f'🐾 寵物用品 {gear["code"]} '
            f'耐久剩餘 {remaining}/100，請準備維修或更換。'
        )
        _notice(db, gid, pid, cid, message)
        notes.append(message)

    if remaining == 0 and gear['durability'] > 0:
        message = (
            f'🐾 寵物用品 {gear["code"]} '
            '已損壞（0/100），不能繼續使用。'
        )
        _notice(db, gid, pid, cid, message)
        notes.append(message)

    if extra:
        _log(
            db, gid, pid, cid, 'destroy',
            detail={
                'item': gear['id'],
                'damage': extra
            }
        )

    return notes


def _injury(db, gid, pid, cid, row, traits, rng, action):
    if action not in ('play', 'walk', 'groom', 'train'):
        return 0

    pet = pets._pet(db, gid, pid)
    anger = row['anger']

    base = (
        1 if anger < 30
        else 3 if anger < 60
        else 8 if anger < 80
        else 15
    )

    risk = min(
        35,
        base
        + (5 if row['fatigue'] >= 70 else 0)
        + (8 if pet['illness'] else 0)
        - (3 if traits['耐心'] >= 80 else 0)
        - (3 if row['emotion_control'] >= 80 else 0)
    )

    if rng.random() * 100 >= max(0, risk):
        return 0

    draw = rng.random() * 100

    loss = (
        0 if draw < 65
        else rng.randint(1, 3) if draw < 90
        else rng.randint(4, 7) if draw < 99
        else rng.randint(8, 12)
    )

    if not loss:
        return 0

    day = _utc().date().isoformat()

    spent = db.execute(
        '''SELECT COALESCE(SUM(health_loss),0)
        FROM rp_pet_incidents_v22
        WHERE guild_id=? AND character_id=?
        AND substr(created_at,1,10)=?''',
        (gid, cid, day)
    ).fetchone()[0]

    loss = min(
        loss,
        max(0, SETTINGS['injury_daily_cap'] - spent)
    )

    if not loss:
        return 0

    attr = db.execute(
        """SELECT name,value FROM attributes
        WHERE guild_id=? AND character_id=?
        AND name IN ('健康值','健康')
        ORDER BY CASE name
            WHEN '健康值' THEN 0
            ELSE 1
        END
        LIMIT 1""",
        (gid, cid)
    ).fetchone()

    if not attr:
        return 0

    loss = min(loss, int(attr['value']))

    if loss:
        db.execute(
            '''UPDATE attributes
            SET value=value-?,version=version+1
            WHERE guild_id=? AND character_id=? AND name=?
            AND value>=?''',
            (
                loss, gid, cid,
                attr['name'], loss
            )
        )

        _log(
            db, gid, pid, cid, 'injury',
            loss, {'action': action}
        )

    return loss


def interaction(guild_id, pid, cid, uid, action, rng=None):
    gid = str(guild_id)
    pid = int(pid)
    cid = int(cid)
    rng = _rng(rng)

    with transaction() as db:
        initialize(db)
        pets._actor(db, gid, cid, uid)
        pets._access(db, gid, pid, cid)
        if pets._hospitalized(db, gid, pid):
            raise ValueError('寵物住院中，不可進行一般互動，請使用醫療探病')

        row = _recover(
            db, gid, pid, _create(db, gid, pid)
        )
        traits = json.loads(row['traits_json'])

        notes = _wear(
            db, gid, pid, cid,
            action, traits, row, rng
        )

        change = (
            -rng.randint(3, 6)
            if action in ('play', 'walk')
            else rng.randint(3, 8)
            if action == 'wrong_food'
            else 0
        )

        if change:
            db.execute(
                '''UPDATE rp_pet_traits_v22
                SET anger=?
                WHERE guild_id=? AND pet_id=?''',
                (
                    _clamp(row['anger'] + change),
                    gid, pid
                )
            )

        injury = _injury(
            db, gid, pid, cid,
            row, traits, rng, action
        )

        return {
            'messages': notes,
            'injury': injury
        }


def training(guild_id,pid,cid,uid,course,professional=False,rng=None):
    if professional:
        raise ValueError('專業訓練請先到專業課程中心報名，完成時間後統一結算')
    with transaction() as db:
        return _training_in_transaction(db,str(guild_id),int(pid),int(cid),uid,course,False,_rng(rng))


def _training_in_transaction(db,gid,pid,cid,uid,course,professional,rng):
    # Only the paid-course service may invoke professional=True inside a transaction.
    if course not in SETTINGS['training']:
        raise ValueError('未知的訓練項目')
    initialize(db)
    pets._actor(db, gid, cid, uid)
    pets._access(db, gid, pid, cid)

    pet = pets._pet(db, gid, pid)
    if pets._hospitalized(db, gid, pid):
        raise ValueError('寵物住院中，不能參加一般訓練')

    if (
        pet['state'] != 'home'
        or pet['health'] == 0
        or pet['illness']
    ):
        raise ValueError(
            '寵物需要先恢復健康並待在家中才能接受訓練'
        )

    row = _recover(
        db, gid, pid, _create(db, gid, pid)
    )
    traits = json.loads(row['traits_json'])

    target, fatigue, base, difficulty = (
        SETTINGS['training'][course]
    )

    if target == '服從性' and pet['species'] != '狗':
        raise ValueError('這門課程只適用於狗狗')

    if row['anger'] >= 80:
        raise ValueError(
            '寵物情緒緊繃，先休息或安撫'
        )

    cost = fatigue + (5 if professional else 0)

    if row['fatigue'] + cost >= 90:
        raise ValueError(
            '寵物疲勞過高，需要休息'
        )


    chance = max(
        5,
        min(
            95,
            base
            + .25 * traits['學習力']
            + .15 * traits['耐心']
            - (5 if row['fatigue'] >= 30 else 0)
            - (15 if row['fatigue'] >= 50 else 0)
            - (30 if row['fatigue'] >= 70 else 0)
            - (10 if row['anger'] >= 60 else 0)
            - difficulty
        )
    )

    if professional:
        chance = min(95, chance + (20 if course=='進階技能' else 15 if course=='益智訓練' else 10))
    success = rng.random() * 100 < chance
    gained = 0
    skill_gain = 0
    today = _utc().date().isoformat()

    if success:
        if target.startswith('技能:'):
            old = db.execute(
                '''SELECT mastery
                FROM rp_pet_training_v22
                WHERE guild_id=? AND pet_id=? AND skill=?''',
                (gid, pid, target)
            ).fetchone()

            used = db.execute(
                """SELECT COALESCE(SUM(amount),0)
                FROM rp_pet_growth_v22
                WHERE guild_id=? AND pet_id=? AND day=?
                AND stat LIKE '技能:%'""",
                (gid, pid, today)
            ).fetchone()[0]

            skill_gain = min(
                min(10, math.ceil((5 if rng.random()<.1 else 3)*(2 if professional and course=='進階技能' else 1.75 if professional and course=='益智訓練' else 1.5 if professional else 1))),
                SETTINGS['daily_skill_growth'] - used,
                100 - (old['mastery'] if old else 0)
            )

            if skill_gain > 0:
                db.execute(
                    '''INSERT INTO rp_pet_training_v22
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(guild_id,pet_id,skill)
                    DO UPDATE SET
                        experience=experience+excluded.experience,
                        mastery=MIN(100,mastery+excluded.mastery)''',
                    (
                        gid, pid, target,
                        skill_gain, skill_gain
                    )
                )

        else:
            single = db.execute(
                '''SELECT amount FROM rp_pet_growth_v22
                WHERE guild_id=? AND pet_id=?
                AND day=? AND stat=?''',
                (gid, pid, today, target)
            ).fetchone()

            total = db.execute(
                """SELECT COALESCE(SUM(amount),0)
                FROM rp_pet_growth_v22
                WHERE guild_id=? AND pet_id=? AND day=?
                AND stat NOT LIKE '技能:%'""",
                (gid, pid, today)
            ).fetchone()[0]

            additional = {
                '自制能力': row['self_control'],
                '情緒調節': row['emotion_control'],
                '環境適應': row['adaptability']
            }

            current = traits.get(
                target, additional.get(target, 0)
            )

            gained = max(
                0,
                min(
                    min(5, math.ceil(rng.randint(1,3)*(2 if professional and course=='進階技能' else 1.75 if professional and course=='益智訓練' else 1.5 if professional else 1))),
                    SETTINGS['daily_single_growth']
                    - (single['amount'] if single else 0),
                    SETTINGS['daily_total_growth'] - total,
                    100 - current
                )
            )

            if gained:
                if target in traits:
                    traits[target] += gained

                    db.execute(
                        '''UPDATE rp_pet_traits_v22
                        SET traits_json=?
                        WHERE guild_id=? AND pet_id=?''',
                        (
                            json.dumps(
                                traits, ensure_ascii=False
                            ),
                            gid, pid
                        )
                    )

                else:
                    col = {
                        '自制能力': 'self_control',
                        '情緒調節': 'emotion_control',
                        '環境適應': 'adaptability'
                    }[target]

                    db.execute(
                        f'''UPDATE rp_pet_traits_v22
                        SET {col}={col}+?
                        WHERE guild_id=? AND pet_id=?''',
                        (gained, gid, pid)
                    )

        growth = gained or skill_gain

        if growth:
            db.execute(
                '''INSERT INTO rp_pet_growth_v22
                VALUES (?,?,?,?,?)
                ON CONFLICT(guild_id,pet_id,day,stat)
                DO UPDATE SET
                    amount=amount+excluded.amount''',
                (
                    gid, pid, today,
                    target, growth
                )
            )

    anger = _clamp(
        row['anger']
        + (
            rng.randint(4, 8)
            if not success and row['fatigue'] >= 50
            else 0
        )
    )

    db.execute(
        '''UPDATE rp_pet_traits_v22
        SET fatigue=?,anger=?
        WHERE guild_id=? AND pet_id=?''',
        (
            _clamp(row['fatigue'] + cost),
            anger,
            gid,
            pid
        )
    )

    _log(
        db, gid, pid, cid, 'training',
        detail={
            'course': course,
            'success': success,
            'growth': gained,
            'skill': skill_gain
        }
    )

    return {
        'success': success,
        'growth': gained,
        'skill_gain': skill_gain,
        'fatigue': row['fatigue'] + cost,
        'chance': round(chance, 1)
    }



def repair(
    guild_id, pid, cid, uid,
    item_id, restore_to=100
):
    from core.database import get_or_create_account
    from core.economy import _payment

    gid = str(guild_id)
    pid = int(pid)
    cid = int(cid)

    with transaction() as db:
        initialize(db)
        pets._actor(db, gid, cid, uid)
        pets._access(db, gid, pid, cid)

        item = db.execute(
            '''SELECT * FROM rp_pet_items_v22
            WHERE guild_id=? AND pet_id=? AND id=?''',
            (gid, pid, int(item_id))
        ).fetchone()

        if not item:
            raise LookupError('找不到寵物用品')

        target = _clamp(restore_to)

        if int(item['durability']) <= 0:
            raise ValueError('用品耐久歸零即永久損壞，不能維修')
        if target <= item['durability']:
            raise ValueError(
                '維修目標必須高於目前耐久'
            )

        row = db.execute(
            '''SELECT price,quality FROM rp_pet_catalog_v2
            WHERE guild_id=? AND kind=? AND code=?''',
            (gid, 'item', item['code'])
        ).fetchone()

        if not row:
            raise LookupError('找不到原商品售價')

        repair_rates = {'T1':.40,'T2':.35,'T3':.30,'T4':.25,'T5':.20,
                        'basic':.35,'premium':.30,'luxury':.25}
        cost = math.ceil(
            row['price'] * (target - item['durability']) / 100
            * repair_rates.get(row['quality'], .35)
        )

        sender = get_or_create_account(
            db, gid, 'character', cid, 'wallet'
        )
        receiver = get_or_create_account(
            db, gid, 'system', 'pet_center', 'wallet'
        )

        if cost:
            _payment(
                db, gid,
                f'pet:repair:{gid}:{item_id}:{_utc().timestamp()}',
                sender,
                receiver,
                cost,
                '寵物用品維修'
            )

        db.execute(
            '''UPDATE rp_pet_items_v22
            SET durability=?,warning_sent=?
            WHERE id=?''',
            (
                target,
                0 if target >= 30 else item['warning_sent'],
                item_id
            )
        )

        return {
            'cost': cost,
            'durability': target
        }


def daily_equipment(guild_id, timestamp=None):
    gid = str(guild_id)
    stamp = timestamp or _utc()
    day = stamp.date().isoformat()
    processed = 0

    with transaction() as db:
        initialize(db)

        db.execute(
            '''CREATE TABLE IF NOT EXISTS rp_pet_equipment_days_v22 (
            guild_id TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            PRIMARY KEY(guild_id,item_id,day))'''
        )

        gear = db.execute(
            '''SELECT i.*,p.species,p.name
            FROM rp_pet_items_v22 i
            JOIN rp_pets_v1 p
              ON p.guild_id=i.guild_id
             AND p.id=i.pet_id
            WHERE i.guild_id=?
              AND i.durability>0
              AND p.state='home' ''',
            (gid,)
        ).fetchall()

        for item in gear:
            if pets._hospitalized(db, gid, item['pet_id']):
                continue
            parts = item['code'].split(':')

            meta=db.execute('SELECT required_group,quality FROM rp_pet_catalog_v2 WHERE guild_id=? AND kind=? AND code=?',
                            (gid,'item',item['code'])).fetchone()
            group=meta['required_group'] if meta else (parts[-2] if len(parts)>=4 else '')
            quality=meta['quality'] if meta else (parts[-1] if len(parts)>=4 else 'T2')
            if group not in ('cage','bed','toilet','bowl','daily'):
                continue
            # Only one piece of each function/subcategory takes daily wear;
            # spares do not degrade while waiting in the closet.
            key=db.execute('''SELECT subcategory FROM rp_pet_catalog_v2
               WHERE guild_id=? AND kind='item' AND code=?''',
               (gid,item['code'])).fetchone()
            sub=key['subcategory'] if key else group
            alternatives=db.execute('''SELECT i.id,i.use_count,i.durability,i.created_at
              FROM rp_pet_items_v22 i LEFT JOIN rp_pet_catalog_v2 c
                ON c.guild_id=i.guild_id AND c.kind='item' AND c.code=i.code
              WHERE i.guild_id=? AND i.pet_id=? AND i.durability>0
                AND (c.subcategory=? OR (c.code IS NULL AND i.code LIKE ?))
              ORDER BY i.use_count DESC,i.durability ASC,i.created_at ASC,i.id ASC
              LIMIT 1''',(gid,item['pet_id'],sub,f'v21:%:{group}:%')).fetchone()
            if alternatives and int(alternatives['id'])!=int(item['id']):
                continue

            if db.execute(
                '''SELECT 1 FROM rp_pet_equipment_days_v22
                WHERE guild_id=? AND item_id=? AND day=?''',
                (gid, item['id'], day)
            ).fetchone():
                continue

            row = _recover(
                db, gid, item['pet_id'],
                _create(db, gid, item['pet_id']),
                stamp
            )

            traits = json.loads(row['traits_json'])

            factor = max(
                .5,
                min(
                    2.5,
                    1
                    + (traits['破壞力'] - 50) / 100
                    - (traits['耐心'] - 50) / 200
                )
            )

            amount = max(
                1,
                math.ceil(
                    SETTINGS['wear_base'].get(group, 2)
                    * factor
                    * SETTINGS['quality_wear'].get(quality, 1)
                )
            )

            remaining = max(
                0, item['durability'] - amount
            )

            db.execute(
                '''UPDATE rp_pet_items_v22
                SET durability=?,warning_sent=?
                WHERE id=?''',
                (
                    remaining,
                    int(
                        item['warning_sent']
                        or remaining < 30
                    ),
                    item['id']
                )
            )

            db.execute(
                '''INSERT INTO rp_pet_equipment_days_v22
                VALUES (?,?,?)''',
                (gid, item['id'], day)
            )

            if (
                remaining < 30
                and not item['warning_sent']
                and remaining > 0
            ):
                _notice(
                    db, gid, item['pet_id'],
                    item['owner_character_id'],
                    f'🐾 {item["name"]} 的 {item["code"]} '
                    f'耐久剩餘 {remaining}/100，快要損壞了。'
                )

            if remaining == 0:
                _notice(
                    db, gid, item['pet_id'],
                    item['owner_character_id'],
                    f'🐾 {item["name"]} 的 {item["code"]} '
                    '已損壞（0/100）。'
                )

            processed += 1

    return processed


def dispatch_notices(limit=50):
    from modules.notifications import queue_notification
    from core.database import get_connection

    with get_connection() as db:
        exists = db.execute(
            """SELECT 1 FROM sqlite_master
            WHERE type='table'
              AND name='rp_pet_notice_queue_v22'"""
        ).fetchone()

        if not exists:
            return 0

        rows = [
            dict(r)
            for r in db.execute(
                '''SELECT *
                FROM rp_pet_notice_queue_v22
                WHERE sent_at IS NULL
                ORDER BY id LIMIT ?''',
                (limit,)
            )
        ]

    done = 0

    for item in rows:
        try:
            queue_notification(
                int(item['guild_id']),
                'private',
                'other',
                item['message'],
                character_id=int(item['character_id']),
                priority='high',
                dedupe_key=(
                    f'pet-v22:{item["guild_id"]}:{item["id"]}'
                )
            )
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                '寵物通知等待重試 id=%s',
                item['id']
            )
            continue

        with transaction() as db:
            db.execute(
                '''UPDATE rp_pet_notice_queue_v22
                SET sent_at=?
                WHERE id=? AND sent_at IS NULL''',
                (
                    _stamp(_utc()),
                    item['id']
                )
            )

        done += 1

    return done
