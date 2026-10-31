
"""傳聞正式結算前唯讀檢查，不發獎、不變更資料。

安裝：core/rumor_finalization_readiness.py
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from core.database import get_connection
from core.rumor_grade_service import (
    load_grade,
    calculate_grade,
)
from core.rumor_plan_store import (
    load_case_rules,
    load_reward_plan,
)
from core.rumor_heat_snapshot import load_heat_snapshot
from core.rumor_grade_policy import load_grade_policy


@dataclass(frozen=True)
class CaseReadiness:
    guild_id: str
    rumor_id: int
    ready: bool
    code: str
    explanation: str


def _result(gid, rid, code, message):
    return CaseReadiness(
        gid, rid, False, code, message
    )


def check_case(
    guild_id: str | int,
    rumor_id: int,
    *,
    now: int | None = None,
) -> CaseReadiness:
    """核對到期、評級、規則及獎懲方案。"""

    gid = str(guild_id).strip()

    if (
        not gid
        or type(rumor_id) is not int
        or rumor_id <= 0
    ):
        raise ValueError("伺服器或案件編號無效")

    timestamp = (
        int(time.time()) if now is None else int(now)
    )

    with get_connection() as db:
        case = db.execute(
            """
            SELECT character_id, status, settle_at
            FROM rp_company_rumors
            WHERE guild_id=? AND id=?
            """,
            (gid, rumor_id),
        ).fetchone()

        if case is None:
            return _result(
                gid, rumor_id,
                "missing", "案件不存在"
            )

        if case["status"] != "active":
            return _result(
                gid, rumor_id,
                "not_active", "案件不是進行中"
            )

        if int(case["settle_at"]) > timestamp:
            return _result(
                gid, rumor_id,
                "not_due", "尚未到達結算時間"
            )

        cid = int(case["character_id"])

        tables = {
            row[0]
            for row in db.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table'
                """
            )
        }

        required = {
            "rp_rumor_rule_snapshots",
            "rp_rumor_locked_plans",
            "rp_rumor_grade_snapshots",
            "rp_rumor_heat_snapshots",
            "rp_rumor_case_grade_policy",
            "rp_rumor_case_tiers",
        }

        missing = required - tables

        if missing:
            return _result(
                gid, rumor_id,
                "missing_tables",
                "缺少結算資料表："
                + ", ".join(sorted(missing)),
            )

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

    if not policy_row or not tier_row:
        return _result(
            gid, rumor_id,
            "no_policy",
            "案件缺少鎖定的評級政策或T級",
        )

    try:
        rules = load_case_rules(gid, rumor_id)
        grade = load_grade(gid, rumor_id, cid)
        heat = load_heat_snapshot(gid, rumor_id)

        policy = load_grade_policy(
            gid, policy_row["version"]
        )

        plan = load_reward_plan(
            gid, rumor_id, cid, phase="final"
        )

    except LookupError as exc:
        return _result(
            gid, rumor_id,
            "missing_snapshot", str(exc)
        )

    except (
        ValueError, RuntimeError,
        KeyError, TypeError,
    ) as exc:
        return _result(
            gid, rumor_id,
            "invalid_snapshot", str(exc)
        )

    if (
        int(tier_row["tier"]) != grade.tier
        or plan.tier != grade.tier
    ):
        return _result(
            gid, rumor_id,
            "tier_mismatch",
            "方案、評級與案件T級不一致",
        )

    if (
        grade.heat != heat.heat
        or plan.heat != heat.heat
    ):
        return _result(
            gid, rumor_id,
            "heat_mismatch",
            "方案、評級與熱度快照不一致",
        )

    if (
        grade.grade != plan.grade
        or calculate_grade(
            grade.score, policy.thresholds
        ) != grade.grade
    ):
        return _result(
            gid, rumor_id,
            "grade_mismatch",
            "方案或正式門檻與評級不一致",
        )

    if (
        grade.rule_version != rules.version
        or plan.version != rules.version
    ):
        return _result(
            gid, rumor_id,
            "rule_mismatch",
            "獎懲規則版本不一致",
        )

    if not plan.changes:
        return _result(
            gid, rumor_id,
            "empty_plan",
            "目前結算器不支援空數值方案",
        )

    return CaseReadiness(
        gid,
        rumor_id,
        True,
        "ready",
        "可交由正式結算器再次驗證",
    )


def check_recent_cases(
    guild_id: str | int,
    limit: int = 50,
    *,
    now: int | None = None,
) -> list[CaseReadiness]:
    """檢查近期進行中的案件。"""

    gid = str(guild_id).strip()

    if not gid:
        raise ValueError("伺服器編號不可為空")

    limit = max(1, min(int(limit), 200))

    with get_connection() as db:
        rows = db.execute(
            """
            SELECT id
            FROM rp_company_rumors
            WHERE guild_id=? AND status='active'
            ORDER BY id DESC LIMIT ?
            """,
            (gid, limit),
        ).fetchall()

    return [
        check_case(
            gid,
            int(row["id"]),
            now=now,
        )
        for row in rows
    ]
