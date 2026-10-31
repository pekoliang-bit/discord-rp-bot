
"""傳聞到期案件逐階段準備器；不發獎、不扣款、不結案。

放 core/rumor_settlement_stager.py。預設 dry_run，只有正式授權的後端
可以逐階段寫入評級、獎懲方案、prepared 工作單。勿直接供玩家調用。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from core.database import get_connection
from core.rumor_grade_pipeline import lock_prepared_grade
from core.rumor_grade_plan_bridge import approve_from_locked_grade
from core.rumor_reward_job_builder import prepare_locked_reward_job


@dataclass(frozen=True)
class StageResult:
    guild_id: str
    rumor_id: int
    character_id: int
    stage: str
    detail: str
    dry_run: bool


def stage_due_case(
    guild_id: str | int,
    rumor_id: int,
    *,
    trusted: bool = False,
    dry_run: bool = True,
    now: int | None = None,
) -> StageResult:
    """每次只前進一階段，遇到缺資料立即停止。"""

    if not trusted:
        raise PermissionError("只能由已核實權限的後端執行")

    if type(dry_run) is not bool:
        raise ValueError("dry_run 必須為布林值")

    if type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("傳聞編號無效")

    if now is not None and type(now) is not int:
        raise ValueError("now 必須為整數 Unix 秒數")

    gid = str(guild_id).strip()

    if not gid:
        raise ValueError("伺服器編號不可空白")

    timestamp = int(time.time()) if now is None else now

    with get_connection() as db:
        case = db.execute(
            "SELECT character_id,status,settle_at "
            "FROM rp_company_rumors "
            "WHERE guild_id=? AND id=?",
            (gid, rumor_id),
        ).fetchone()

    if case is None:
        raise LookupError("傳聞不存在")

    if case["status"] != "active":
        raise ValueError("案件並非進行中，禁止重做獎懲")

    if int(case["settle_at"]) > timestamp:
        raise ValueError("案件尚未到結算時間")

    cid = int(case["character_id"])

    # 預覽絕不寫入，也不建立方案或工作單。
    if dry_run:
        result = lock_prepared_grade(
            gid,
            rumor_id,
            trusted=True,
            dry_run=True,
            now=timestamp,
        )

        if result.character_id != cid or not result.dry_run:
            raise RuntimeError(
                "評級預覽違反唯讀要求或角色不符"
            )

        return StageResult(
            gid,
            rumor_id,
            cid,
            "preview",
            f"{result.grade} / {result.score}分（未寫入）",
            True,
        )

    # 第一階段：鎖定正式評級。
    grade = lock_prepared_grade(
        gid,
        rumor_id,
        trusted=True,
        dry_run=False,
        now=timestamp,
    )

    if grade.character_id != cid:
        raise RuntimeError("正式評級角色不符")

    if not grade.already_locked:
        return StageResult(
            gid,
            rumor_id,
            cid,
            "grade_locked",
            f"已鎖定 {grade.grade} 級，尚未發獎",
            False,
        )

    # 第二階段：核准正式獎懲方案。
    from core.rumor_plan_store import load_reward_plan

    try:
        plan = load_reward_plan(
            gid,
            rumor_id,
            cid,
            phase="final",
        )
    except LookupError:
        plan = None

    if plan is None:
        approved = approve_from_locked_grade(
            gid,
            rumor_id,
            trusted=True,
        )

        if approved.character_id != cid:
            raise RuntimeError("獎懲方案角色不符")

        return StageResult(
            gid,
            rumor_id,
            cid,
            "plan_locked",
            "已核准正式獎懲方案；尚未建立工作單",
            False,
        )

    # 第三階段：建立或核對工作單。
    # 不執行工作單內容，不修改玩家資產。
    job = prepare_locked_reward_job(
        gid,
        rumor_id,
        cid,
        trusted=True,
    )

    return StageResult(
        gid,
        rumor_id,
        cid,
        "job_" + job.state,
        "工作單已存在並完成一致性核對；未執行發獎",
        False,
    )
