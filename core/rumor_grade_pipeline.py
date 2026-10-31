
"""傳聞到期公關評級協調器。純後端，勿當 Discord Cog 載入。

預設 dry_run=True，僅預覽，不寫資料庫；正式鎖定需由經驗證的
後端明確指定 dry_run=False、trusted=True。絕不扣款、發獎、結案。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from core.database import get_connection
from core.rumor_grade_preparation import prepare_rumor_grade
from core.rumor_grade_policy import load_grade_policy
from core.rumor_grade_service import calculate_grade, lock_grade, load_grade
from core.rumor_plan_store import load_case_rules


@dataclass(frozen=True)
class GradeLockResult:
    guild_id: str
    rumor_id: int
    character_id: int
    grade: str
    score: int
    fingerprint: str | None
    already_locked: bool
    dry_run: bool


def lock_prepared_grade(
    guild_id: str | int,
    rumor_id: int,
    *,
    trusted: bool = False,
    dry_run: bool = True,
    now: int | None = None,
) -> GradeLockResult:
    """核對到期案件並預覽／鎖定評級，缺資料就拒絕處理。"""
    if not trusted:
        raise PermissionError("只能由已驗證的結算後端呼叫")

    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("伺服器或案件編號無效")
    if type(dry_run) is not bool:
        raise ValueError("dry_run 必須是布林值")

    timestamp = int(time.time()) if now is None else int(now)

    with get_connection() as db:
        row = db.execute(
            "SELECT character_id,status,settle_at FROM rp_company_rumors "
            "WHERE guild_id=? AND id=?",
            (gid, rumor_id),
        ).fetchone()

    if row is None:
        raise LookupError("案件不存在")
    if row["status"] != "active":
        raise ValueError("案件已結束或不在進行中")
    if int(row["settle_at"]) > timestamp:
        raise ValueError("尚未到達72小時結算時間")

    cid = int(row["character_id"])

    # 已鎖定者只讀舊結果，絕不重新計分或覆寫。
    try:
        previous = load_grade(gid, rumor_id, cid)
    except LookupError:
        previous = None

    if previous is not None:
        return GradeLockResult(
            gid, rumor_id, cid,
            previous.grade, previous.score,
            previous.fingerprint, True, dry_run,
        )

    preview = prepare_rumor_grade(
        gid, rumor_id, trusted=True, lock=False
    )
    if (
        preview.character_id != cid
        or preview.rumor_id != rumor_id
    ):
        raise RuntimeError("評級預覽和當事案件不一致")

    rules = load_case_rules(gid, rumor_id)
    policy = load_grade_policy(
        gid, preview.policy_version
    )
    expected = calculate_grade(
        preview.score, policy.thresholds
    )

    if expected != preview.grade:
        raise RuntimeError("評級預覽與鎖定門檻不一致")

    if dry_run:
        return GradeLockResult(
            gid, rumor_id, cid, expected,
            preview.score, None, False, True,
        )

    # 下游透過資料庫唯一鍵防止不同快照覆寫。
    # 正式上線金流前仍須驗證跨連線並發一致性。
    locked = lock_grade(
        gid,
        rumor_id,
        character_id=cid,
        score=preview.score,
        heat=preview.heat,
        tier=preview.tier,
        rule_version=rules.version,
        thresholds=policy.thresholds,
        trusted=True,
    )

    return GradeLockResult(
        gid, rumor_id, cid,
        locked.grade, locked.score,
        locked.fingerprint, False, False,
    )
