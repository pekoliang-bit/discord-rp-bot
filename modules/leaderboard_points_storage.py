
"""排行榜可分配點數儲存層。"""

from core.database import get_connection, transaction


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS lb_point_balances (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            pool TEXT NOT NULL,
            balance INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, character_id, pool),
            CHECK (pool IN ('free', 'skill')),
            CHECK (balance >= 0)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS lb_point_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            pool TEXT NOT NULL,
            amount INTEGER NOT NULL,
            category TEXT NOT NULL,
            target TEXT NOT NULL,
            source_key TEXT NOT NULL,
            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (guild_id, source_key),
            CHECK (amount > 0)
        )
    """)


def get_balance(guild_id, character_id):
    with get_connection() as db:
        initialize(db)

        rows = db.execute("""
            SELECT pool, balance
            FROM lb_point_balances
            WHERE guild_id = ?
              AND character_id = ?
        """, (
            str(guild_id),
            int(character_id),
        )).fetchall()

    result = {"free": 0, "skill": 0}

    for row in rows:
        result[row["pool"]] = int(row["balance"])

    return result


def grant_points(
    guild_id,
    character_id,
    pool,
    amount,
    source_key,
):
    """依唯一來源發點，重試不會重複增加。"""
    if pool not in ("free", "skill"):
        raise ValueError("未知點數池")

    amount = int(amount)

    if amount <= 0:
        raise ValueError("點數必須大於零")

    if not str(source_key).strip():
        raise ValueError("必須提供唯一來源")

    with transaction() as db:
        initialize(db)

        character = db.execute("""
            SELECT 1 FROM characters
            WHERE guild_id = ? AND id = ?
              AND status IN ('approved', 'active')
        """, (
            str(guild_id),
            int(character_id),
        )).fetchone()

        if character is None:
            raise ValueError("角色不存在或尚未啟用")

        cursor = db.execute("""
            INSERT OR IGNORE INTO lb_point_history (
                guild_id, character_id, pool,
                amount, category, target, source_key
            )
            VALUES (?, ?, ?, ?, 'grant', '', ?)
        """, (
            str(guild_id),
            int(character_id),
            pool,
            amount,
            str(source_key),
        ))

        if cursor.rowcount == 0:
            return False

        db.execute("""
            INSERT INTO lb_point_balances (
                guild_id, character_id, pool, balance
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, character_id, pool)
            DO UPDATE SET
                balance = balance + excluded.balance
        """, (
            str(guild_id),
            int(character_id),
            pool,
            amount,
        ))

    return True


def get_history(guild_id, character_id, limit=30):
    limit = max(1, min(int(limit), 100))

    with get_connection() as db:
        initialize(db)

        rows = db.execute("""
            SELECT *
            FROM lb_point_history
            WHERE guild_id = ?
              AND character_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (
            str(guild_id),
            int(character_id),
            limit,
        )).fetchall()

    return [dict(row) for row in rows]
