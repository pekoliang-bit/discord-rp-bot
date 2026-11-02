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

    # A character id can be re-used across guild migrations or earlier
    # business imports. Allocate an unused id within this guild atomically.
    base_id = f"AUTO-GRP-{owner_character_id}"
    group_id = base_id
    suffix = 2
    while db.execute(
        "SELECT 1 FROM rp_business_entities "
        "WHERE guild_id=? AND business_id=?",
        (gid, group_id),
    ).fetchone():
        group_id = f"{base_id}-{suffix}"
        suffix += 1
        if suffix > 10000:
            raise RuntimeError("無法分配不重複的集團編號")

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
