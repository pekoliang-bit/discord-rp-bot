"""五間正式公司自動成立集團。

在公司審核核准的同一交易中呼叫。
"""

from __future__ import annotations

from core.company_group_eligibility import (
    group_eligibility,
)
from core.company_group_membership import (
    attach_all_owned,
)


def maybe_create_group(
    db,
    guild_id,
    owner_id,
    owner_character_id,
):
    gid = str(guild_id)
    uid = str(owner_id)

    eligibility = group_eligibility(
        db, gid, uid
    )

    if eligibility["existing_group_id"]:
        return None

    if not eligibility["automatic_eligible"]:
        return None

    # 集團共用本伺服器的公司／集團流水號，避免與手動申請重複。
    # 仍維持同一筆審核交易，失敗會連同新增集團一起回滾。
    from core.company_public_ids import ensure_companies, _next, prefix
    ensure_companies(db, gid)
    ordinal = _next(db, gid)
    group_id = f"{prefix(gid)}-{ordinal}"
    db.execute(
        "INSERT INTO rp_company_public_codes "
        "(guild_id,business_id,public_code,ordinal) VALUES(?,?,?,?)",
        (gid, group_id, group_id, ordinal),
    )

    db.execute(
        """
        INSERT INTO rp_business_entities (
            guild_id,
            business_id,
            kind,
            name,
            category,
            industry,
            description,
            region,
            owner_id,
            owner_character_id,
            parent_id,
            representative_character_id,
            brand_tier
        )
        VALUES (
            ?, ?, 'group', ?,
            '自動成立集團',
            '綜合',
            '持有五間正式公司成立',
            '待設定',
            ?, ?, NULL, NULL, NULL
        )
        """,
        (
            gid,
            group_id,
            f"集團-{owner_character_id}",
            uid,
            int(owner_character_id),
        ),
    )

    attached = attach_all_owned(
        db, gid, group_id, uid
    )

    db.execute(
        """
        INSERT INTO rp_business_audit (
            guild_id,
            business_id,
            action,
            actor_id,
            details
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            gid,
            group_id,
            "auto_group_created",
            uid,
            f"五公司達標；歸屬{attached}間",
        ),
    )

    return group_id
