"""品牌代言任命、解除及競品風險。"""

from __future__ import annotations

import sqlite3

from core.database import transaction
from core.company_endorsement_rules import (
    validate_endorsement,
    competitor_risk,
    displayed_endorsement,
)
from core.company_endorsement_store import (
    initialize_endorsements,
    active_endorsements,
)


def _require_brand_owner(db, gid, brand_id, uid):
    brand = db.execute(
        """
        SELECT owner_id, industry, status, kind
        FROM rp_business_entities
        WHERE guild_id=? AND business_id=?
        """,
        (gid, brand_id),
    ).fetchone()

    if (
        brand is None
        or brand["kind"] != "brand"
        or brand["status"] != "active"
    ):
        raise ValueError("找不到已啟用品牌")

    if str(brand["owner_id"]) != uid:
        raise PermissionError(
            "只有品牌持有人可以任命代言"
        )

    return brand


def appoint_endorsement(guild_id, brand_id, character_id, tier, scope, actor_id):
    """既有入口相容：跨玩家不直接生效，必須使用邀請流程。"""
    from core.company_endorsement_appointment import request
    return request(guild_id,brand_id,character_id,tier,scope,actor_id)


def end_endorsement(
    guild_id,
    endorsement_id,
    actor_id,
    reason="任期結束",
):
    gid = str(guild_id)
    uid = str(actor_id)

    with transaction() as db:
        initialize_endorsements(db)

        row = db.execute(
            """
            SELECT *
            FROM rp_company_endorsements
            WHERE guild_id=? AND id=?
              AND status='active'
            """,
            (gid, int(endorsement_id)),
        ).fetchone()

        if row is None:
            raise LookupError(
                "找不到有效的代言紀錄"
            )

        _require_brand_owner(
            db, gid, row["brand_id"], uid
        )

        db.execute(
            """UPDATE rp_company_endorsements SET status='ended',
              ended_at=CURRENT_TIMESTAMP,end_reason=?
              WHERE guild_id=? AND id=? AND status='active'""",
            (str(reason)[:300],gid,int(endorsement_id)),
        )
        from core.company_endorsement_lifecycle import _setup, _audit
        _setup(db)
        db.execute("""UPDATE rp_endorsement_terms SET status='terminated',updated_at=CURRENT_TIMESTAMP
              WHERE guild_id=? AND endorsement_id=? AND status IN ('active','proposed')""",
              (gid,int(endorsement_id)))
        _audit(db,gid,int(endorsement_id),'terminate',uid,str(reason)[:300])
        # 新舊入口共用頭銜 ID 時同步結束舊合約，避免兩邊狀態矛盾。
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_endorsement_contracts'").fetchone():
            cols={r['name'] for r in db.execute('PRAGMA table_info(rp_endorsement_contracts)')}
            if 'endorsement_id' in cols:
                db.execute("UPDATE rp_endorsement_contracts SET status='ended' WHERE guild_id=? AND endorsement_id=? AND status='active'",(gid,int(endorsement_id)))

    return "✅ 代言任期已結束，歷史紀錄保留。"


def artist_endorsements(guild_id, character_id):
    from core.database import get_connection

    with get_connection() as db:
        initialize_endorsements(db)
        return active_endorsements(
            db, guild_id,
            character_id=character_id,
        )
