
"""已鎖定傳聞獎懲工作單的安全執行入口。

安裝：core/rumor_reward_job_executor.py（純後端；不要註冊 Discord 指令）
預設 dry_run=True；不會自動加入排程，不處理金幣、粉絲或結案。
呼叫前須由 Discord 後端驗證權限；trusted 不是身分驗證。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from core.database import get_connection
from core.rumor_plan_store import load_reward_plan
from modules.rumor_settlement import settle_character_reward


@dataclass(frozen=True)
class ExecutionResult:
    guild_id: str
    rumor_id: int
    character_id: int
    state: str
    duplicate: bool
    dry_run: bool


def execute_locked_reward_job(
    guild_id: str | int,
    rumor_id: int,
    character_id: int,
    *,
    trusted: bool = False,
    dry_run: bool = True,
) -> ExecutionResult:
    """核對工作單和不可變 final 方案後，選擇性執行角色數值獎懲。"""
    if not trusted:
        raise PermissionError("僅限已驗證的內部結算後端")
    if type(dry_run) is not bool:
        raise ValueError("dry_run 必須是布林值")
    if type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("案件編號無效")
    if type(character_id) is not int or character_id <= 0:
        raise ValueError("角色編號無效")
    gid = str(guild_id).strip()
    if not gid:
        raise ValueError("伺服器編號無效")

    with get_connection() as db:
        row = db.execute(
            "SELECT r.status, j.grade, j.changes_json, j.state "
            "FROM rp_rumor_reward_jobs AS j "
            "JOIN rp_company_rumors AS r "
            "ON r.guild_id=j.guild_id AND r.id=j.rumor_id "
            "AND r.character_id=j.character_id "
            "WHERE j.guild_id=? AND j.rumor_id=? "
            "AND j.character_id=? AND j.phase='final'",
            (gid, rumor_id, character_id),
        ).fetchone()

    if row is None:
        raise LookupError("找不到本案已準備的正式獎懲工作單")
    if row["status"] not in ("active", "settled"):
        raise ValueError("案件狀態不允許處理工作單")
    if row["state"] not in ("prepared", "applied"):
        raise ValueError("工作單狀態不合法")

    plan = load_reward_plan(
        gid, rumor_id, character_id, phase="final"
    )
    canonical = json.dumps(
        plan.changes,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if (
        row["grade"] != plan.grade
        or row["changes_json"] != canonical
    ):
        raise RuntimeError(
            "工作單與正式鎖定方案不一致，拒絕發獎"
        )
    if not plan.changes:
        raise ValueError(
            "空獎懲方案不得進入此角色數值執行器"
        )

    if row["state"] == "applied":
        return ExecutionResult(
            gid, rumor_id, character_id,
            "applied", True, dry_run,
        )
    if row["status"] != "active":
        raise RuntimeError(
            "舊案已結案但工作單未完成，必須人工核對"
        )
    if dry_run:
        return ExecutionResult(
            gid, rumor_id, character_id,
            "prepared", False, True,
        )

    # 沿用既有來源去重機制，不自行新增第二套發獎方式。
    result = settle_character_reward(
        gid,
        rumor_id,
        character_id,
        plan.grade,
        dict(plan.changes),
        trusted=True,
    )
    return ExecutionResult(
        gid,
        rumor_id,
        character_id,
        result.state,
        result.duplicate,
        False,
    )
