
"""排行榜基礎獎勵待派發帳本。"""

import json

from core.database import transaction
from modules.leaderboard_storage import initialize
from modules.leaderboard_reward_rules import calculate
from modules.leaderboard_milestone_service import stage_milestones


def stage_rewards(guild_id, cycle, index, board, ranked_rows):
    """封存獎勵，不直接入帳。"""
    if cycle != "30d":
        return 0

    initialize()

    count = 0

    with transaction() as db:
        for row in ranked_rows:
            rank = int(row["rank"])
            entity_key = str(row["entity_key"])

            if rank < 1:
                continue

            reward = calculate(board, rank)

            if reward is None:
                state = "unconfigured"
                payload = "{}"
            else:
                state = "pending_adapter"
                payload = json.dumps(
                    reward,
                    ensure_ascii=False,
                )

            cursor = db.execute("""
                INSERT OR IGNORE INTO lb_v11_awards (
                    guild_id,
                    cycle,
                    idx,
                    board,
                    entity_key,
                    rank,
                    reward_json,
                    state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(guild_id),
                cycle,
                int(index),
                board,
                entity_key,
                rank,
                payload,
                state,
            ))

            count += cursor.rowcount

    # 先完成基礎獎勵交易，再處理里程碑。
    # 避免巢狀寫入交易。
    if board != "awards" and ranked_rows:
        stage_milestones(
            guild_id,
            index,
            board,
            ranked_rows,
        )

    return count


def record_unconfigured(
    guild_id,
    cycle,
    index,
    board,
    entity_key,
    rank,
):
    return stage_rewards(
        guild_id,
        cycle,
        index,
        board,
        [{
            "entity_key": entity_key,
            "rank": rank,
        }],
    )


def claim(
    guild_id,
    cycle,
    index,
    board,
    entity_key,
):
    raise NotImplementedError(
        "尚未完成正式入帳適配器，禁止直接派發"
    )
