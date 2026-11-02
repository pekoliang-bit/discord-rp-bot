
"""品牌代言曝光與熱度事件連動。

不修改角色屬性、不給予影響指數。
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.database import transaction


EXPOSURE_POINTS = {
    1: 5_000,
    2: 10_000,
    3: 50_000,
    4: 100_000,
}


def record_endorsement_exposure(
    guild_id,
    endorsement_id,
    exposure_level,
    event_key,
    actor_id,
):
    from modules.company_brand_progress import (
        initialize,
    )
    from core.company_endorsement_store import (
        initialize_endorsements,
    )

    initialize()

    gid = str(guild_id)
    uid = str(actor_id)
    level = int(exposure_level)
    key = str(event_key).strip()

    if level not in EXPOSURE_POINTS:
        raise ValueError("曝光級別只能是1至4")

    if not key or len(key) > 180:
        raise ValueError("曝光事件識別碼不正確")

    with transaction() as db:
        initialize_endorsements(db)

        endorsement = db.execute(
            """
            SELECT *
            FROM rp_company_endorsements
            WHERE guild_id=? AND id=?
              AND status='active'
            """,
            (gid, int(endorsement_id)),
        ).fetchone()

        if endorsement is None:
            raise ValueError("代言紀錄不存在或已結束")

        brand = db.execute(
            """
            SELECT owner_id, status
            FROM rp_business_entities
            WHERE guild_id=? AND business_id=?
              AND kind='brand'
            """,
            (
                gid,
                endorsement["brand_id"],
            ),
        ).fetchone()

        if (
            brand is None
            or brand["status"] != "active"
            or str(brand["owner_id"]) != uid
        ):
            raise PermissionError(
                "只有品牌持有人能登記代言曝光"
            )

        full_key = (
            f"endorsement:{endorsement_id}:{key}"
        )

        existed = db.execute(
            """
            SELECT 1 FROM rp_brand_heat_events
            WHERE guild_id=? AND event_key=?
            """,
            (gid, full_key),
        ).fetchone()

        if existed:
            return {
                "duplicate": True,
                "points": 0,
            }

        points = EXPOSURE_POINTS[level]

        db.execute(
            """
            INSERT INTO rp_brand_heat_events
            (
                guild_id,brand_id,event_key,
                source,points,created_at
            )
            VALUES (?,?,?,?,?,?)
            """,
            (
                gid,
                endorsement["brand_id"],
                full_key,
                "representative",
                points,
                datetime.now(
                    timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

        return {
            "duplicate": False,
            "points": points,
        }
