
"""傳聞案件建立與規則快照原子寫入。

安裝：core/rumor_case_creation.py
純工具模組，勿作為 Discord extension 載入。

上游須先初始化傳聞主資料表、分類資料表、
規則快照資料表，並取得官方已核准的 RuleConfig。

案件、分類、規則快照及案件等級
全部使用呼叫者提供的同一筆 SQLite 交易。
"""
from __future__ import annotations

import time

from core.rumor_plan_store import (
    _rule_data,
    _json,
    _digest,
)
from core.rumor_reward_planner import RuleConfig
from core.rumor_pr_action_service import (
    lock_case_tier_in_transaction,
)


def insert_case_and_snapshot(
    db,
    *,
    guild_id: str,
    business_id: str,
    character_id: int,
    source_type: str,
    creator_id: str,
    title: str,
    description: str,
    category: str,
    settle_seconds: int,
    tier: int,
    rules: RuleConfig,
    trusted: bool = False,
) -> int:
    """在同一交易中插入傳聞及其正式規則資料。

    不建立第二個資料庫連線或交易。
    權限、角色狀態、分類授權須由上游驗證。
    """

    if not trusted:
        raise PermissionError(
            "僅限可信任的傳聞案件建立流程"
        )

    if not isinstance(rules, RuleConfig):
        raise TypeError(
            "必須使用已核准的 RuleConfig"
        )

    if not db.in_transaction:
        raise RuntimeError(
            "必須在已啟動的 SQLite 交易內呼叫"
        )

    if type(character_id) is not int or character_id <= 0:
        raise ValueError("角色編號無效")

    if type(settle_seconds) is not int or settle_seconds <= 0:
        raise ValueError("結算期間無效")

    if type(tier) is not int or tier not in range(1, 6):
        raise ValueError(
            "案件等級必須是 T1～T5"
        )

    data = _rule_data(rules)
    encoded = _json(data)
    digest = _digest(data)

    now = int(time.time())

    cur = db.execute(
        """
        INSERT INTO rp_company_rumors (
            guild_id,
            business_id,
            character_id,
            source_type,
            creator_id,
            title,
            description,
            created_at,
            settle_at
        )
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            guild_id,
            business_id,
            character_id,
            source_type,
            creator_id,
            title,
            description,
            now,
            now + settle_seconds,
        ),
    )

    rid = int(cur.lastrowid)

    db.execute(
        """
        INSERT INTO rp_rumor_case_meta (
            guild_id,
            rumor_id,
            category,
            rule_version
        )
        VALUES (?,?,?,?)
        """,
        (
            guild_id,
            rid,
            category,
            1,
        ),
    )

    db.execute(
        """
        INSERT INTO rp_rumor_rule_snapshots (
            guild_id,
            rumor_id,
            rules_json,
            rules_hash,
            created_at
        )
        VALUES (?,?,?,?,?)
        """,
        (
            guild_id,
            rid,
            encoded,
            digest,
            now,
        ),
    )

    lock_case_tier_in_transaction(
        db,
        guild_id,
        rid,
        tier,
    )

    return rid
