
"""公關付費安全入口與案件等級鎖定。

安裝：core/rumor_pr_action_service.py
非 Discord extension。

創案流程應在建立案件的同一筆交易中呼叫
lock_case_tier_in_transaction()。

案件等級只能由可信任的系統規則決定。
舊案件不會自動推定等級。
"""

from __future__ import annotations

import time

from core.database import transaction
from core.rumor_payment import (
    PR_COSTS,
    initialize,
    spend_for_pr_action,
    RumorPaymentError,
)


def lock_case_tier_in_transaction(
    db,
    guild_id,
    rumor_id,
    tier,
):
    """在現有交易內鎖定案件等級。

    重複鎖定相同等級可以通過。
    嘗試改為其他等級則直接拒絕。
    """

    if not db.in_transaction:
        raise RuntimeError(
            "必須在現有的資料庫交易內執行"
        )

    if (
        type(tier) is not int
        or tier not in PR_COSTS
    ):
        raise ValueError(
            "案件等級只能是 T1～T5"
        )

    gid = str(guild_id).strip()

    if (
        not gid
        or type(rumor_id) is not int
        or rumor_id <= 0
    ):
        raise ValueError(
            "伺服器或案件識別碼錯誤"
        )

    initialize(db)

    case = db.execute(
        """
        SELECT id
        FROM rp_company_rumors
        WHERE guild_id=? AND id=?
        """,
        (gid, rumor_id),
    ).fetchone()

    if case is None:
        raise LookupError(
            "找不到傳聞案件"
        )

    db.execute(
        """
        INSERT OR IGNORE INTO rp_rumor_case_tiers (
            guild_id,
            rumor_id,
            tier,
            locked_at
        )
        VALUES (?,?,?,?)
        """,
        (
            gid,
            rumor_id,
            tier,
            int(time.time()),
        ),
    )

    row = db.execute(
        """
        SELECT tier
        FROM rp_rumor_case_tiers
        WHERE guild_id=? AND rumor_id=?
        """,
        (gid, rumor_id),
    ).fetchone()

    if int(row["tier"]) != tier:
        raise RumorPaymentError(
            "案件已鎖定其他等級，拒絕覆蓋"
        )

    return tier


def charge_verified_pr_action(
    guild_id,
    actor_id,
    character_id,
    rumor_id,
    strategy,
    resource,
    request_key,
    *,
    effect_writer=None,
):
    """執行正式公關付費。

    核心扣費函式會在同一筆資料庫交易內
    讀取已鎖定的案件等級。

    上游仍需驗證角色權限及策略合法性。
    """

    return spend_for_pr_action(
        guild_id,
        actor_id,
        character_id,
        rumor_id,
        None,
        strategy,
        resource,
        request_key,
        effect_writer=effect_writer,
        admin_override=False,
    )
