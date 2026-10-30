
"""傳聞案件建立與規則快照原子寫入。

安裝：core/rumor_case_creation.py
純工具模組，不作為 Discord extension 載入。

上游需先驗證角色、案件來源及傳聞分類授權。
所有寫入都使用呼叫者提供的同一筆交易。
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
from core.rumor_grade_policy import (
    lock_case_grade_policy_in_transaction,
)
from core.rumor_complexity_source import (
    lock_complexity_in_transaction,
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
    complexity: int,
    complexity_version: str,
    rules: RuleConfig,
    trusted: bool = False,
) -> int:
    """建立案件、規則快照、T級與複雜度。

    呼叫者必須使用同一筆 SQLite 交易。
    """

    if not trusted:
        raise PermissionError(
            "僅限可信任的案件建立流程"
        )

    if not isinstance(rules, RuleConfig):
        raise TypeError(
            "必須使用已核准的 RuleConfig"
        )

    if not db.in_transaction:
        raise RuntimeError(
            "必須在已啟動的交易內呼叫"
        )

    if type(character_id) is not int or character_id <= 0:
        raise ValueError("角色編號無效")

    if type(settle_seconds) is not int or settle_seconds <= 0:
        raise ValueError("結算期間無效")

    if type(tier) is not int or tier not in range(1, 6):
        raise ValueError("案件等級必須是 T1～T5")

    if (
        type(complexity) is not int
        or complexity not in range(1, 6)
    ):
        raise ValueError(
            "複雜度必須為 1～5，且不能用 T 級代替"
        )

    if (
        not isinstance(complexity_version, str)
        or not complexity_version.strip()
    ):
        raise ValueError(
            "必須提供官方複雜度來源版本"
        )

    data = _rule_data(rules)
    encoded = _json(data)
    digest = _digest(data)

    now = int(time.time())

    cursor = db.execute(
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

    rumor_id = int(cursor.lastrowid)

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
            rumor_id,
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
            rumor_id,
            encoded,
            digest,
            now,
        ),
    )

    lock_case_tier_in_transaction(
        db,
        guild_id,
        rumor_id,
        tier,
    )

    lock_case_grade_policy_in_transaction(
        db,
        guild_id,
        rumor_id,
        trusted=True,
    )

    lock_complexity_in_transaction(
        db,
        guild_id,
        rumor_id,
        complexity,
        source_version=complexity_version,
        trusted=True,
    )

    return rumor_id
