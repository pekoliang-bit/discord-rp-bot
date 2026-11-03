
"""排行榜獎勵預覽，不執行入帳。"""

import json

from modules.leaderboard_payout_policy import inspect_reward


def preview_award(award_row):
    row = dict(award_row)

    try:
        reward = json.loads(row["reward_json"])
    except (KeyError, TypeError, ValueError):
        reward = None

    board = str(row.get("board", ""))
    entity_key = str(row.get("entity_key", ""))

    result = inspect_reward(
        board, entity_key, reward
    )

    return {
        "guild_id": str(row.get("guild_id", "")),
        "cycle": row.get("cycle"),
        "index": row.get("idx"),
        "board": board,
        "entity_key": entity_key,
        "rank": row.get("rank"),
        "reward": reward,
        "can_pay": result["allowed"],
        "issues": result["issues"],
    }
