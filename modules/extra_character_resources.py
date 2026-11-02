
"""路人角色初始資源與正式經濟帳本。

總粉絲和忠實粉絲分開：
- extra_character_fan_totals: 路人總粉絲
- character_system_tracking: 正式忠實粉絲、黑粉、知名度

不會把隨機總粉絲全部變成忠實粉絲。
不會重複發放初始金幣。
"""

from __future__ import annotations

from core.database import get_or_create_account


def ensure_tables(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS
        extra_character_fan_totals (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            total_fans INTEGER NOT NULL DEFAULT 0
                CHECK(total_fans >= 0),
            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, character_id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS
        character_system_tracking (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            loyal_fans INTEGER NOT NULL DEFAULT 0,
            active_antis INTEGER NOT NULL DEFAULT 0,
            public_awareness INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, character_id)
        )
    """)


def initialize_resources(db, gid, cid, profile):
    """在創角交易中寫入資源；多次呼叫不重複發放。"""
    ensure_tables(db)

    gid = str(gid)
    cid = int(cid)

    resources = dict(
        profile.get("initial_resources") or {}
    )

    total_fans = max(
        0, int(resources.get("fans", 0))
    )
    awareness = max(
        0, int(resources.get("awareness", 0))
    )
    coins = max(
        0, int(resources.get("coins", 0))
    )

    db.execute("""
        INSERT OR IGNORE INTO
        extra_character_fan_totals (
            guild_id, character_id, total_fans
        )
        VALUES (?, ?, ?)
    """, (gid, cid, total_fans))

    # 忠實粉絲由正式系統獲得。
    # 不把隨機生成的總粉絲視為忠實粉絲。
    db.execute("""
        INSERT OR IGNORE INTO
        character_system_tracking (
            guild_id, character_id,
            loyal_fans, active_antis,
            public_awareness
        )
        VALUES (?, ?, 0, 0, ?)
    """, (gid, cid, awareness))

    if coins > 0:
        wallet = get_or_create_account(
            db, gid, "character", str(cid), "wallet"
        )

        source = get_or_create_account(
            db, gid, "system",
            "extra_initial_grants", "treasury"
        )

        key = f"extra-initial:{cid}"

        cursor = db.execute("""
            INSERT OR IGNORE INTO
            ledger_transactions (
                guild_id, unique_key, description
            )
            VALUES (?, ?, ?)
        """, (
            gid, key, "路人角色初始資產"
        ))

        if cursor.rowcount == 1:
            txid = int(cursor.lastrowid)

            db.executemany("""
                INSERT INTO ledger_entries (
                    guild_id, transaction_id,
                    account_id, amount
                )
                VALUES (?, ?, ?, ?)
            """, [
                (gid, txid, source, -coins),
                (gid, txid, wallet, coins),
            ])

    return {
        "total_fans": total_fans,
        "loyal_fans": 0,
        "awareness": awareness,
        "coins": coins,
    }


def get_resources(db, gid, cid):
    """讀取路人的真實粉絲、認知度與錢包餘額。"""
    ensure_tables(db)

    gid = str(gid)
    cid = int(cid)

    total = db.execute("""
        SELECT total_fans
        FROM extra_character_fan_totals
        WHERE guild_id=? AND character_id=?
    """, (gid, cid)).fetchone()

    tracking = db.execute("""
        SELECT
            loyal_fans,
            active_antis,
            public_awareness
        FROM character_system_tracking
        WHERE guild_id=? AND character_id=?
    """, (gid, cid)).fetchone()

    balance = db.execute("""
        SELECT COALESCE(SUM(e.amount), 0) AS coins
        FROM accounts a
        LEFT JOIN ledger_entries e
          ON e.guild_id=a.guild_id
         AND e.account_id=a.id
        WHERE a.guild_id=?
          AND a.owner_type='character'
          AND a.owner_id=?
          AND a.account_type='wallet'
    """, (gid, str(cid))).fetchone()

    return {
        "total_fans": (
            int(total["total_fans"]) if total else 0
        ),
        "loyal_fans": (
            int(tracking["loyal_fans"])
            if tracking else 0
        ),
        "active_antis": (
            int(tracking["active_antis"])
            if tracking else 0
        ),
        "public_awareness": (
            int(tracking["public_awareness"])
            if tracking else 0
        ),
        "coins": (
            int(balance["coins"]) if balance else 0
        ),
    }


def grow_popularity(
    db, gid, cid, *, fan_rate, awareness_rate
):
    """只增加總粉絲與知名度，不虛增忠實粉絲。"""
    ensure_tables(db)

    gid = str(gid)
    cid = int(cid)

    row = db.execute("""
        SELECT total_fans
        FROM extra_character_fan_totals
        WHERE guild_id=? AND character_id=?
    """, (gid, cid)).fetchone()

    fans_added = 0

    if row:
        old = int(row["total_fans"])
        fans_added = max(
            0, round(old * max(0.0, fan_rate))
        )

        db.execute("""
            UPDATE extra_character_fan_totals
            SET total_fans=total_fans+?,
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND character_id=?
        """, (fans_added, gid, cid))

    row = db.execute("""
        SELECT public_awareness
        FROM character_system_tracking
        WHERE guild_id=? AND character_id=?
    """, (gid, cid)).fetchone()

    awareness_added = 0

    if row:
        old = int(row["public_awareness"])
        awareness_added = max(
            0, round(old * max(0.0, awareness_rate))
        )

        db.execute("""
            UPDATE character_system_tracking
            SET public_awareness=public_awareness+?,
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND character_id=?
        """, (awareness_added, gid, cid))

    return {
        "total_fans": fans_added,
        "public_awareness": awareness_added,
    }
