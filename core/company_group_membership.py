
"""集團旗下公司歸屬管理。

僅提供同一 SQLite 交易內使用的函式。
"""

from __future__ import annotations


def get_group(db, guild_id, group_id):
    row = db.execute(
        """
        SELECT *
        FROM rp_business_entities
        WHERE guild_id=?
          AND business_id=?
          AND kind='group'
          AND status='active'
        """,
        (str(guild_id), str(group_id)),
    ).fetchone()

    if row is None:
        raise LookupError("找不到正式集團")

    return row


def attach_company(
    db,
    guild_id,
    group_id,
    company_id,
    actor_id,
):
    gid = str(guild_id)
    group = get_group(db, gid, group_id)

    if str(group["owner_id"]) != str(actor_id):
        raise PermissionError(
            "只有集團董事長能管理旗下公司"
        )

    company = db.execute(
        """
        SELECT *
        FROM rp_business_entities
        WHERE guild_id=?
          AND business_id=?
          AND kind='company'
          AND status='active'
        """,
        (gid, str(company_id)),
    ).fetchone()

    if company is None:
        raise LookupError("找不到正式公司")

    if str(company["owner_id"]) != str(actor_id):
        raise PermissionError(
            "目前只允許董事長將"
            "自己持有的公司納入集團。"
        )

    parent = company["parent_id"]

    if parent and str(parent) != str(group_id):
        raise ValueError(
            "公司已歸屬其他商業主體，"
            "請先完成正式解除程序。"
        )

    if parent and str(parent) == str(group_id):
        return False

    result = db.execute(
        """
        UPDATE rp_business_entities
        SET parent_id=?
        WHERE guild_id=?
          AND business_id=?
          AND kind='company'
          AND status='active'
          AND parent_id IS NULL
        """,
        (
            str(group_id),
            gid,
            str(company_id),
        ),
    )

    if result.rowcount != 1:
        raise RuntimeError(
            "公司歸屬已變更，請重新查詢"
        )

    return True


def attach_all_owned(
    db,
    guild_id,
    group_id,
    actor_id,
):
    rows = db.execute(
        """
        SELECT business_id
        FROM rp_business_entities
        WHERE guild_id=?
          AND owner_id=?
          AND kind='company'
          AND status='active'
          AND parent_id IS NULL
        ORDER BY business_id
        """,
        (str(guild_id), str(actor_id)),
    ).fetchall()

    attached = 0

    for row in rows:
        if attach_company(
            db,
            guild_id,
            group_id,
            row["business_id"],
            actor_id,
        ):
            attached += 1

    return attached
