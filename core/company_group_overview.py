
"""集團資訊唯讀查詢。"""

from __future__ import annotations

from core.database import get_connection
from core.company_group_eligibility import (
    group_eligibility,
)


def group_overview(guild_id, actor_id):
    gid = str(guild_id)
    uid = str(actor_id)

    with get_connection() as db:
        eligibility = group_eligibility(
            db, gid, uid
        )

        group_id = eligibility[
            "existing_group_id"
        ]

        if group_id is None:
            return {
                "group_id": None,
                "company_count": eligibility[
                    "company_count"
                ],
                "required": 5,
                "missing": eligibility["missing"],
                "members": (),
            }

        group = db.execute(
            """
            SELECT name, founded_at
            FROM rp_business_entities
            WHERE guild_id=?
              AND business_id=?
              AND owner_id=?
              AND kind='group'
            """,
            (gid, group_id, uid),
        ).fetchone()

        rows = db.execute(
            """
            SELECT business_id, name
            FROM rp_business_entities
            WHERE guild_id=?
              AND parent_id=?
              AND kind='company'
              AND status='active'
            ORDER BY name
            """,
            (gid, group_id),
        ).fetchall()

        return {
            "group_id": group_id,
            "group_name": group["name"],
            "founded_at": group["founded_at"],
            "company_count": eligibility[
                "company_count"
            ],
            "required": 5,
            "missing": 0,
            "members": tuple(
                dict(row) for row in rows
            ),
        }


def format_group_overview(data):
    if not data["group_id"]:
        return (
            "🏢 **我的集團資格**\n"
            "目前尚未成立集團。\n"
            f"正式公司：{data['company_count']}間\n"
            f"距離自動成立："
            f"還差{data['missing']}間\n"
            "也可以透過正式申請，"
            "支付1,000萬金幣直接成立。"
        )

    lines = [
        f"🏢 **{data['group_name']}**",
        f"集團編號：{data['group_id']}",
        f"成立時間：{data['founded_at']}",
        f"持有人名下正式公司："
        f"{data['company_count']}間",
        "",
        "**集團旗下公司**",
    ]

    members = data["members"]

    if not members:
        lines.append("尚無歸屬此集團的公司。")
    else:
        for row in members:
            lines.append(
                f"・{row['name']} "
                f"（{row['business_id']}）"
            )

    return "\n".join(lines)
