
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


def appoint_endorsement(
    guild_id,
    brand_id,
    character_id,
    tier,
    scope,
    actor_id,
):
    gid = str(guild_id)
    bid = str(brand_id)
    uid = str(actor_id)
    cid = int(character_id)
    tier = int(tier)
    scope = validate_endorsement(tier, scope)

    with transaction() as db:
        initialize_endorsements(db)

        brand = _require_brand_owner(
            db, gid, bid, uid
        )

        character = db.execute(
            """
            SELECT owner_id, status
            FROM characters
            WHERE guild_id=? AND id=?
            """,
            (gid, cid),
        ).fetchone()

        if character is None or character["status"] != "active":
            raise ValueError("代言角色尚未啟用")

        # 防止任意指定別人的角色。
        # 代言授權應由正式申請或當事人確認取得。
        if str(character["owner_id"]) != uid:
            raise PermissionError(
                "非本人角色須先取得正式代言授權；"
                "目前此入口不直接任命其他玩家角色。"
            )

        existing = active_endorsements(
            db, gid, character_id=cid
        )

        competing = any(
            row["brand_id"] != bid
            and row["industry_key"]
                == str(brand["industry"])
            for row in existing
        )

        risk = competitor_risk(
            [row["tier"] for row in existing],
            tier,
            competing_brand=competing,
        )

        try:
            cur = db.execute(
                """
                INSERT INTO rp_company_endorsements
                (
                    guild_id,brand_id,character_id,
                    tier,scope,industry_key,appointed_by
                )
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    gid, bid, cid, tier, scope,
                    str(brand["industry"]), uid,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "此代言範圍已有有效任命，"
                "不能重複占用。"
            ) from exc

        return {
            "id": int(cur.lastrowid),
            "title": displayed_endorsement(
                tier, scope
            ),
            "risk": risk,
        }


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
            """
            UPDATE rp_company_endorsements
            SET status='ended',
                ended_at=CURRENT_TIMESTAMP,
                end_reason=?
            WHERE guild_id=? AND id=?
              AND status='active'
            """,
            (
                str(reason)[:300],
                gid,
                int(endorsement_id),
            ),
        )

    return "✅ 代言任期已結束，歷史紀錄保留。"


def artist_endorsements(guild_id, character_id):
    from core.database import get_connection

    with get_connection() as db:
        initialize_endorsements(db)
        return active_endorsements(
            db, guild_id,
            character_id=character_id,
        )
