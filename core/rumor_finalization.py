
"""傳聞正式結案協調器（單案件入口，尚未連接排程）。

安裝：core/rumor_finalization.py。純工具模組，不是 Discord extension。

前提：案件建立時已有規則快照；公關評級已由可信任流程核定；
正式獎懲方案已由 core.rumor_plan_store.lock_reward_plan 保存。
此模組不自行選擇評級、計算獎懲，也不自動替舊案補發。

操作順序：讀取鎖定方案 -> 建立不可變工作單 -> 數值獎懲去重執行
-> 確認工作單已 applied -> 把案件狀態更新為 settled。
若發獎後當機，可由同一入口重試；原有數值核心按 source_id 去重。
注意：不得與 modules.pr_rumor.settle_due 的舊結案排程同時啟用。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from core.database import get_connection, transaction
from core.rumor_plan_store import load_reward_plan
from core.rumor_grade_service import load_grade
from core.rumor_reward_job_builder import prepare_locked_reward_job
from modules.rumor_settlement import settle_character_reward


@dataclass(frozen=True)
class FinalizationResult:
    guild_id: str
    rumor_id: int
    character_id: int
    grade: str
    state: str
    duplicate: bool


def finalize_locked_case(
    guild_id: str | int,
    rumor_id: int,
    *,
    trusted: bool = False,
    now: int | None = None,
) -> FinalizationResult:
    """只結算已有正式鎖定方案、且已到期的主要當事人案件。

    必須由受信任的後端流程呼叫，不能直接接受 Discord 玩家輸入。
    本版不支援空數值方案、金幣、死忠粉與警方後續案件。
    """
    if not trusted:
        raise PermissionError("只有可信任的系統結算器可以執行結案")
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("伺服器或傳聞編號無效")
    timestamp = int(time.time()) if now is None else int(now)

    with get_connection() as db:
        case = db.execute(
            "SELECT character_id, status, settle_at FROM rp_company_rumors "
            "WHERE guild_id=? AND id=?",
            (gid, rumor_id),
        ).fetchone()
        if case is None:
            raise LookupError("傳聞案件不存在")
        cid = int(case["character_id"])
        status = str(case["status"])
        if status not in ("active", "settled"):
            raise ValueError("案件狀態不允許正式結算")
        if status == "active" and int(case["settle_at"]) > timestamp:
            raise ValueError("案件尚未到達72小時結算時間")

    # 規則快照、方案指紋與角色歸屬都會由載入器再次驗證。
    plan = load_reward_plan(gid, rumor_id, cid, phase="final")
    grade_snapshot = load_grade(gid, rumor_id, cid)
    if (
        grade_snapshot.grade != plan.grade
        or grade_snapshot.tier != plan.tier
        or grade_snapshot.heat != plan.heat
        or grade_snapshot.rule_version != plan.version
    ):
        raise RuntimeError("評級快照與正式獎懲方案不一致，禁止結案")
    if not plan.changes:
        raise ValueError("本版尚不支援無數值變動案件；不可擅自標記結案")

    if status == "settled":
        # 只認可已有 applied 工作單的已結案案件；舊案不能事後補發。
        with get_connection() as db:
            job = db.execute(
                "SELECT state, grade FROM rp_rumor_reward_jobs "
                "WHERE guild_id=? AND rumor_id=? AND phase='final' "
                "AND character_id=?",
                (gid, rumor_id, cid),
            ).fetchone()
        if job is None or job["state"] != "applied" or job["grade"] != plan.grade:
            raise ValueError("已結案案件缺少已完成獎懲工作單，請人工核對")
        return FinalizationResult(gid, rumor_id, cid, plan.grade, "settled", True)

    # 若這裡失敗，維持 active；不會讓案件在獎懲前先結束。
    prepare_locked_reward_job(gid, rumor_id, cid, trusted=True)
    outcome = settle_character_reward(
        gid, rumor_id, cid, plan.grade, plan.changes, trusted=True
    )
    if outcome.state != "applied":
        raise RuntimeError("獎懲工作尚未完成，案件不得結案")

    # 已完成的獎懲可能先於這筆交易提交；若此處當機，重試不會重複發獎。
    with transaction() as db:
        row = db.execute(
            "SELECT status, settle_at FROM rp_company_rumors "
            "WHERE guild_id=? AND id=?",
            (gid, rumor_id),
        ).fetchone()
        if row is None or row["status"] not in ("active", "settled"):
            raise RuntimeError("結案前案件狀態已被其他流程改動")
        job = db.execute(
            "SELECT state, grade FROM rp_rumor_reward_jobs "
            "WHERE guild_id=? AND rumor_id=? AND phase='final' AND character_id=?",
            (gid, rumor_id, cid),
        ).fetchone()
        if job is None or job["state"] != "applied" or job["grade"] != plan.grade:
            raise RuntimeError("找不到已完成且評級一致的獎懲工作單")
        if row["status"] == "active":
            if int(row["settle_at"]) > timestamp:
                raise RuntimeError("案件結算時間已被更改")
            updated = db.execute(
                "UPDATE rp_company_rumors SET status='settled', settled_at=? "
                "WHERE guild_id=? AND id=? AND status='active' AND settle_at<=?",
                (timestamp, gid, rumor_id, timestamp),
            )
            if updated.rowcount != 1:
                raise RuntimeError("案件狀態已變更，拒絕標記結案")

    return FinalizationResult(
        gid, rumor_id, cid, plan.grade, "settled", bool(outcome.duplicate)
    )
