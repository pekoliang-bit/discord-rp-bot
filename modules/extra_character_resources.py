
"""路人角色初始資源。

沿用：
- character_system_tracking：忠實粉絲、黑粉、知名度
- accounts：正式帳戶
- ledger_transactions / ledger_entries：正式金幣帳本

本模組不產生額外 Slash 指令。
"""

from __future__ import annotations

from core.database import get_or_create_account


def _ensure_tracking_table(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS character_system_tracking (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            loyal_fans INTEGER NOT NULL DEFAULT 0
                CHECK(loyal_fans >= 0),
            active_antis INTEGER NOT NULL DEFAULT 0
                CHECK(active_antis >= 0),
            public_awareness INTEGER NOT NULL DEFAULT 0
                CHECK(public_awareness >= 0),
            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, character_id)
        )
        """
    )


def initialize_resources(db, gid, cid, profile):
    """在角色建立的同一筆資料庫交易中初始化資源。

    重複呼叫不會重複增加粉絲、認知度或金幣。
    """
    gid = str(gid)
    cid = int(cid)

    resources = dict(profile.get("initial_resources") or {})

    fans = max(0, int(resources.get("fans", 0)))
    awareness = max(0, int(resources.get("awareness", 0)))
    coins = max(0, int(resources.get("coins", 0)))

    _ensure_tracking_table(db)

    # INITIAL 資源只在首次建立追蹤資料時寫入。
    db.execute(
        """
        INSERT OR IGNORE INTO character_system_tracking (
            guild_id,
            character_id,
            loyal_fans,
            active_antis,
            public_awareness
        )
        VALUES (?, ?, ?, 0, ?)
        """,
        (gid, cid, fans, awareness),
    )

    if coins == 0:
        return {
            "fans": fans,
            "awareness": awareness,
            "coins": 0,
        }

    wallet_id = get_or_create_account(
        db,
        gid,
        "character",
        str(cid),
        "wallet",
    )

    source_id = get_or_create_account(
        db,
        gid,
        "system",
        "extra_initial_grants",
        "treasury",
    )

    unique_key = f"extra-initial:{cid}"

    cursor = db.execute(
        """
        INSERT OR IGNORE INTO ledger_transactions (
            guild_id,
            unique_key,
            description
        )
        VALUES (?, ?, ?)
        """,
        (
            gid,
            unique_key,
            "路人角色初始資產",
        ),
    )

    if cursor.rowcount == 1:
        transaction_id = int(cursor.lastrowid)

        db.executemany(
            """
            INSERT INTO ledger_entries (
                guild_id,
                transaction_id,
                account_id,
                amount
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (gid, transaction_id, source_id, -coins),
                (gid, transaction_id, wallet_id, coins),
            ],
        )

    return {
        "fans": fans,
        "awareness": awareness,
        "coins": coins,
    }


def get_resources(db, gid, cid):
    """讀取路人的正式追蹤數值和錢包餘額。"""
    gid = str(gid)
    cid = int(cid)

    _ensure_tracking_table(db)

    tracking = db.execute(
        """
        SELECT
            loyal_fans,
            active_antis,
            public_awareness
        FROM character_system_tracking
        WHERE guild_id = ?
          AND character_id = ?
        """,
        (gid, cid),
    ).fetchone()

    wallet = db.execute(
        """
        SELECT COALESCE(SUM(e.amount), 0) AS balance
        FROM accounts a
        LEFT JOIN ledger_entries e
          ON e.guild_id = a.guild_id
         AND e.account_id = a.id
        WHERE a.guild_id = ?
          AND a.owner_type = 'character'
          AND a.owner_id = ?
          AND a.account_type = 'wallet'
        """,
        (gid, str(cid)),
    ).fetchone()

    return {
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
            int(wallet["balance"])
            if wallet else 0
        ),
    }
