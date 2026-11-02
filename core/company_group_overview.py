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


def delegated_group_overview(guild_id, actor_id, group_id):
    """限制於董事長或已明確授權的集團唯讀資料。"""
    from core.company_group_delegation import has_capability
    gid=str(guild_id);group=str(group_id)
    with get_connection() as db:
        owner=db.execute("SELECT owner_id,name,founded_at FROM rp_business_entities WHERE guild_id=? AND business_id=? AND kind='group' AND status='active'",(gid,group)).fetchone()
        if not owner: raise ValueError('找不到有效集團')
        if str(owner['owner_id'])!=str(actor_id) and not has_capability(gid,group,actor_id,'overview'):
            raise PermissionError('沒有集團查閱權')
        members=[dict(r) for r in db.execute("SELECT business_id,name FROM rp_business_entities WHERE guild_id=? AND parent_id=? AND kind='company' AND status='active' ORDER BY name",(gid,group))]
        return dict(group_id=group,group_name=owner['name'],founded_at=owner['founded_at'],company_count=len(members),required=5,missing=0,members=tuple(members))


def delegated_company_summary(guild_id, actor_id, group_id):
    """提供集團授權者的旗下公司基本資料；不包含金庫、銀行、人事細節。"""
    from core.company_group_delegation import has_capability
    gid,group=str(guild_id),str(group_id)
    if not has_capability(gid,group,actor_id,'company_summary') and not has_capability(gid,group,actor_id,'member_report'):
        raise PermissionError('沒有旗下公司唯讀授權')
    with get_connection() as db:
        return [dict(r) for r in db.execute("""SELECT business_id,name,status FROM rp_business_entities
          WHERE guild_id=? AND parent_id=? AND kind='company' ORDER BY name LIMIT 50""",(gid,group))]


def delegated_member_report(guild_id, actor_id, group_id):
    """集團成員概況，僅顯示正式公司基本資訊；不洩漏員工或財務資料。"""
    from core.company_group_delegation import has_capability
    gid, group = str(guild_id), str(group_id)
    if not has_capability(gid, group, actor_id, 'member_report'):
        raise PermissionError('沒有集團成員報告查閱權')
    with get_connection() as db:
        entity = db.execute(
            "SELECT name FROM rp_business_entities WHERE guild_id=? AND business_id=? AND kind='group' AND status='active'",
            (gid, group),
        ).fetchone()
        if entity is None:
            raise ValueError('找不到有效集團')
        rows = db.execute(
            "SELECT business_id,name,status FROM rp_business_entities "
            "WHERE guild_id=? AND parent_id=? AND kind='company' "
            "ORDER BY name,business_id LIMIT 50",
            (gid, group),
        ).fetchall()
    companies = [dict(row) for row in rows]
    return {
        'group_id': group,
        'group_name': entity['name'],
        'company_count': len(companies),
        'companies': companies,
    }
