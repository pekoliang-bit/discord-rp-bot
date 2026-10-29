
"""陣營資料庫安全補強。

檔案：modules/faction_safety.py

不建立 Discord 指令。
不刪除或重置既有玩家資料。
"""

from __future__ import annotations

from core.database import transaction


def initialize_safety():
    """建立交易防重複及陣營設定資料表。"""

    with transaction() as db:

        # 每個伺服器記錄陣營初始化狀態。
        # 避免管理員改名後又新增預設陣營。
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            faction_settings (
                guild_id TEXT PRIMARY KEY,
                defaults_initialized INTEGER
                    NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 記錄陣營交易與既有經濟流水的關係。
        # 同一伺服器的交易編號只能登記一次。
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            faction_payment_links (
                guild_id TEXT NOT NULL,
                request_key TEXT NOT NULL,
                faction_ledger_id INTEGER NOT NULL,
                economy_transaction_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    guild_id,
                    request_key
                ),
                UNIQUE (
                    guild_id,
                    economy_transaction_id
                ),
                UNIQUE (
                    guild_id,
                    faction_ledger_id
                )
            )
        """)

        # 記錄每期結算狀態。
        # 防止公告、獎勵或權限重複發放。
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            faction_settlement_events (
                guild_id TEXT NOT NULL,
                faction_id INTEGER NOT NULL,
                period INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                processed_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    guild_id,
                    faction_id,
                    period,
                    event_type
                )
            )
        """)


def register_payment(
    db,
    guild_id,
    request_key,
    faction_ledger_id,
    economy_transaction_id,
):
    """在同一筆財務交易內登記關聯。"""

    db.execute(
        """
        INSERT INTO faction_payment_links (
            guild_id,
            request_key,
            faction_ledger_id,
            economy_transaction_id
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            str(guild_id),
            str(request_key),
            int(faction_ledger_id),
            int(economy_transaction_id),
        ),
    )


def settlement_completed(
    db,
    guild_id,
    faction_id,
    period,
    event_type,
):
    """嘗試登記結算事件。

    True：首次登記，可繼續處理。
    False：已處理過，不應重複執行。

    必須與實際結算操作放在同一交易中。
    """

    cursor = db.execute(
        """
        INSERT OR IGNORE INTO
        faction_settlement_events (
            guild_id,
            faction_id,
            period,
            event_type
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            str(guild_id),
            int(faction_id),
            int(period),
            str(event_type),
        ),
    )

    return cursor.rowcount == 1
