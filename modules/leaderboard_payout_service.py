
"""排行榜統一派發入口。

保護機制：
- 檢查派發工作是否存在
- 已付款紀錄不重複處理
- 驗證獎勵身分與資料格式
- 暫定獎勵不得入帳
- 禁止繞過尚未完成的原子入帳適配

本版不會擅自啟用正式付款。
"""

from __future__ import annotations

import json

from modules.leaderboard_payout_storage import (
    get_job,
)
from modules.leaderboard_payout_policy import (
    require_payout_permission,
)
from modules.leaderboard_reward_validation import (
    validate_reward,
)
from modules.leaderboard_payout_identity import (
    validate_identity,
)


class PayoutNotReadyError(RuntimeError):
    """獎勵符合部分條件，但正式入帳適配尚未完成。"""


def inspect_payout(payout_key):
    job = get_job(str(payout_key))

    if job is None:
        return {
            "status": "missing",
            "payout_key": str(payout_key),
            "issues": ["找不到派發紀錄"],
        }

    if job["state"] == "paid":
        return {
            "status": "already_paid",
            "payout_key": str(payout_key),
            "issues": [],
        }

    issues = []

    try:
        reward = json.loads(
            job["reward_json"]
        )
    except (TypeError, ValueError):
        reward = None
        issues.append("獎勵JSON格式錯誤")

    if isinstance(reward, dict):
        board = reward.get("board")

        try:
            validate_reward(reward)
        except (TypeError, ValueError, KeyError) as exc:
            issues.append(str(exc))

        try:
            validate_identity(
                board,
                job["entity_key"],
            )
        except (TypeError, ValueError) as exc:
            issues.append(str(exc))

        if reward.get("reward_version") == (
            "v11-provisional"
        ):
            issues.append("暫定獎勵尚未核准")

        if reward.get("requires_adapter") is not True:
            issues.append("缺少正式入帳適配標記")

    else:
        issues.append("獎勵不是有效物件")

    if job["state"] not in (
        "ready",
        "processing",
    ):
        issues.append(
            f"派發工作目前狀態為{job['state']}"
        )

    return {
        "status": (
            "blocked" if issues else "review_required"
        ),
        "payout_key": str(payout_key),
        "guild_id": str(job["guild_id"]),
        "entity_key": str(job["entity_key"]),
        "reward": reward,
        "issues": issues,
    }


def payout(payout_key):
    """唯一正式派發入口；尚未核准時拒絕入帳。"""
    payout_key = str(payout_key)

    result = inspect_payout(payout_key)

    if result["status"] == "already_paid":
        return {
            "status": "already_paid",
            "payout_key": payout_key,
        }

    if result["status"] == "missing":
        raise ValueError("派發紀錄不存在")

    if result["issues"]:
        raise PermissionError(
            "；".join(result["issues"])
        )

    reward = result["reward"]

    require_payout_permission(
        reward["board"],
        result["entity_key"],
        reward,
    )

    # 真正入帳必須同時完成：
    # 1. 正式金幣雙向分錄
    # 2. 正式屬性獎勵
    # 3. 公司、陣營、CP及社群的獎勵歸屬
    # 4. 同一交易內將派發紀錄標記paid
    #
    # 在完整原子適配器完成前，不可只發錢
    # 再單獨發屬性，否則失敗重試會重複入帳。
    raise PayoutNotReadyError(
        "排行榜正式原子入帳適配器尚未完成；"
        "本次未發放任何金幣或數值。"
    )
