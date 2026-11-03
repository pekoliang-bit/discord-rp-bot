
"""排行榜基礎獎勵封存帳本。

- 30d 才建立基礎獎勵
- 尊重路人排行榜總開關、個別開關
- 使用既有獎勵計算規則
- 不直接發金幣或數值
- 重複封存不重複建立
"""

from __future__ import annotations

import json

from core.database import transaction
from modules.leaderboard_storage import initialize
from modules.leaderboard_reward_rules import calculate
from modules.leaderboard_milestone_service import (
    stage_milestones,
)
from modules.leaderboard_entity_identity import (
    parse_identity,
)
from modules.leaderboard_payout_identity import (
    CHARACTER_BOARDS,
)


def _settings(raw):
    try:
        obj = json.loads(raw or "{}")
        return obj if isinstance(obj, dict) else {}
    except (TypeError, ValueError):
        return {}


def _character_id(board, entity_key):
    """只有角色類排行榜才嘗試取角色 ID。"""
    if board not in CHARACTER_BOARDS:
        return None

    parsed = parse_identity(entity_key)

    if parsed is not None:
        if parsed["entity_type"] != "character":
            return None
        value = parsed["entity_id"]
    else:
        # 舊快照相容：只用於資格檢查，不核准付款。
        value = str(entity_key)

    if not value.isdecimal():
        return None

    return int(value)


def _eligible(db, gid, board, entity_key):
    """封存時檢查路人資格，其他榜單保留原規則。"""
    if board not in CHARACTER_BOARDS:
        return True

    cid = _character_id(board, entity_key)
    if cid is None:
        return False

    row = db.execute(
        """
        SELECT status,settings_json
        FROM characters
        WHERE guild_id=? AND id=?
        """,
        (gid, cid),
    ).fetchone()

    if row is None or row["status"] != "active":
        return False

    cfg = _settings(row["settings_json"])

    if cfg.get("character_category") != "extra":
        return True

    if cfg.get("ranking_eligible", True) is False:
        return False

    exists = db.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='extra_settings'
        """
    ).fetchone()

    if exists is None:
        return True

    switch = db.execute(
        """
        SELECT allow_ranking
        FROM extra_settings
        WHERE guild_id=?
        """,
        (gid,),
    ).fetchone()

    return (
        switch is None
        or bool(switch["allow_ranking"])
    )


def stage_rewards(
    guild_id,
    cycle,
    index,
    board,
    ranked_rows,
):
    """封存合格快照獎勵，不執行入帳。"""
    if cycle != "30d":
        return 0

    initialize()

    gid = str(guild_id)
    qualified = []
    count = 0

    with transaction() as db:
        for row in ranked_rows:
            rank = int(row["rank"])
            entity_key = str(row["entity_key"])

            if rank < 1:
                continue

            if not _eligible(
                db, gid, board, entity_key
            ):
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
                    sort_keys=True,
                )

            cursor = db.execute(
                """
                INSERT OR IGNORE INTO lb_v11_awards (
                    guild_id,cycle,idx,board,
                    entity_key,rank,reward_json,state
                )
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    gid,
                    cycle,
                    int(index),
                    str(board),
                    entity_key,
                    rank,
                    payload,
                    state,
                ),
            )

            count += cursor.rowcount

            qualified.append({
                "rank": rank,
                "entity_key": entity_key,
            })

    # 保留里程碑原本獨立交易機制。
    if board != "awards" and qualified:
        stage_milestones(
            gid,
            int(index),
            board,
            qualified,
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
    """只允許交由正式派發服務，不直接修改資產。

    舊版尚未驗證的獎勵不能繞過核准程序。
    """
    raise PermissionError(
        "排行榜獎勵必須先完成正式版本核准、"
        "派發身分驗證及原子入帳適配，"
        "不得從封存帳本直接領取。"
    )
