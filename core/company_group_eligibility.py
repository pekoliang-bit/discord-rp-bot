
"""集團五間公司資格核對。"""

from __future__ import annotations

from core.company_group_rules import (
    AUTO_GROUP_COMPANIES,
)


def owned_companies(db, guild_id, owner_id):
    rows = db.execute(
        """
        SELECT business_id, parent_id
        FROM rp_business_entities
        WHERE guild_id=?
          AND owner_id=?
          AND kind='company'
          AND status='active'
        ORDER BY business_id
        """,
        (str(guild_id), str(owner_id)),
    ).fetchall()

    return tuple(dict(row) for row in rows)


def owned_group(db, guild_id, owner_id):
    return db.execute(
        """
        SELECT business_id, name
        FROM rp_business_entities
        WHERE guild_id=?
          AND owner_id=?
          AND kind='group'
          AND status='active'
        ORDER BY founded_at
        LIMIT 1
        """,
        (str(guild_id), str(owner_id)),
    ).fetchone()


def group_eligibility(db, guild_id, owner_id):
    companies = owned_companies(
        db, guild_id, owner_id
    )

    group = owned_group(
        db, guild_id, owner_id
    )

    count = len(companies)

    return {
        "company_count": count,
        "required": AUTO_GROUP_COMPANIES,
        "missing": max(
            0, AUTO_GROUP_COMPANIES - count
        ),
        "automatic_eligible": (
            count >= AUTO_GROUP_COMPANIES
            and group is None
        ),
        "direct_eligible": group is None,
        "existing_group_id": (
            group["business_id"]
            if group else None
        ),
        "companies": companies,
    }


def validate_group_application(
    db,
    guild_id,
    owner_id,
    *,
    method="automatic",
):
    from core.company_group_rules import (
        check_group_creation,
    )

    result = group_eligibility(
        db, guild_id, owner_id
    )

    check_group_creation(
        method=method,
        company_count=result["company_count"],
        owns_group=bool(
            result["existing_group_id"]
        ),
    )

    return result
