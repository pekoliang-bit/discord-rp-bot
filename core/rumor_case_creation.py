
"""傳聞案件建立與規則快照原子寫入。

安裝：core/rumor_case_creation.py

純工具模組，勿作為 Discord extension 載入。
上游必須先完成資料表初始化與權限驗證。
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
from core.rumor_scoring_policy import (
    lock_case_policy_in_transaction,
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
    published_at: int | None = None,
) -> int:
    """在呼叫者的同一筆 SQLite 交易內建立案件。"""

    if not trusted:
        raise PermissionError(
            "僅限可信任的傳聞案件建立流程"
        )

    if not isinstance(rules, RuleConfig):
        raise TypeError("必須使用已核准的 RuleConfig")

    if not db.in_transaction:
        raise RuntimeError(
            "必須在已啟動的 SQLite 交易內呼叫"
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
            "複雜度必須為1～5，不能使用T級替代"
        )

    if (
        not isinstance(complexity_version, str)
        or not complexity_version.strip()
    ):
        raise ValueError("必須提供官方複雜度來源版本")

    data = _rule_data(rules)
    encoded = _json(data)
    digest = _digest(data)

    # 系統自動刊登可指定真正的 Discord 發文時間。
    # 其他正常建案流程仍使用目前時間。
    now = (
        int(time.time())
        if published_at is None
        else int(published_at)
    )

    if published_at is not None and (
        now < 1_577_836_800
        or now > int(time.time()) + 60
    ):
        raise ValueError("傳聞發布時間不合法")

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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        VALUES (?, ?, ?, ?)
        """,
        (guild_id, rid, category, 1),
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
        VALUES (?, ?, ?, ?, ?)
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

    lock_case_grade_policy_in_transaction(
        db,
        guild_id,
        rid,
        trusted=True,
    )

    lock_complexity_in_transaction(
        db,
        guild_id,
        rid,
        complexity,
        source_version=complexity_version,
        trusted=True,
    )

    lock_case_policy_in_transaction(
        db,
        guild_id,
        rid,
        trusted=True,
    )

    return rid
