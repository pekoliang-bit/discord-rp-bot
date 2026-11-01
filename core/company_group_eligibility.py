
"""集團成立資格與旗下公司歸屬。

不自動成立集團。
僅供正式申請與審核流程使用。
"""

from __future__ import annotations


REQUIRED_COMPANIES = 5


def eligible_companies(db, guild_id, owner_id):
    rows = db.execute("""
        SELECT business_id
        FROM rp_business_entities
        WHERE guild_id=?
          AND owner_id=?
          AND kind='company'
          AND status='active'
        ORDER BY founded_at, business_id
    """, (str(guild_id), str(owner_id))).fetchall()

    return tuple(str(r["business_id"]) for r in rows)


def validate_group_application(db, guild_id, owner_id):
    companies = eligible_companies(
        db, guild_id, owner_id
    )

    if len(companies) < REQUIRED_COMPANIES:
        raise ValueError(
            f"成立集團需要至少5間正式公司，"
            f"目前只有{len(companies)}間。"
            "品牌及商店不計入。"
        )

    existing = db.execute("""
        SELECT business_id
        FROM rp_business_entities
        WHERE guild_id=?
          AND owner_id=?
          AND kind='group'
          AND status='active'
        LIMIT 1
    """, (str(guild_id), str(owner_id))).fetchone()

    if existing is not None:
        raise ValueError(
            "你已經擁有正式集團，"
            "不得再次成立第二個集團。"
        )

    return companies


def attach_owned_companies(
    db, guild_id, owner_id, group_id
):
    """將符合條件的自有公司歸屬至新集團。

    必須在建立集團的同一筆交易內執行。
    """

    group = db.execute("""
        SELECT business_id
        FROM rp_business_entities
        WHERE guild_id=?
          AND business_id=?
          AND owner_id=?
          AND kind='group'
          AND status='active'
    """, (
        str(guild_id),
        str(group_id),
        str(owner_id),
    )).fetchone()

    if group is None:
        raise ValueError("集團不存在或持有人不符。")

    companies = eligible_companies(
        db, guild_id, owner_id
    )

    if len(companies) < REQUIRED_COMPANIES:
        raise ValueError("正式公司數量不足5間。")

    for company_id in companies:
        db.execute("""
            UPDATE rp_business_entities
            SET parent_id=?
            WHERE guild_id=?
              AND business_id=?
              AND owner_id=?
              AND kind='company'
              AND status='active'
        """, (
            str(group_id),
            str(guild_id),
            company_id,
            str(owner_id),
        ))

    return len(companies)
