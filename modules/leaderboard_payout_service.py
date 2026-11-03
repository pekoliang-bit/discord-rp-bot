
"""排行榜獎勵派發入口。

尚未完成原子入帳適配器，不允許實際派發。
"""

from modules.leaderboard_payout_storage import (
    get_job,
)
from modules.leaderboard_payout_policy import (
    require_payout_permission,
)


def payout(payout_key):
    job = get_job(payout_key)

    if job is None:
        raise ValueError("派發紀錄不存在")

    if job["state"] == "paid":
        return {
            "status": "already_paid",
            "payout_key": payout_key,
        }

    import json

    reward = json.loads(job["reward_json"])

    require_payout_permission(
        reward.get("board"),
        job["entity_key"],
        reward,
    )

    raise NotImplementedError(
        "尚未完成與經濟、角色、組織資料庫的"
        "原子交易整合，禁止自動派發"
    )
