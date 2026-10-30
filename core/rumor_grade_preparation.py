
"""傳聞評級準備器（更新版，僅預覽，不發獎）。

位置：core/rumor_grade_preparation.py
整合已鎖定的案件等級、官方門檻、公關行動、熱度及能力／難度修正快照。
正式評級鎖定尚需驗證修正數據的實際來源、時間點與並行一致性；
本版 lock=True 仍拒絕，避免不完整資料影響正式玩家資產。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.database import get_connection
from core.rumor_grade_policy import load_grade_policy
from core.rumor_pr_score_summary import summarize_pr_score
from core.rumor_heat_snapshot import load_heat_snapshot
from core.rumor_grade_adjustments import load_adjustment, adjusted_score
from core.rumor_grade_service import calculate_grade
from core.rumor_plan_store import load_case_rules


@dataclass(frozen=True)
class GradePreparation:
    guild_id: str
    rumor_id: int
    character_id: int
    policy_version: str
    base_score: int
    ability_bonus: int
    complexity_penalty: int
    heat_modifier: int
    score: int
    heat: int
    tier: int
    grade: str
    locked: bool
    plan_fingerprint: str | None


def prepare_rumor_grade(
    guild_id: str | int,
    rumor_id: int,
    *,
    trusted: bool = False,
    lock: bool = False,
) -> GradePreparation:
    """核對來源並提供 S～D 預覽；目前不執行鎖定與發獎。"""
    if not trusted:
        raise PermissionError("只有可信任的結算後端可執行")
    if lock:
        raise RuntimeError("正式評級鎖定尚未通過跨系統一致性驗證，禁止發獎")
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("伺服器或案件編號無效")

    with get_connection() as db:
        case = db.execute(
            "SELECT character_id,status FROM rp_company_rumors WHERE guild_id=? AND id=?",
            (gid, rumor_id),
        ).fetchone()
        if case is None or case["status"] != "active":
            raise ValueError("案件不存在或不是進行中")
        cid = int(case["character_id"])
        policy_row = db.execute(
            "SELECT version FROM rp_rumor_case_grade_policy WHERE guild_id=? AND rumor_id=?",
            (gid, rumor_id),
        ).fetchone()
        tier_row = db.execute(
            "SELECT tier FROM rp_rumor_case_tiers WHERE guild_id=? AND rumor_id=?",
            (gid, rumor_id),
        ).fetchone()
    if policy_row is None or tier_row is None:
        raise LookupError("案件缺少建立時鎖定的評級門檻或 T 級")
    tier = int(tier_row["tier"])
    if tier not in (1, 2, 3, 4, 5):
        raise RuntimeError("案件 T 級資料損壞")

    # 規則版本與門檻皆從原案件讀取，不使用目前的新設定覆蓋。
    load_case_rules(gid, rumor_id)
    policy = load_grade_policy(gid, policy_row["version"])
    summary = summarize_pr_score(gid, rumor_id, cid)
    heat = load_heat_snapshot(gid, rumor_id)
    adjust = load_adjustment(gid, rumor_id, cid)
    score = adjusted_score(summary.score, adjust)
    grade = calculate_grade(score, policy.thresholds)

    return GradePreparation(
        guild_id=gid,
        rumor_id=rumor_id,
        character_id=cid,
        policy_version=policy.version,
        base_score=summary.score,
        ability_bonus=adjust.ability_bonus,
        complexity_penalty=adjust.complexity_penalty,
        heat_modifier=adjust.heat_modifier,
        score=score,
        heat=heat.heat,
        tier=tier,
        grade=grade,
        locked=False,
        plan_fingerprint=None,
    )
