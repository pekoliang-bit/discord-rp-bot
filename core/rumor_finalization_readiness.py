"""傳聞結算準備狀態盤點（只讀，純後端）。

放置：core/rumor_finalization_readiness.py
不初始化資料表、不決定評級、不建立工作單，也不發獎。
"""
from __future__ import annotations

from dataclasses import dataclass
from core.database import get_connection
from core.rumor_plan_store import load_reward_plan, load_case_rules


@dataclass(frozen=True)
class CaseReadiness:
    guild_id: str
    rumor_id: int
    ready: bool
    code: str
    explanation: str


def check_case(guild_id: str | int, rumor_id: int) -> CaseReadiness:
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("伺服器或案件編號無效")
    with get_connection() as db:
        case = db.execute(
            "SELECT character_id,status FROM rp_company_rumors "
            "WHERE guild_id=? AND id=?", (gid, rumor_id),
        ).fetchone()
        if case is None:
            return CaseReadiness(gid, rumor_id, False, "missing", "案件不存在")
        if case["status"] != "active":
            return CaseReadiness(gid, rumor_id, False, "not_active", "案件並非進行中")
        cid = int(case["character_id"])

        snapshot_table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='rp_rumor_rule_snapshots'"
        ).fetchone()
        plan_table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='rp_rumor_locked_plans'"
        ).fetchone()
    if snapshot_table is None:
        return CaseReadiness(gid, rumor_id, False, "no_snapshot_table", "尚未建立規則快照表")
    if plan_table is None:
        return CaseReadiness(gid, rumor_id, False, "no_plan_table", "尚未建立正式方案表")
    try:
        load_case_rules(gid, rumor_id)
    except LookupError:
        return CaseReadiness(gid, rumor_id, False, "no_snapshot", "缺少案件建立時的規則快照")
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        return CaseReadiness(gid, rumor_id, False, "invalid_snapshot", str(exc))
    try:
        plan = load_reward_plan(gid, rumor_id, cid, phase="final")
    except LookupError:
        return CaseReadiness(gid, rumor_id, False, "no_plan", "尚未核定最終獎懲方案")
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        return CaseReadiness(gid, rumor_id, False, "invalid_plan", str(exc))
    if not plan.changes:
        return CaseReadiness(gid, rumor_id, False, "empty_plan", "目前結算器尚不支援空獎懲方案")
    return CaseReadiness(gid, rumor_id, True, "ready", "已具備規則快照與可信任的鎖定方案")


def check_recent_cases(guild_id: str | int, limit: int = 50) -> list[CaseReadiness]:
    gid = str(guild_id).strip()
    if not gid:
        raise ValueError("伺服器編號不可為空")
    limit = max(1, min(int(limit), 200))
    with get_connection() as db:
        rows = db.execute(
            "SELECT id FROM rp_company_rumors WHERE guild_id=? "
            "AND status='active' ORDER BY id DESC LIMIT ?", (gid, limit),
        ).fetchall()
    return [check_case(gid, int(row["id"])) for row in rows]
