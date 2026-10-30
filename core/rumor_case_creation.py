
"""傳聞案件建立器：案件、分類、規則版本、等級原子寫入。

位置：core/rumor_case_creation.py
純後端工具，不是 Discord Cog。

呼叫前上游必須驗證：
- 角色擁有權與啟用狀態
- 傳聞分類授權
- 建案來源與群主／管理員權限
- tier 由系統計算，不接受玩家自行指定
- rules 必須來自伺服器已啟用的正式版本

上游須使用 core.database.transaction()，
本函式只在既有交易中執行，不自行提交。
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


CATEGORIES = frozenset({
    "戀情",
    "商業",
    "工作",
    "人際",
    "私生活",
    "其他",
})

TIERS = frozenset({1, 2, 3, 4, 5})


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
    """一次建立傳聞案件及其不可變規則資料。

    此函式刻意不提供 tier 的預設值，
    避免舊程式略過正式等級判定。
    """

    if not trusted:
        raise PermissionError(
            "只有可信任的案件建立流程可以呼叫"
        )

    if not db.in_transaction:
        raise RuntimeError(
            "必須在已開啟的 SQLite 交易中呼叫"
        )

    if type(character_id) is not int or character_id <= 0:
        raise ValueError("角色編號不合法")

    if type(tier) is not int or tier not in TIERS:
        raise ValueError("案件等級必須是 T1～T5")

    if (
        type(settle_seconds) is not int
        or settle_seconds <= 0
    ):
        raise ValueError("結算時間不合法")

    if not isinstance(rules, RuleConfig):
        raise TypeError("正式規則必須是 RuleConfig")

    gid = str(guild_id).strip()
    bid = str(business_id).strip()
    creator = str(creator_id).strip()
    source = str(source_type).strip()

    if not gid or not creator or not source:
        raise ValueError("案件來源資訊不完整")

    title = str(title).strip()
    description = str(description).strip()
    category = str(category).strip()

    if not title or len(title) > 150:
        raise ValueError("傳聞標題長度不合法")

    if not description or len(description) > 4000:
        raise ValueError("傳聞內容長度不合法")

    if category not in CATEGORIES:
        raise ValueError("不支援的傳聞分類")

    # 使用第17個檔案提供的規則正規化方法。
    rule_data = _rule_data(rules)
    encoded = _json(rule_data)
    digest = _digest(rule_data)

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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gid,
            bid,
            character_id,
            source,
            creator,
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
        VALUES (?, ?, ?, ?)
        """,
        (gid, rumor_id, category, 1),
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
            gid,
            rumor_id,
            encoded,
            digest,
            now,
        ),
    )

    # 仍在同一筆外層交易中。
    # 這個函式也會檢查案件存在與原等級一致性。
    lock_case_tier_in_transaction(
        db,
        gid,
        rumor_id,
        tier,
    )

    return rumor_id
