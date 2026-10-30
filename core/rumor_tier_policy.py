
"""傳聞建立前的 T1–T5 官方分級設定（資料層）。

安裝：core/rumor_tier_policy.py
純工具，不能由 Discord extension loader 自動載入。
群主權限須由 /官方 入口核對；trusted=True 不是玩家授權機制。
不為未設定的分類猜測級別，也不修改既有案件。
"""
from __future__ import annotations

import time
from core.database import transaction

CATEGORIES = frozenset({
    '戀情', '商業', '工作', '人際', '私生活', '其他'
})
SOURCES = frozenset({
    '玩家', '造謠', '系統', '條件'
})


def initialize(db) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS rp_rumor_tier_policy (
            guild_id TEXT NOT NULL,
            category TEXT NOT NULL,
            source_type TEXT NOT NULL,
            tier INTEGER NOT NULL
                CHECK(tier BETWEEN 1 AND 5),
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,category,source_type)
        )
    ''')


def _validate(guild_id, category, source_type):
    gid = str(guild_id).strip()

    if (
        not gid
        or category not in CATEGORIES
        or source_type not in SOURCES
    ):
        raise ValueError(
            '伺服器、傳聞分類或來源無效'
        )

    return gid


def set_tier_policy(
    guild_id,
    category,
    source_type,
    tier,
    *,
    trusted=False,
):
    """供已驗證群主身分的後端設定入口呼叫；只影響新案。"""

    if not trusted:
        raise PermissionError(
            '只有可信任的官方設定流程可以修改分級'
        )

    gid = _validate(
        guild_id, category, source_type
    )

    if type(tier) is not int or tier not in range(1, 6):
        raise ValueError(
            '案件等級必須為 T1～T5'
        )

    with transaction() as db:
        initialize(db)

        db.execute('''
            INSERT INTO rp_rumor_tier_policy
            (guild_id,category,source_type,tier,updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(guild_id,category,source_type)
            DO UPDATE SET
                tier=excluded.tier,
                updated_at=excluded.updated_at
        ''', (
            gid,
            category,
            source_type,
            tier,
            int(time.time()),
        ))

    return tier


def resolve_tier_in_transaction(
    db,
    guild_id,
    category,
    source_type,
) -> int:
    """在傳聞建立的同一筆交易中讀取官方分級。"""

    if not db.in_transaction:
        raise RuntimeError(
            '必須在已開啟的交易中解析案件等級'
        )

    gid = _validate(
        guild_id, category, source_type
    )

    initialize(db)

    row = db.execute('''
        SELECT tier
        FROM rp_rumor_tier_policy
        WHERE guild_id=?
          AND category=?
          AND source_type=?
    ''', (
        gid,
        category,
        source_type,
    )).fetchone()

    if row is None:
        raise LookupError(
            '官方尚未設定此分類及來源的 '
            'T1～T5 等級，暫不建立傳聞'
        )

    return int(row['tier'])
