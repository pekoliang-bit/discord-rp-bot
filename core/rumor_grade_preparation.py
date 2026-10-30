
"""傳聞公關評級準備器。

安裝：core/rumor_grade_preparation.py

讀取：
- 案件已鎖定的 T1～T5 等級
- 建案時的官方評級門檻版本
- 公關行動累積分數
- 熱度快照
- 公關能力與屬性版本
- 案件複雜度及其來源版本
- 已保存的評級修正

本版只提供預覽，不發獎、不結案。
評級修正數值仍須由正式換算規則驗證。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.database import get_connection
from core.rumor_grade_policy import load_grade_policy
from core.rumor_pr_score_summary import (
    summarize_pr_score,
)
from core.rumor_heat_snapshot import (
    load_heat_snapshot,
)
from core.rumor_grade_adjustments import (
    load_adjustment,
    adjusted_score,
)
from core.rumor_grade_service import calculate_grade
from core.rumor_plan_store import load_case_rules
from core.rumor_pr_ability_source import read_pr_ability
from core.rumor_complexity_source import read_complexity


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

    pr_ability_value: int
    pr_ability_version: int
    complexity: int
    complexity_source_version: str


def prepare_rumor_grade(
    guild_id: str | int,
    rumor_id: int,
    *,
    trusted: bool = False,
    lock: bool = False,
) -> GradePreparation:
    """核對案件資料並預覽 S～D 結果。"""

    if not trusted:
        raise PermissionError(
            "只有可信任的結算後端可執行"
        )

    if lock:
        raise RuntimeError(
            "正式評級鎖定尚未通過跨系統驗證，禁止發獎"
        )

    gid = str(guild_id).strip()

    if (
        not gid
        or type(rumor_id) is not int
        or rumor_id <= 0
    ):
        raise ValueError(
            "伺服器或案件編號無效"
        )

    with get_connection() as db:
        case = db.execute(
            """
            SELECT character_id, status
            FROM rp_company_rumors
            WHERE guild_id=? AND id=?
            """,
            (gid, rumor_id),
        ).fetchone()

        if case is None or case["status"] != "active":
            raise ValueError(
                "案件不存在或不是進行中"
            )

        cid = int(case["character_id"])

        policy_row = db.execute(
            """
            SELECT version
            FROM rp_rumor_case_grade_policy
            WHERE guild_id=? AND rumor_id=?
            """,
            (gid, rumor_id),
        ).fetchone()

        tier_row = db.execute(
            """
            SELECT tier
            FROM rp_rumor_case_tiers
            WHERE guild_id=? AND rumor_id=?
            """,
            (gid, rumor_id),
        ).fetchone()

    if policy_row is None or tier_row is None:
        raise LookupError(
            "案件缺少評級門檻版本或正式 T 級"
        )

    tier = int(tier_row["tier"])

    if tier not in (1, 2, 3, 4, 5):
        raise RuntimeError(
            "案件 T 級資料損壞"
        )

    # 舊案件不能使用新版規則取代原始快照。
    load_case_rules(
        gid,
        rumor_id,
    )

    policy = load_grade_policy(
        gid,
        policy_row["version"],
    )

    summary = summarize_pr_score(
        gid,
        rumor_id,
        cid,
    )

    heat = load_heat_snapshot(
        gid,
        rumor_id,
    )

    ability = read_pr_ability(
        gid,
        cid,
    )

    complexity = read_complexity(
        gid,
        rumor_id,
    )

    adjust = load_adjustment(
        gid,
        rumor_id,
        cid,
    )

    # 已保存的修正目前尚未核對官方換算來源。
    # 此結果只能用於預覽，不能正式結算。
    score = adjusted_score(
        summary.score,
        adjust,
    )

    grade = calculate_grade(
        score,
        policy.thresholds,
    )

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
        pr_ability_value=ability.value,
        pr_ability_version=ability.attribute_version,
        complexity=complexity.complexity,
        complexity_source_version=complexity.source_version,
    )
