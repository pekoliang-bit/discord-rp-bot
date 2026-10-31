"""傳聞付費公關流程：扣費與效果在同一筆交易內完成。

安裝：core/rumor_pr_paid_flow.py
純後端，不是 Discord Cog。

官方策略分數由經驗證的呼叫端提供，
不得接受玩家自行指定。
呼叫端必須完成 Discord 操作者授權檢查。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.database import get_connection
from core.rumor_pr_action_service import (
    charge_verified_pr_action,
)
from core.rumor_pr_effects import (
    create_effect_writer,
)


@dataclass(frozen=True)
class PaidPRResult:
    action_id: int
    cost: int
    resource: str
    repeat_count: int
    multiplier: float
    effective_score: int
    duplicate: bool


def perform_paid_pr_action(
    guild_id,
    actor_id,
    character_id,
    rumor_id,
    strategy,
    resource,
    request_key,
    *,
    official_base_score: int,
    trusted: bool = False,
) -> PaidPRResult:
    """扣費交易內寫入效果；相同請求不重扣。"""

    if not trusted:
        raise PermissionError(
            "只有已驗證的內部公關服務可操作"
        )

    if (
        type(official_base_score) is not int
        or not 0 <= official_base_score <= 10000
    ):
        raise ValueError(
            "官方策略基礎分數必須在 0～10000"
        )

    gid = str(guild_id).strip()

    if not gid or not str(request_key).strip():
        raise ValueError(
            "伺服器與操作識別碼不可為空"
        )

    writer = create_effect_writer(
        gid,
        rumor_id,
        character_id,
        strategy,
        official_base_score,
        trusted=True,
    )

    charged = charge_verified_pr_action(
        gid,
        actor_id,
        character_id,
        rumor_id,
        strategy,
        resource,
        request_key,
        effect_writer=writer,
    )

    # 重試請求不會再次執行效果回呼，
    # 因此必須核對原始效果紀錄。
    with get_connection() as db:
        row = db.execute(
            """
            SELECT
                base_score,
                effective_score,
                multiplier,
                strategy,
                rumor_id,
                character_id
            FROM rp_rumor_pr_effects
            WHERE guild_id=? AND action_id=?
            """,
            (gid, charged.action_id),
        ).fetchone()

    if row is None:
        raise RuntimeError(
            "扣費紀錄缺少公關效果；"
            "請人工核對，不得重扣"
        )

    if (
        int(row["base_score"]) != official_base_score
        or row["strategy"] != strategy
        or int(row["rumor_id"]) != rumor_id
        or int(row["character_id"]) != character_id
        or abs(
            float(row["multiplier"])
            - charged.effectiveness
        ) > 1e-9
    ):
        raise RuntimeError(
            "相同公關請求的效果與原始設定不一致"
        )

    return PaidPRResult(
        action_id=charged.action_id,
        cost=charged.cost,
        resource=charged.resource,
        repeat_count=charged.repeat_count,
        multiplier=charged.effectiveness,
        effective_score=int(row["effective_score"]),
        duplicate=charged.duplicated,
    )
