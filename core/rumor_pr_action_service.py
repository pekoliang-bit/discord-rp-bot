
"""傳聞公關付費入口：由可信任的案件等級解析器決定價格。

安裝 core/rumor_pr_action_service.py，非 Discord extension。
在現有 core.rumor_payment 外增加一道伺服器端案件等級檢查。
注意：這不是 Discord 指令；上游仍須驗證角色、互動及策略權限。
"""
from __future__ import annotations

from collections.abc import Callable
from core.database import get_connection
from core.rumor_payment import PR_COSTS, spend_for_pr_action


def charge_verified_pr_action(
    guild_id: str | int,
    actor_id: str | int,
    character_id: int,
    rumor_id: int,
    strategy: str,
    resource: str,
    request_key: str,
    *,
    resolve_tier: Callable[[object, str, int], int],
    effect_writer=None,
):
    """透過可信任回呼從資料庫取得案件等級，然後扣費。

    resolve_tier(db, guild_id, rumor_id) -> 1..5。
    該回呼不得讀取玩家提交的等級，且必須取用案件持久化等級。
    既有 spend_for_pr_action 負責同交易扣費／去重／效果寫入。
    回呼僅能從受信任的後端建立，禁止由玩家傳入。

    注意：此處先讀等級再進入扣款交易，可能發生 TOCTOU。
    必須把正式事件等級設為建立後不可變，才能安全啟用。
    """
    if not callable(resolve_tier):
        raise ValueError("必須提供可信任的案件等級解析器")
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("案件識別資料無效")
    with get_connection() as db:
        tier = resolve_tier(db, gid, rumor_id)
    if type(tier) is not int or tier not in PR_COSTS:
        raise ValueError("案件未具備有效的正式 T1～T5 等級")
    return spend_for_pr_action(
        gid, actor_id, character_id, rumor_id, tier,
        strategy, resource, request_key,
        effect_writer=effect_writer,
        admin_override=False,
    )
