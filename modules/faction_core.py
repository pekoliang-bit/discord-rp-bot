
"""Discord RP 陣營系統核心。

檔案位置：modules/faction_core.py

功能：
- 五個預設陣營
- 每個角色獨立選擇陣營
- 陣營共用經費池
- 經費收入流水
- 每 100 金幣有效貢獻 = 1 積分
- 違約金入池但不計分
- 每 30 天結算內部排行榜
- 保存歷任第一名與第二名

本模組不註冊任何 Discord 指令。
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

# 可計入角色個人積分的資金來源
PERSONAL_SOURCES = {
    "offering",
    "commission",
    "personal_award",
    "sponsor_personal",
}

# 所有允許進入陣營經費池的資金來源
POOL_SOURCES = PERSONAL_SOURCES | {
    "departure_penalty",
    "faction_rank",
    "faction_battle",
    "sponsor_group",
    "group_award",
    "adjustment",
}


def initialize():
    """建立陣營資料表，不覆蓋既有資料。"""

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
                PRIMARY KEY(
                    guild_id,
                    faction_id,
                    period
                )
            )
        """)


def _now():
    """取得 UTC 時間。"""
    return datetime.now(timezone.utc)


def ensure_guild(guild_id):
    """確保伺服器擁有預設陣營及結算期。"""

    initialize()

    gid = str(guild_id)
    now = _now()

    with transaction() as db:

        for name in DEFAULT_FACTIONS:
            db.execute(
                """
                INSERT OR IGNORE INTO faction_groups
                    (guild_id, name)
                VALUES (?, ?)
                """,
                (gid, name),
            )

        db.execute(
            """
            INSERT OR IGNORE INTO faction_periods
                (guild_id, starts_at, ends_at)
            VALUES (?, ?, ?)
            """,
            (
                gid,
                now.isoformat(),
                (now + timedelta(days=30)).isoformat(),
            ),
        )

    return gid


def _roll(db, gid):
    """結算已結束的期別，保存前兩名。"""

    row = db.execute(
        """
        SELECT *
        FROM faction_periods
        WHERE guild_id = ?
        """,
        (gid,),
    ).fetchone()

    if not row:
        raise RuntimeError("陣營期別尚未初始化")

    now = _now()
    end = datetime.fromisoformat(row["ends_at"])
    seq = int(row["sequence"])

    while now >= end:

        factions = db.execute(
            """
            SELECT id
            FROM faction_groups
            WHERE guild_id = ?
            """,
            (gid,),
        ).fetchall()

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
                if rank else None
            )

            deputy_id = (
                rank[1]["character_id"]
                if len(rank) > 1 else None
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

    if seq != int(row["sequence"]):

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
    """替玩家自己的已啟用角色加入初始陣營。"""

    gid = ensure_guild(guild_id)

    with transaction() as db:

        char = db.execute(
            """
            SELECT
                owner_id,
                status,
                character_type
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                gid,
                int(character_id),
            ),
        ).fetchone()

        if (
            not char
            or str(char["owner_id"]) != str(owner_id)
            or char["status"] != "active"
            or char["character_type"] != "player"
        ):
            raise ValueError(
                "僅能替自己已啟用的原創角色選擇陣營"
            )

        exists = db.execute(
            """
            SELECT 1
            FROM faction_members
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                gid,
                int(character_id),
            ),
        ).fetchone()

        if exists:
            raise ValueError(
                "角色已有陣營；轉換陣營須使用正式退出／違約流程"
            )

        faction = db.execute(
            """
            SELECT 1
            FROM faction_groups
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                gid,
                int(faction_id),
            ),
        ).fetchone()

        if not faction:
            raise ValueError("陣營不存在")

        db.execute(
            """
            INSERT INTO faction_members (
                guild_id,
                character_id,
                faction_id
            )
            VALUES (?, ?, ?)
            """,
            (
                gid,
                int(character_id),
                int(faction_id),
            ),
        )


def credit(
    guild_id,
    faction_id,
    amount,
    source,
    character_id=None,
    memo="",
):
    """記錄已驗證的入池資金。

    注意：
    此函式不負責扣除玩家錢包。
    必須由已完成付款或獎勵驗證的模組呼叫。
    不可直接暴露給玩家操作。
    """

    gid = ensure_guild(guild_id)

    if source not in POOL_SOURCES:
        raise ValueError("不支援的經費來源")

    if type(amount) is not int or amount <= 0:
        raise ValueError(
            "入池金額必須為正整數"
        )

    with transaction() as db:

        seq = _roll(db, gid)

        faction = db.execute(
            """
            SELECT id
            FROM faction_groups
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                gid,
                int(faction_id),
            ),
        ).fetchone()

        if not faction:
            raise ValueError("陣營不存在")

        points = 0

        if source in PERSONAL_SOURCES:

            if character_id is None:
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
                (
                    gid,
                    int(faction_id),
                    int(character_id),
                ),
            ).fetchone()

            if not member:
                raise ValueError(
                    "貢獻角色不屬於此陣營"
                )

            # 同一期的小額貢獻可累計換算積分。
            old = db.execute(
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
                (
                    gid,
                    int(faction_id),
                    int(character_id),
                    seq,
                ),
            ).fetchone()

            previous = int(old["total"])

            points = (
                (previous + amount) // 100
                - previous // 100
            )

        db.execute(
            """
            UPDATE faction_groups
            SET pool = pool + ?
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                amount,
                gid,
                int(faction_id),
            ),
        )

        cur = db.execute(
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
                int(faction_id),
                (
                    int(character_id)
                    if character_id is not None
                    else None
                ),
                source,
                amount,
                points,
                seq,
                str(memo)[:500],
            ),
        )

        return {
            "ledger_id": cur.lastrowid,
            "period": seq,
            "points": points,
        }


def snapshot(guild_id, faction_id):
    """取得陣營經費及當期內部排行榜。"""

    gid = ensure_guild(guild_id)

    with transaction() as db:

        seq = _roll(db, gid)

        faction = db.execute(
            """
            SELECT *
            FROM faction_groups
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                gid,
                int(faction_id),
            ),
        ).fetchone()

        if not faction:
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
            (
                gid,
                int(faction_id),
                seq,
            ),
        ).fetchall()

        return {
            "name": faction["name"],
            "pool": faction["pool"],
            "period": seq,
            "rank": [
                (
                    row["character_id"],
                    row["name"],
                    row["points"],
                )
                for row in rows
            ],
        }


def list_factions(guild_id):
    """取得伺服器的陣營清單。"""

    gid = ensure_guild(guild_id)

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT
                id,
                name,
                pool
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
