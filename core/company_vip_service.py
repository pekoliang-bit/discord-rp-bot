
"""品牌 VIP 會員管理。"""

from __future__ import annotations

from core.database import get_connection, transaction

VIP_LEVELS = {
    1: "普通VIP",
    2: "高級VIP",
    3: "尊榮VIP",
    4: "頂級VIP",
}


def initialize_vip(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS
        rp_company_brand_vip (
            guild_id TEXT NOT NULL,
            brand_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            vip_level INTEGER NOT NULL
                CHECK(vip_level BETWEEN 1 AND 4),
            granted_by TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(
                guild_id,brand_id,character_id
            )
        )
    """)


def set_brand_vip(
    guild_id,
    brand_id,
    character_id,
    level,
    actor_id,
    note="",
):
    gid = str(guild_id)
    bid = str(brand_id)
    uid = str(actor_id)
    cid = int(character_id)
    level = int(level)

    if level not in VIP_LEVELS:
        raise ValueError("VIP等級須介於1至4")

    with transaction() as db:
        initialize_vip(db)

        brand = db.execute(
            """
            SELECT owner_id, kind, status
            FROM rp_business_entities
            WHERE guild_id=? AND business_id=?
            """,
            (gid, bid),
        ).fetchone()

        if (
            brand is None
            or brand["kind"] != "brand"
            or brand["status"] != "active"
        ):
            raise ValueError("品牌不存在或未啟用")

        if str(brand["owner_id"]) != uid:
            raise PermissionError(
                "只有品牌持有人能管理VIP"
            )

        character = db.execute(
            """
            SELECT 1 FROM characters
            WHERE guild_id=? AND id=?
              AND status='active'
            """,
            (gid, cid),
        ).fetchone()

        if character is None:
            raise ValueError("找不到已啟用角色")

        db.execute(
            """
            INSERT INTO rp_company_brand_vip
            (
                guild_id,brand_id,character_id,
                vip_level,granted_by,note
            )
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(
                guild_id,brand_id,character_id
            )
            DO UPDATE SET
                vip_level=excluded.vip_level,
                granted_by=excluded.granted_by,
                note=excluded.note,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                gid, bid, cid,
                level, uid, str(note)[:500],
            ),
        )

    return f"✅ VIP已設定：{VIP_LEVELS[level]}。"


def remove_brand_vip(
    guild_id,
    brand_id,
    character_id,
    actor_id,
):
    gid = str(guild_id)
    bid = str(brand_id)
    uid = str(actor_id)

    with transaction() as db:
        initialize_vip(db)

        owner = db.execute(
            """
            SELECT owner_id FROM rp_business_entities
            WHERE guild_id=? AND business_id=?
              AND kind='brand' AND status='active'
            """,
            (gid, bid),
        ).fetchone()

        if owner is None or str(owner["owner_id"]) != uid:
            raise PermissionError(
                "沒有品牌VIP管理權限"
            )

        db.execute(
            """
            DELETE FROM rp_company_brand_vip
            WHERE guild_id=? AND brand_id=?
              AND character_id=?
            """,
            (gid, bid, int(character_id)),
        )

    return "✅ 已取消此品牌的VIP資格。"


def get_brand_vip(guild_id, brand_id, character_id):
    with get_connection() as db:
        initialize_vip(db)

        row = db.execute(
            """
            SELECT vip_level, note, updated_at
            FROM rp_company_brand_vip
            WHERE guild_id=? AND brand_id=?
              AND character_id=?
            """,
            (
                str(guild_id),
                str(brand_id),
                int(character_id),
            ),
        ).fetchone()

        return dict(row) if row else None
