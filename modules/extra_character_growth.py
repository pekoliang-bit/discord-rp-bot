
"""路人每角色獨立48小時結算；至多補7期，交易內防重複。"""
from __future__ import annotations

import json
import random
import time

from core.database import transaction
from modules.attributes import default_schema
from modules.extra_character_rules import (
    GROWTH_HOURS,
    CATCHUP_LIMIT,
    ABILITY_GROWTH_CHANCE,
)

SECONDS = GROWTH_HOURS * 3600


def initialize(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS extra_growth_clock(
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            last_cycle INTEGER NOT NULL DEFAULT 0,
            next_due_at INTEGER,
            PRIMARY KEY(guild_id,character_id)
        )"""
    )

    columns = {
        r[1]
        for r in db.execute(
            "PRAGMA table_info(extra_growth_clock)"
        )
    }

    if "next_due_at" not in columns:
        db.execute(
            "ALTER TABLE extra_growth_clock "
            "ADD COLUMN next_due_at INTEGER"
        )

    # 將舊版的固定時段計時轉換為下次結算時間。
    db.execute(
        """UPDATE extra_growth_clock
           SET next_due_at=(last_cycle+1)*?
           WHERE next_due_at IS NULL""",
        (SECONDS,),
    )

    db.execute(
        """CREATE TABLE IF NOT EXISTS extra_growth_history(
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            cycle INTEGER NOT NULL,
            delta_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,character_id,cycle)
        )"""
    )


def bootstrap(db, gid, cid, now=None):
    initialize(db)

    now = int(
        time.time() if now is None else now
    )

    db.execute(
        """INSERT OR IGNORE INTO extra_growth_clock
           (guild_id,character_id,last_cycle,next_due_at)
           VALUES(?,?,0,?)""",
        (
            str(gid),
            int(cid),
            now + SECONDS,
        ),
    )


def _increment(db, gid, cid, name, amount):
    if not amount:
        return 0

    row = db.execute(
        """SELECT value FROM attributes
           WHERE guild_id=? AND character_id=?
           AND name=?""",
        (gid, cid, name),
    ).fetchone()

    if row is None:
        return 0

    old = int(row["value"])
    new = max(0, old + int(amount))

    db.execute(
        """UPDATE attributes SET value=?
           WHERE guild_id=? AND character_id=?
           AND name=?""",
        (new, gid, cid, name),
    )

    return new - old


def run_guild(gid, now=None):
    gid = str(gid)
    now = int(
        time.time() if now is None else now
    )

    processed = 0

    with transaction() as db:
        initialize(db)

        rows = db.execute(
            """SELECT id,settings_json FROM characters
               WHERE guild_id=? AND status='active'
               AND json_extract(
                   settings_json,'$.character_category'
               )='extra'""",
            (gid,),
        ).fetchall()

        schema = default_schema()

        general = [
            k for k in schema["general"]
            if k not in (
                "影響指數",
                "流量熱度",
                "商業價值",
            )
        ]

        talent = list(schema["talent"])

        for char in rows:
            cid = int(char["id"])

            clock = db.execute(
                """SELECT last_cycle,next_due_at
                   FROM extra_growth_clock
                   WHERE guild_id=? AND character_id=?""",
                (gid, cid),
            ).fetchone()

            if clock is None:
                bootstrap(db, gid, cid, now)
                continue

            due = int(clock["next_due_at"])

            if now < due:
                continue

            elapsed = (
                (now - due) // SECONDS
            ) + 1

            count = min(
                CATCHUP_LIMIT,
                elapsed,
            )

            # 略過超過補算上限的較早週期。
            skip = elapsed - count

            current_cycle = (
                int(clock["last_cycle"]) + skip
            )

            for offset in range(count):
                cycle = current_cycle + offset + 1

                exists = db.execute(
                    """SELECT 1
                       FROM extra_growth_history
                       WHERE guild_id=?
                       AND character_id=?
                       AND cycle=?""",
                    (gid, cid, cycle),
                ).fetchone()

                if exists:
                    continue

                delta = {}

                if random.random() < ABILITY_GROWTH_CHANCE:
                    for names, maximum in (
                        (general, 5),
                        (talent, 10),
                    ):
                        if not names:
                            continue

                        amount = random.randint(
                            0, maximum
                        )

                        for _ in range(amount):
                            name = random.choice(names)
                            delta[name] = (
                                delta.get(name, 0) + 1
                            )

                # 熱度及商業價值可以上升或下降。
                for name, low, high in (
                    ("流量熱度", -.05, .08),
                    ("商業價值", -.01, .02),
                ):
                    row = db.execute(
                        """SELECT value
                           FROM attributes
                           WHERE guild_id=?
                           AND character_id=?
                           AND name=?""",
                        (gid, cid, name),
                    ).fetchone()

                    if row:
                        delta[name] = round(
                            int(row["value"])
                            * random.uniform(low, high)
                        )

                applied = {}

                for name, value in delta.items():
                    actual = _increment(
                        db, gid, cid, name, value
                    )

                    if actual:
                        applied[name] = actual

                tracking = db.execute(
                    """SELECT loyal_fans,public_awareness
                       FROM character_system_tracking
                       WHERE guild_id=?
                       AND character_id=?""",
                    (gid, cid),
                ).fetchone()

                if tracking:
                    fans = round(
                        int(tracking["loyal_fans"])
                        * random.uniform(0, .02)
                    )

                    awareness = round(
                        int(tracking["public_awareness"])
                        * random.uniform(0, .01)
                    )

                    db.execute(
                        """UPDATE character_system_tracking
                           SET loyal_fans=loyal_fans+?,
                               public_awareness=public_awareness+?,
                               updated_at=CURRENT_TIMESTAMP
                           WHERE guild_id=?
                           AND character_id=?""",
                        (
                            fans,
                            awareness,
                            gid,
                            cid,
                        ),
                    )

                    applied["粉絲增長"] = fans
                    applied["知名度增長"] = awareness

                db.execute(
                    """INSERT INTO extra_growth_history
                       (guild_id,character_id,cycle,delta_json)
                       VALUES(?,?,?,?)""",
                    (
                        gid,
                        cid,
                        cycle,
                        json.dumps(
                            applied,
                            ensure_ascii=False,
                        ),
                    ),
                )

                processed += 1

            db.execute(
                """UPDATE extra_growth_clock
                   SET last_cycle=?,next_due_at=?
                   WHERE guild_id=? AND character_id=?""",
                (
                    int(clock["last_cycle"]) + elapsed,
                    due + elapsed * SECONDS,
                    gid,
                    cid,
                ),
            )

    return {
        "characters": len(rows),
        "cycles_applied": processed,
    }
