"""公關評級與正式獎懲方案的核對入口。

安裝 core/rumor_grade_plan_bridge.py；非 Discord Cog。
只用已鎖定的評級、T 級、熱度與案件規則快照建立獎懲方案。
不支付金幣、不改角色數值、不結案；後續交給既有工作單協調器。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.database import get_connection
from core.rumor_grade_service import load_grade
from core.rumor_plan_store import load_case_rules, load_reward_plan
from core.rumor_plan_approval import approve_final_plan


@dataclass(frozen=True)
class BridgedPlan:
    guild_id: str
    rumor_id: int
    character_id: int
    grade: str
    tier: int
    heat: int
    fingerprint: str
    changes: dict[str, int]


def approve_from_locked_grade(
    guild_id: str | int,
    rumor_id: int,
    *,
    trusted: bool = False,
) -> BridgedPlan:
    """核對已存在的評級與規則快照，產生不可變獎懲方案。

    注意：評級快照本身必須由可信任的評級器建立，不能直接
    讓玩家傳入 score、熱度或門檻。
    """
    if not trusted:
        raise PermissionError("只有可信任結算器能核定方案")
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("無效的案件識別碼")
    with get_connection() as db:
        case = db.execute(
            "SELECT character_id,status FROM rp_company_rumors "
            "WHERE guild_id=? AND id=?", (gid, rumor_id)
        ).fetchone()
    if case is None or case["status"] != "active":
        raise ValueError("只允許處理仍在進行中的案件")
    cid = int(case["character_id"])
    grade = load_grade(gid, rumor_id, cid)
    rules = load_case_rules(gid, rumor_id)
    if grade.rule_version != rules.version:
        raise ValueError("評級與案件規則快照版本不同")
    with get_connection() as db:
        tier_row = db.execute(
            "SELECT tier FROM rp_rumor_case_tiers "
            "WHERE guild_id=? AND rumor_id=?",
            (gid, rumor_id)
        ).fetchone()
    if tier_row is None or int(tier_row["tier"]) != grade.tier:
        raise ValueError("評級所用案件等級與正式等級不一致")

    approve_final_plan(
        gid, rumor_id, grade=grade.grade,
        tier=grade.tier, heat=grade.heat, trusted=True,
    )
    plan = load_reward_plan(gid, rumor_id, cid, phase="final")
    if (plan.grade != grade.grade or plan.tier != grade.tier or
        plan.heat != grade.heat or plan.version != grade.rule_version):
        raise RuntimeError("正式獎懲方案與評級快照不一致")
    return BridgedPlan(
        gid, rumor_id, cid, grade.grade, grade.tier, grade.heat,
        plan.fingerprint, dict(plan.changes),
    )
