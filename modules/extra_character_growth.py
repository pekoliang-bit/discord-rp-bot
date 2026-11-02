
"""路人每48小時成長結算。

每個角色使用獨立時鐘。
超過七期只補最近七期。
所有變動在同一筆資料庫交易中完成。
"""

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
from modules.extra_character_resources import (
    ensure_tables,
    grow_popularity,
)

SECONDS = GROWTH_HOURS * 3600


def initialize(db):
    ensure_tables(db)

    db.execute("""
        CREATE TABLE IF NOT EXISTS extra_growth_clock (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            last_cycle INTEGER NOT NULL DEFAULT 0,
            next_due_at INTEGER,
            PRIMARY KEY(guild_id, character_id)
        )
    """)

    columns = {
        row[1]
        for row in db.execute(
            "PRAGMA table_info(extra_growth_clock)"
        )
    }

    if "next_due_at" not in columns:
        db.execute("""
            ALTER TABLE extra_growth_clock
            ADD COLUMN next_due_at INTEGER
        """)

    # 舊資料只轉換一次，避免每次啟動重置時間。
    db.execute("""
        UPDATE extra_growth_clock
        SET next_due_at=(last_cycle+1)*?
        WHERE next_due_at IS NULL
    """, (SECONDS,))

    db.execute("""
        CREATE TABLE IF NOT EXISTS extra_growth_history (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            cycle INTEGER NOT NULL,
            delta_json TEXT NOT NULL,
            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(
                guild_id, character_id, cycle
            )
        )
    """)


def bootstrap(db, gid, cid, now=None):
    """建立路人時設定首次48小時結算時間。"""
    initialize(db)

    now = int(
        time.time() if now is None else now
    )

    db.execute("""
        INSERT OR IGNORE INTO extra_growth_clock (
            guild_id,
            character_id,
            last_cycle,
            next_due_at
        )
        VALUES (?, ?, 0, ?)
    """, (
        str(gid),
        int(cid),
        now + SECONDS,
    ))


def _apply_attribute(db, gid, cid, name, delta):
    """更新正式屬性並回傳實際變動量。"""
    if name == "影響指數":
        return 0

    row = db.execute("""
        SELECT value FROM attributes
        WHERE guild_id=? AND character_id=?
          AND name=?
    """, (
        gid, cid, name,
    )).fetchone()

    if row is None:
        return 0

    old = max(0, int(row["value"]))
    new = max(0, old + int(delta))

    if old == new:
        return 0

    db.execute("""
        UPDATE attributes
        SET value=?
        WHERE guild_id=? AND character_id=?
          AND name=?
    """, (
        new, gid, cid, name,
    ))

    return new - old


def _ability_growth(db, gid, cid):
    """75%機率成長；一次成長分別從兩池抽點。"""
    result = {}

    if random.random() >= ABILITY_GROWTH_CHANCE:
        return result

    schema = default_schema()

    general = [
        name for name in schema["general"]
        if name not in (
            "影響指數",
            "流量熱度",
            "商業價值",
        )
    ]

    talent = list(schema["talent"])

    for names, maximum in (
        (general, 5),
        (talent, 10),
    ):
        if not names:
            continue

        amount = random.randint(0, maximum)

        for _ in range(amount):
            name = random.choice(names)
            result[name] = result.get(name, 0) + 1

    applied = {}

    for name, amount in result.items():
        actual = _apply_attribute(
            db, gid, cid, name, amount
        )
        if actual:
            applied[name] = actual

    return applied


def _resource_growth(db, gid, cid):
    """人氣成長與熱度、商業價值波動。"""
    changes = {}

    resources = grow_popularity(
        db, gid, cid,
        fan_rate=random.uniform(0, 0.02),
        awareness_rate=random.uniform(0, 0.01),
    )

    changes.update({
        "總粉絲": resources["total_fans"],
        "大眾認知": resources["public_awareness"],
    })

    for name, low, high in (
        ("流量熱度", -0.05, 0.08),
        ("商業價值", -0.01, 0.02),
    ):
        row = db.execute("""
            SELECT value FROM attributes
            WHERE guild_id=? AND character_id=?
              AND name=?
        """, (
            gid, cid, name,
        )).fetchone()

        if row is None:
            continue

        old = max(0, int(row["value"]))
        delta = round(
            old * random.uniform(low, high)
        )

        actual = _apply_attribute(
            db, gid, cid, name, delta
        )

        changes[name] = actual

    return changes


def run_guild(gid, now=None):
    """結算整個伺服器已到期的路人角色。"""
    gid = str(gid)
    now = int(
        time.time() if now is None else now
    )

    result = {
        "characters": 0,
        "cycles_applied": 0,
        "cycles_skipped": 0,
    }

    with transaction() as db:
        initialize(db)

        characters = db.execute("""
            SELECT id
            FROM characters
            WHERE guild_id=?
              AND status='active'
              AND json_extract(
                  settings_json,
                  '$.character_category'
              )='extra'
        """, (gid,)).fetchall()

        result["characters"] = len(characters)

        for character in characters:
            cid = int(character["id"])

            clock = db.execute("""
                SELECT last_cycle, next_due_at
                FROM extra_growth_clock
                WHERE guild_id=? AND character_id=?
            """, (
                gid, cid,
            )).fetchone()

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
                CATCHUP_LIMIT, elapsed
            )
            skipped = elapsed - count

            first_cycle = (
                int(clock["last_cycle"])
                + skipped
                + 1
            )

            result["cycles_skipped"] += skipped

            for cycle in range(
                first_cycle,
                first_cycle + count,
            ):
                exists = db.execute("""
                    SELECT 1
                    FROM extra_growth_history
                    WHERE guild_id=?
                      AND character_id=?
                      AND cycle=?
                """, (
                    gid, cid, cycle,
                )).fetchone()

                if exists:
                    continue

                changes = _ability_growth(
                    db, gid, cid
                )

                changes.update(
                    _resource_growth(db, gid, cid)
                )

                db.execute("""
                    INSERT INTO extra_growth_history (
                        guild_id,
                        character_id,
                        cycle,
                        delta_json
                    )
                    VALUES (?, ?, ?, ?)
                """, (
                    gid,
                    cid,
                    cycle,
                    json.dumps(
                        changes,
                        ensure_ascii=False,
                    ),
                ))

                result["cycles_applied"] += 1

            db.execute("""
                UPDATE extra_growth_clock
                SET last_cycle=?,
                    next_due_at=?
                WHERE guild_id=?
                  AND character_id=?
            """, (
                int(clock["last_cycle"]) + elapsed,
                due + elapsed * SECONDS,
                gid,
                cid,
            ))

    return result
