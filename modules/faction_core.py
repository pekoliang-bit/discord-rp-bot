
"""Discord RP 陣營系統核心。

路徑：modules/faction_core.py

功能：
- 五個預設陣營，只初始化一次
- 保護管理員修改後的陣營名稱
- 每個角色獨立選擇陣營
- 陣營共用經費池
- 個人貢獻積分
- 違約金入池但不計分
- 每 30 天結算內部排行榜
- 保存歷任第一名及第二名

所有玩家操作由 /陣營 提供。
本模組不註冊 Discord 指令。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.database import get_connection, transaction


DEFAULT_FACTIONS = (
    "單打獨鬥",
    "熟人抱團",
    "另闢蹊徑",
    "經紀公司所屬",
    "MCN機構",
)

PERSONAL_SOURCES = {
    "offering",
    "commission",
    "personal_award",
    "sponsor_personal",
}

POOL_SOURCES = PERSONAL_SOURCES | {
    "departure_penalty",
    "faction_rank",
    "faction_battle",
    "sponsor_group",
    "group_award",
    "adjustment",
}


def _now():
    return datetime.now(timezone.utc)


def initialize():
    """建立必要資料表，不清除既有資料。"""

    with transaction() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS faction_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                pool INTEGER NOT NULL DEFAULT 0
                    CHECK(pool >= 0),
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, name),
                UNIQUE(guild_id, id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS faction_members (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                faction_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id, character_id),
                FOREIGN KEY(guild_id, faction_id)
                    REFERENCES faction_groups(guild_id, id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS faction_periods (
                guild_id TEXT PRIMARY KEY,
                starts_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                sequence INTEGER NOT NULL DEFAULT 1
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS faction_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                faction_id INTEGER NOT NULL,
                character_id INTEGER,
                source TEXT NOT NULL,
                amount INTEGER NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                period INTEGER NOT NULL,
                memo TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(guild_id, faction_id)
                    REFERENCES faction_groups(guild_id, id)
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS faction_ledger_rank
            ON faction_ledger(
                guild_id,
                faction_id,
                period,
                character_id
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS faction_leaders (
                guild_id TEXT NOT NULL,
                faction_id INTEGER NOT NULL,
                period INTEGER NOT NULL,
                leader_id INTEGER,
                deputy_id INTEGER,
                PRIMARY KEY(guild_id, faction_id, period)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS faction_settings (
                guild_id TEXT PRIMARY KEY,
                defaults_initialized INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)


def ensure_guild(guild_id):
    """初始化陣營，避免改名後重新建立預設陣營。"""

    initialize()

    gid = str(guild_id)
    now = _now()

    with transaction() as db:

        setting = db.execute(
            """
            SELECT defaults_initialized
            FROM faction_settings
            WHERE guild_id = ?
            """,
            (gid,),
        ).fetchone()

        if (
            setting is None
            or int(setting["defaults_initialized"]) == 0
        ):

            existing = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM faction_groups
                WHERE guild_id = ?
                """,
                (gid,),
            ).fetchone()

            if int(existing["total"]) == 0:

                for name in DEFAULT_FACTIONS:
                    db.execute(
                        """
                        INSERT INTO faction_groups (
                            guild_id,
                            name
                        )
                        VALUES (?, ?)
                        """,
                        (gid, name),
                    )

            db.execute(
                """
                INSERT INTO faction_settings (
                    guild_id,
                    defaults_initialized
                )
                VALUES (?, 1)
                ON CONFLICT(guild_id)
                DO UPDATE SET
                    defaults_initialized = 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (gid,),
            )

        db.execute(
            """
            INSERT OR IGNORE INTO faction_periods (
                guild_id,
                starts_at,
                ends_at
            )
            VALUES (?, ?, ?)
            """,
            (
                gid,
                now.isoformat(),
                (
                    now + timedelta(days=30)
                ).isoformat(),
            ),
        )

    return gid


def _roll(db, gid):
    """結算已結束的期別並保存前兩名。"""

    row = db.execute(
        """
        SELECT *
        FROM faction_periods
        WHERE guild_id = ?
        """,
        (gid,),
    ).fetchone()

    if row is None:
        raise RuntimeError("陣營期別尚未初始化")

    now = _now()
    end = datetime.fromisoformat(row["ends_at"])
    seq = int(row["sequence"])
    original_seq = seq

    factions = db.execute(
        """
        SELECT id
        FROM faction_groups
        WHERE guild_id = ?
        """,
        (gid,),
    ).fetchall()

    while now >= end:

        for faction in factions:

            rank = db.execute(
                """
                SELECT
                    character_id,
                    SUM(points) AS score
                FROM faction_ledger
                WHERE guild_id = ?
                  AND faction_id = ?
                  AND period = ?
                  AND character_id IS NOT NULL
                GROUP BY character_id
                HAVING score > 0
                ORDER BY
                    score DESC,
                    character_id ASC
                LIMIT 2
                """,
                (
                    gid,
                    faction["id"],
                    seq,
                ),
            ).fetchall()

            leader_id = (
                rank[0]["character_id"]
                if len(rank) >= 1
                else None
            )

            deputy_id = (
                rank[1]["character_id"]
                if len(rank) >= 2
                else None
            )

            db.execute(
                """
                INSERT OR IGNORE INTO faction_leaders (
                    guild_id,
                    faction_id,
                    period,
                    leader_id,
                    deputy_id
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    gid,
                    faction["id"],
                    seq,
                    leader_id,
                    deputy_id,
                ),
            )

        end += timedelta(days=30)
        seq += 1

    if seq != original_seq:

        start = end - timedelta(days=30)

        db.execute(
            """
            UPDATE faction_periods
            SET starts_at = ?,
                ends_at = ?,
                sequence = ?
            WHERE guild_id = ?
            """,
            (
                start.isoformat(),
                end.isoformat(),
                seq,
                gid,
            ),
        )

    return seq


def join(
    guild_id,
    character_id,
    faction_id,
    owner_id,
):
    """替自己的已啟用原創角色選擇初始陣營。"""

    gid = ensure_guild(guild_id)
    cid = int(character_id)
    fid = int(faction_id)

    with transaction() as db:

        char = db.execute(
            """
            SELECT owner_id, status, character_type
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (gid, cid),
        ).fetchone()

        if (
            char is None
            or str(char["owner_id"]) != str(owner_id)
            or char["status"] != "active"
            or char["character_type"] != "player"
        ):
            raise ValueError(
                "僅能替自己已啟用的原創角色選擇陣營"
            )

        existing = db.execute(
            """
            SELECT faction_id
            FROM faction_members
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (gid, cid),
        ).fetchone()

        if existing is not None:
            raise ValueError(
                "角色已有陣營，請使用正式退出或轉換流程"
            )

        faction = db.execute(
            """
            SELECT id
            FROM faction_groups
            WHERE guild_id = ?
              AND id = ?
            """,
            (gid, fid),
        ).fetchone()

        if faction is None:
            raise ValueError("指定的陣營不存在")

        db.execute(
            """
            INSERT INTO faction_members (
                guild_id,
                character_id,
                faction_id
            )
            VALUES (?, ?, ?)
            """,
            (gid, cid, fid),
        )


def credit(
    guild_id,
    faction_id,
    amount,
    source,
    character_id=None,
    memo="",
):
    """記錄已驗證的陣營收入。

    注意：
    不會自行扣款或創造金幣。
    呼叫者必須先完成付款或獎勵驗證。
    """

    gid = ensure_guild(guild_id)

    if source not in POOL_SOURCES:
        raise ValueError("不支援的經費來源")

    if type(amount) is not int or amount <= 0:
        raise ValueError("入池金額必須為正整數")

    fid = int(faction_id)

    with transaction() as db:

        period = _roll(db, gid)

        faction = db.execute(
            """
            SELECT id
            FROM faction_groups
            WHERE guild_id = ?
              AND id = ?
            """,
            (gid, fid),
        ).fetchone()

        if faction is None:
            raise ValueError("陣營不存在")

        points = 0

        cid = (
            int(character_id)
            if character_id is not None
            else None
        )

        if source in PERSONAL_SOURCES:

            if cid is None:
                raise ValueError(
                    "個人貢獻必須指定角色"
                )

            member = db.execute(
                """
                SELECT 1
                FROM faction_members
                WHERE guild_id = ?
                  AND faction_id = ?
                  AND character_id = ?
                """,
                (gid, fid, cid),
            ).fetchone()

            if member is None:
                raise ValueError(
                    "貢獻角色不屬於此陣營"
                )

            previous_row = db.execute(
                """
                SELECT
                    COALESCE(SUM(amount), 0) AS total
                FROM faction_ledger
                WHERE guild_id = ?
                  AND faction_id = ?
                  AND character_id = ?
                  AND period = ?
                  AND source IN (
                      'offering',
                      'commission',
                      'personal_award',
                      'sponsor_personal'
                  )
                """,
                (gid, fid, cid, period),
            ).fetchone()

            previous = int(previous_row["total"])

            points = (
                (previous + amount) // 100
                - previous // 100
            )

        updated = db.execute(
            """
            UPDATE faction_groups
            SET pool = pool + ?
            WHERE guild_id = ?
              AND id = ?
            """,
            (amount, gid, fid),
        )

        if updated.rowcount != 1:
            raise ValueError("經費入池失敗")

        cursor = db.execute(
            """
            INSERT INTO faction_ledger (
                guild_id,
                faction_id,
                character_id,
                source,
                amount,
                points,
                period,
                memo
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gid,
                fid,
                cid,
                source,
                amount,
                points,
                period,
                str(memo)[:500],
            ),
        )

        return {
            "ledger_id": cursor.lastrowid,
            "period": period,
            "points": points,
        }


def snapshot(guild_id, faction_id):
    """取得陣營資訊及當期內部排行榜。"""

    gid = ensure_guild(guild_id)
    fid = int(faction_id)

    with transaction() as db:

        period = _roll(db, gid)

        faction = db.execute(
            """
            SELECT id, name, pool
            FROM faction_groups
            WHERE guild_id = ?
              AND id = ?
            """,
            (gid, fid),
        ).fetchone()

        if faction is None:
            raise ValueError("陣營不存在")

        rows = db.execute(
            """
            SELECT
                l.character_id,
                c.name,
                SUM(l.points) AS points
            FROM faction_ledger AS l
            JOIN characters AS c
              ON c.guild_id = l.guild_id
             AND c.id = l.character_id
            WHERE l.guild_id = ?
              AND l.faction_id = ?
              AND l.period = ?
              AND l.points > 0
            GROUP BY l.character_id
            ORDER BY
                points DESC,
                l.character_id ASC
            LIMIT 20
            """,
            (gid, fid, period),
        ).fetchall()

        rank = [
            (
                row["character_id"],
                row["name"],
                row["points"],
            )
            for row in rows
        ]

        return {
            "name": faction["name"],
            "pool": faction["pool"],
            "period": period,
            "rank": rank,
        }


def list_factions(guild_id):
    """取得目前伺服器所有陣營。"""

    gid = ensure_guild(guild_id)

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT id, name, pool
            FROM faction_groups
            WHERE guild_id = ?
            ORDER BY id
            """,
            (gid,),
        ).fetchall()

        return [
            (
                row["id"],
                row["name"],
                row["pool"],
            )
            for row in rows
        ]
