
"""集團董事長及加入限制。"""

from __future__ import annotations

from core.company_group_eligibility import (
    owned_group,
)


def is_chairperson(db, guild_id, user_id):
    return owned_group(
        db,
        guild_id,
        user_id,
    ) is not None


def require_chairperson(
    db,
    guild_id,
    group_id,
    actor_id,
):
    row = db.execute(
        """
        SELECT owner_id
        FROM rp_business_entities
        WHERE guild_id=?
          AND business_id=?
          AND kind='group'
          AND status='active'
        """,
        (
            str(guild_id),
            str(group_id),
        ),
    ).fetchone()

    if row is None:
        raise LookupError("集團不存在")

    if str(row["owner_id"]) != str(actor_id):
        raise PermissionError(
            "只有現任董事長能操作"
        )

    return True


def verify_join_other_group(
    db,
    guild_id,
    actor_id,
    target_group_id,
):
    gid = str(guild_id)
    uid = str(actor_id)

    target = db.execute(
        """
        SELECT owner_id
        FROM rp_business_entities
        WHERE guild_id=?
          AND business_id=?
          AND kind='group'
          AND status='active'
        """,
        (gid, str(target_group_id)),
    ).fetchone()

    if target is None:
        raise LookupError("目標集團不存在")

    if is_chairperson(db, gid, uid):
        if str(target["owner_id"]) != uid:
            raise PermissionError(
                "董事長不可加入他人的集團"
            )

    return True


def verify_company_transfer(
    db,
    guild_id,
    company_id,
    target_group_id,
):
    company = db.execute(
        """
        SELECT parent_id
        FROM rp_business_entities
        WHERE guild_id=?
          AND business_id=?
          AND kind='company'
          AND status='active'
        """,
        (
            str(guild_id),
            str(company_id),
        ),
    ).fetchone()

    if company is None:
        raise LookupError("找不到正式公司")

    if (
        company["parent_id"]
        and str(company["parent_id"])
        != str(target_group_id)
    ):
        raise PermissionError(
            "公司已屬其他集團，"
            "不能直接轉移。"
        )

    return True
