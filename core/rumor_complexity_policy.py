
"""傳聞案件複雜度官方配置（C1～C5，與 T 級獨立）。

安裝：core/rumor_complexity_policy.py。
純後端工具，不是 Discord extension。
群主權限必須由 /官方 入口核實。
設定只影響新案件。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from core.database import transaction

CATEGORIES = frozenset({
    "戀情", "商業", "工作",
    "人際", "私生活", "其他",
})
SOURCES = frozenset({
    "玩家", "造謠", "系統", "條件",
})

VERSION = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z"
)


@dataclass(frozen=True)
class ComplexityPolicy:
    guild_id: str
    category: str
    source_type: str
    complexity: int
    version: str


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS
        rp_rumor_complexity_policies (
            guild_id TEXT NOT NULL,
            category TEXT NOT NULL,
            source_type TEXT NOT NULL,
            complexity INTEGER NOT NULL
                CHECK(complexity BETWEEN 1 AND 5),
            version TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(
                guild_id, category, source_type
            )
        )
    """)


def _keys(guild_id, category, source_type):
    gid = str(guild_id).strip()

    if (
        not gid
        or category not in CATEGORIES
        or source_type not in SOURCES
    ):
        raise ValueError(
            "伺服器、傳聞分類或來源無效"
        )

    return gid


def set_complexity_policy(
    guild_id,
    category,
    source_type,
    complexity,
    version,
    *,
    trusted=False,
):
    """登錄官方設定；更新僅影響新案件。"""

    if not trusted:
        raise PermissionError(
            "只有經授權的官方後端可以設定"
        )

    gid = _keys(
        guild_id, category, source_type
    )

    if (
        type(complexity) is not int
        or complexity not in range(1, 6)
    ):
        raise ValueError(
            "複雜度必須為1～5"
        )

    if (
        not isinstance(version, str)
        or not VERSION.fullmatch(version)
    ):
        raise ValueError(
            "規則版本無效，例如 C1"
        )

    with transaction() as db:
        initialize(db)

        db.execute("""
            INSERT INTO
            rp_rumor_complexity_policies (
                guild_id, category,
                source_type, complexity,
                version, updated_at
            )
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(
                guild_id, category, source_type
            )
            DO UPDATE SET
                complexity=excluded.complexity,
                version=excluded.version,
                updated_at=excluded.updated_at
        """, (
            gid,
            category,
            source_type,
            complexity,
            version,
            int(time.time()),
        ))

    return ComplexityPolicy(
        gid, category, source_type,
        complexity, version
    )


def resolve_complexity_in_transaction(
    db,
    guild_id,
    category,
    source_type,
):
    """在新案件建立的相同交易內讀取複雜度。"""

    if not db.in_transaction:
        raise RuntimeError(
            "必須在建立案件的交易內讀取"
        )

    gid = _keys(
        guild_id, category, source_type
    )

    initialize(db)

    row = db.execute("""
        SELECT complexity, version
        FROM rp_rumor_complexity_policies
        WHERE guild_id=?
          AND category=?
          AND source_type=?
    """, (
        gid, category, source_type
    )).fetchone()

    if row is None:
        raise LookupError(
            "官方尚未設定此類型的案件複雜度"
        )

    level = int(row["complexity"])
    version = str(row["version"])

    if (
        level not in range(1, 6)
        or not VERSION.fullmatch(version)
    ):
        raise RuntimeError(
            "複雜度政策資料異常"
        )

    return ComplexityPolicy(
        gid, category, source_type,
        level, version
    )
