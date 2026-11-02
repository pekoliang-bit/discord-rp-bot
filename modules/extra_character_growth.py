
"""每48小時路人動態成長：交易式結算，最多補7次；無金幣憑空增加。"""
from __future__ import annotations
import json
import random
import time
from core.database import transaction, get_connection
from modules.extra_character_rules import (
    GROWTH_HOURS, CATCHUP_LIMIT, ABILITY_GROWTH_CHANCE
)
from modules.attributes import default_schema

SECONDS = GROWTH_HOURS * 3600

def initialize(db):
    db.execute(
        '''CREATE TABLE IF NOT EXISTS extra_growth_clock(
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            last_cycle INTEGER NOT NULL,
            PRIMARY KEY(guild_id,character_id)
        )'''
    )
    db.execute(
        '''CREATE TABLE IF NOT EXISTS extra_growth_history(
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            cycle INTEGER NOT NULL,
            delta_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,character_id,cycle)
        )'''
    )

def bootstrap(db, gid, cid, now=None):
    initialize(db)
    tick = int(
        (time.time() if now is None else now) // SECONDS
    )
    db.execute(
        '''INSERT OR IGNORE INTO extra_growth_clock
           (guild_id,character_id,last_cycle)
           VALUES(?,?,?)''',
        (str(gid), int(cid), tick)
    )

def run_guild(gid, now=None):
    now = time.time() if now is None else now
    tick = int(now // SECONDS)

    with transaction() as db:
        initialize(db)
        rows = db.execute(
            """SELECT id,settings_json FROM characters
               WHERE guild_id=? AND status='active'
               AND json_extract(settings_json,'$.character_category')='extra'""",
            (str(gid),)
        ).fetchall()

        changed = 0
        for row in rows:
            cid = row['id']
            saved = db.execute(
                '''SELECT last_cycle FROM extra_growth_clock
                   WHERE guild_id=? AND character_id=?''',
                (str(gid), cid)
            ).fetchone()

            if not saved:
                bootstrap(db, gid, cid, now)
                continue

            last = int(saved['last_cycle'])
            cycles = min(
                CATCHUP_LIMIT,
                max(0, tick - last)
            )
            if cycles == 0:
                continue

            start = tick - cycles + 1
            settings = json.loads(
                row['settings_json'] or '{}'
            )

            talent = [
                a for a in default_schema()['talent']
            ]
            general = [
                a for a in default_schema()['general']
                if a not in (
                    '影響指數', '流量熱度', '商業價值'
                )
            ]

            for cycle in range(start, tick + 1):
                delta = {}

                if random.random() < ABILITY_GROWTH_CHANCE:
                    for keys, limit in (
                        (general, 5),
                        (talent, 10)
                    ):
                        remaining = random.randint(0, limit)
                        for _ in range(remaining):
                            if keys:
                                key = random.choice(keys)
                                delta[key] = delta.get(key, 0) + 1

                for stat, lo, hi in (
                    ('流量熱度', -.05, .08),
                    ('商業價值', -.01, .02)
                ):
                    old = db.execute(
                        '''SELECT value FROM attributes
                           WHERE guild_id=? AND character_id=?
                           AND name=?''',
                        (str(gid), cid, stat)
                    ).fetchone()

                    if old:
                        amount = round(
                            old['value'] * random.uniform(lo, hi)
                        )
                        if amount:
                            delta[stat] = amount

                tracking = db.execute(
                    '''SELECT loyal_fans,public_awareness
                       FROM character_system_tracking
                       WHERE guild_id=? AND character_id=?''',
                    (str(gid), cid)
                ).fetchone()

                if tracking:
                    fans_add = round(
                        tracking['loyal_fans']
                        * random.uniform(0, .02)
                    )
                    aware_add = round(
                        tracking['public_awareness']
                        * random.uniform(0, .01)
                    )

                    if fans_add or aware_add:
                        db.execute(
                            '''UPDATE character_system_tracking
                               SET loyal_fans=loyal_fans+?,
                                   public_awareness=public_awareness+?,
                                   updated_at=CURRENT_TIMESTAMP
                               WHERE guild_id=? AND character_id=?''',
                            (
                                fans_add, aware_add,
                                str(gid), cid
                            )
                        )
                        delta['粉絲'] = fans_add
                        delta['大眾認知度'] = aware_add

                for stat, amount in delta.items():
                    if stat in ('粉絲', '大眾認知度'):
                        continue
                    db.execute(
                        '''UPDATE attributes
                           SET value=MAX(0,value+?)
                           WHERE guild_id=? AND character_id=?
                           AND name=?''',
                        (int(amount), str(gid), cid, stat)
                    )

                db.execute(
                    '''INSERT OR IGNORE INTO extra_growth_history
                       (guild_id,character_id,cycle,delta_json)
                       VALUES(?,?,?,?)''',
                    (
                        str(gid), cid, cycle,
                        json.dumps(delta, ensure_ascii=False)
                    )
                )
                changed += 1

            db.execute(
                '''UPDATE extra_growth_clock
                   SET last_cycle=?
                   WHERE guild_id=? AND character_id=?''',
                (tick, str(gid), cid)
            )

    return {
        'characters': len(rows),
        'cycles_applied': changed
    }
