"""品牌代言資料庫與歷史紀錄。"""


def _upgrade_legacy_tier_constraint(db):
    """保留既有代言紀錄，將早期 T4–T10 CHECK 升級成 T1–T10。

    僅在舊表確實含範圍限制時執行；不刪角色、合約或歷史紀錄。
    """
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='rp_company_endorsements'").fetchone()
    if not row:
        return
    sql = str(row[0] or '')
    import re
    if not re.search(r'BETWEEN\s+4\s+AND\s+10', sql, re.IGNORECASE):
        return
    db.execute("ALTER TABLE rp_company_endorsements RENAME TO rp_company_endorsements_tier_old")
    db.execute("""CREATE TABLE rp_company_endorsements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
        brand_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        tier INTEGER NOT NULL CHECK(tier BETWEEN 1 AND 10),
        scope TEXT NOT NULL, industry_key TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','ended')),
        appointed_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ended_at TEXT, end_reason TEXT NOT NULL DEFAULT ''
    )""")
    db.execute("""INSERT INTO rp_company_endorsements
        (id,guild_id,brand_id,character_id,tier,scope,industry_key,status,
         appointed_by,created_at,ended_at,end_reason)
        SELECT id,guild_id,brand_id,character_id,tier,scope,industry_key,status,
               appointed_by,created_at,ended_at,end_reason
        FROM rp_company_endorsements_tier_old""")
    db.execute("DROP TABLE rp_company_endorsements_tier_old")


def initialize_endorsements(db):
    _upgrade_legacy_tier_constraint(db)
    db.execute("""
        CREATE TABLE IF NOT EXISTS
        rp_company_endorsements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            brand_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            tier INTEGER NOT NULL
                CHECK(tier BETWEEN 1 AND 10),
            scope TEXT NOT NULL,
            industry_key TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','ended')),
            appointed_by TEXT NOT NULL,
            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,
            ended_at TEXT,
            end_reason TEXT NOT NULL DEFAULT ''
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS
        ix_company_endorsement_artist
        ON rp_company_endorsements(
            guild_id, character_id, status
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS
        ix_company_endorsement_brand
        ON rp_company_endorsements(
            guild_id, brand_id, status, tier
        )
    """)

    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        ux_company_endorsement_scope
        ON rp_company_endorsements(
            guild_id,brand_id,tier,scope
        )
        WHERE status='active'
          AND tier BETWEEN 1 AND 9
    """)

    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        ux_company_endorsement_artist_scope
        ON rp_company_endorsements(
            guild_id,brand_id,character_id,tier,scope
        )
        WHERE status='active'
    """)


def active_endorsements(
    db,
    guild_id,
    *,
    brand_id=None,
    character_id=None,
):
    sql = """
        SELECT *
        FROM rp_company_endorsements
        WHERE guild_id=? AND status='active'
    """
    params = [str(guild_id)]

    if brand_id is not None:
        sql += " AND brand_id=?"
        params.append(str(brand_id))

    if character_id is not None:
        sql += " AND character_id=?"
        params.append(int(character_id))

    sql += " ORDER BY tier DESC, id DESC"

    return [
        dict(row)
        for row in db.execute(
            sql, params
        ).fetchall()
    ]
