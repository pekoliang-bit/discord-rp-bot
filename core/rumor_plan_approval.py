"""傳聞正式獎懲方案核定入口（純後端，非 Discord extension）。

放置：core/rumor_plan_approval.py
只接受上游可信任評級器給出的 grade/tier/heat；上游必須先驗證
評級、嚴重度與熱度及官方核定權限，絕不可直接傳入玩家表單數值。
不發獎、不扣款、不結案、不補寫舊案規則快照。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.database import get_connection
from core.rumor_plan_store import (
    load_case_rules, plan_from_locked_rules,
    lock_reward_plan, load_reward_plan,
)


@dataclass(frozen=True)
class PlanApprovalResult:
    guild_id: str
    rumor_id: int
    character_id: int
    grade: str
    tier: int
    heat: int
    fingerprint: str
    changes: dict[str, int]


def approve_final_plan(
    guild_id: str | int, rumor_id: int, *,
    grade: str, tier: int, heat: int, trusted: bool = False,
) -> PlanApprovalResult:
    """以已存在的案件規則快照計算、鎖定並驗證正式方案。

    同一方案重試安全；不同方案重試會遭資料層拒絕。
    必須由受信任的公關評級流程呼叫，禁止直接暴露給玩家。
    """
    if not trusted:
        raise PermissionError("只有受信任的公關結算器可核定正式方案")
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("伺服器或案件編號無效")
    if type(tier) is not int or tier not in range(1, 6):
        raise ValueError("案件等級必須為 T1～T5")
    if type(heat) is not int or heat < 0:
        raise ValueError("案件熱度必須為非負整數")
    if grade not in ("S", "A", "B", "C", "D"):
        raise ValueError("公關評級必須為 S～D")

    # 若沒有快照，禁止將目前版本套到舊案。
    load_case_rules(gid, rumor_id)
    with get_connection() as db:
        case = db.execute(
            "SELECT character_id,status FROM rp_company_rumors "
            "WHERE guild_id=? AND id=?", (gid, rumor_id),
        ).fetchone()
        if case is None:
            raise LookupError("案件不存在")
        if case["status"] != "active":
            raise ValueError("只有進行中的案件可初次核定結算方案")
        cid = int(case["character_id"])
        character = db.execute(
            "SELECT id FROM characters WHERE guild_id=? AND id=?",
            (gid, cid),
        ).fetchone()
        if character is None:
            raise ValueError("主要當事人角色不存在")

    plan = plan_from_locked_rules(gid, rumor_id, cid, grade, tier, heat)
    lock_reward_plan(plan, phase="final", trusted=True)
    verified = load_reward_plan(gid, rumor_id, cid, phase="final")
    if verified != plan:
        raise RuntimeError("讀回的正式方案與核定結果不一致")
    return PlanApprovalResult(
        gid, rumor_id, cid, grade, tier, heat,
        verified.fingerprint, dict(verified.changes),
    )
