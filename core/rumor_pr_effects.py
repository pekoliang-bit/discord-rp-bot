
"""傳聞公關行動效果記錄器。

位置：core/rumor_pr_effects.py
純工具模組，不是 Discord extension。

用途：
1. 保存每次付費公關行動的實際效果
2. 按既有扣費系統提供的遞減倍率計算分數
3. 提供同交易 effect_writer
4. 支援查詢案件公關累積分數

本檔不決定 S～D 最終評級，不發獎、不結案。
策略基礎分數必須由可信任的官方設定提供。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from core.database import get_connection


MAX_BASE_SCORE = 10000


@dataclass(frozen=True)
class PREffectResult:
    action_id: int
    rumor_id: int
    character_id: int
    strategy: str
    base_score: int
    multiplier: str
    effective_score: int


def initialize_pr_effects(db) -> None:
    """建立公關效果資料表，不修改既有紀錄。"""

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS rp_rumor_pr_effects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            rumor_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            action_id INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            base_score INTEGER NOT NULL
                CHECK(base_score BETWEEN 0 AND 10000),
            multiplier TEXT NOT NULL,
            effective_score INTEGER NOT NULL
                CHECK(effective_score >= 0),
            created_at INTEGER NOT NULL,
            UNIQUE(guild_id, action_id)
        )
        """
    )

    db.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_rumor_pr_effects_case
        ON rp_rumor_pr_effects (
            guild_id,
            rumor_id,
            character_id
        )
        """
    )


def create_effect_writer(
    guild_id: str | int,
    rumor_id: int,
    character_id: int,
    strategy: str,
    base_score: int,
    *,
    trusted: bool = False,
):
    """建立供扣費服務使用的效果寫入回呼。

    回傳函式簽名：
        writer(db, effectiveness, action_id)

    必須與扣費服務使用同一 SQLite 交易。
    """

    if not trusted:
        raise PermissionError(
            "僅限可信任的公關行動服務建立效果回呼"
        )

    gid = str(guild_id).strip()
    name = str(strategy).strip()

    if not gid:
        raise ValueError("伺服器編號不可為空")

    if type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("案件編號無效")

    if type(character_id) is not int or character_id <= 0:
        raise ValueError("角色編號無效")

    if not name or len(name) > 80:
        raise ValueError("策略名稱不合法")

    if (
        type(base_score) is not int
        or not 0 <= base_score <= MAX_BASE_SCORE
    ):
        raise ValueError("公關策略基礎分數不合法")

    def effect_writer(
        db,
        effectiveness,
        action_id,
    ):
        if not db.in_transaction:
            raise RuntimeError(
                "公關效果必須與扣費處於同一交易"
            )

        if type(action_id) is not int or action_id <= 0:
            raise ValueError("公關行動編號無效")

        multiplier = Decimal(str(effectiveness))

        if multiplier not in {
            Decimal("1"),
            Decimal("0.75"),
            Decimal("0.5"),
            Decimal("0.25"),
            Decimal("0.1"),
        }:
            raise ValueError("公關效果倍率不合法")

        effective_score = int(
            (
                Decimal(base_score) * multiplier
            ).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )

        initialize_pr_effects(db)

        # 必須確實存在已寫入的付費行動，
        # 且角色、案件及策略完全相符。
        charge = db.execute(
            """
            SELECT rumor_id, character_id, strategy
            FROM rp_rumor_pr_charges
            WHERE guild_id=? AND id=?
            """,
            (gid, action_id),
        ).fetchone()

        if charge is None:
            raise ValueError("找不到對應的公關扣費紀錄")

        if (
            int(charge["rumor_id"]) != rumor_id
            or int(charge["character_id"]) != character_id
            or charge["strategy"] != name
        ):
            raise ValueError(
                "公關扣費紀錄與效果資料不一致"
            )

        db.execute(
            """
            INSERT OR IGNORE INTO rp_rumor_pr_effects (
                guild_id,
                rumor_id,
                character_id,
                action_id,
                strategy,
                base_score,
                multiplier,
                effective_score,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gid,
                rumor_id,
                character_id,
                action_id,
                name,
                base_score,
                str(multiplier),
                effective_score,
                int(time.time()),
            ),
        )

        existing = db.execute(
            """
            SELECT base_score, multiplier, effective_score,
                   rumor_id, character_id, strategy
            FROM rp_rumor_pr_effects
            WHERE guild_id=? AND action_id=?
            """,
            (gid, action_id),
        ).fetchone()

        if (
            existing is None
            or int(existing["rumor_id"]) != rumor_id
            or int(existing["character_id"]) != character_id
            or existing["strategy"] != name
            or int(existing["base_score"]) != base_score
            or Decimal(existing["multiplier"]) != multiplier
            or int(existing["effective_score"]) != effective_score
        ):
            raise ValueError(
                "同一公關行動存在不同的效果資料"
            )

        return PREffectResult(
            action_id=action_id,
            rumor_id=rumor_id,
            character_id=character_id,
            strategy=name,
            base_score=base_score,
            multiplier=str(multiplier),
            effective_score=effective_score,
        )

    return effect_writer


def get_pr_score(
    guild_id: str | int,
    rumor_id: int,
    character_id: int,
) -> int:
    """取得指定角色在案件中的累積公關行動分數。"""

    gid = str(guild_id).strip()

    if (
        not gid
        or type(rumor_id) is not int
        or rumor_id <= 0
        or type(character_id) is not int
        or character_id <= 0
    ):
        raise ValueError("查詢參數無效")

    with get_connection() as db:
        table_exists = db.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table'
              AND name='rp_rumor_pr_effects'
            """
        ).fetchone()

        if not table_exists:
            return 0

        row = db.execute(
            """
            SELECT COALESCE(SUM(effective_score), 0)
            FROM rp_rumor_pr_effects
            WHERE guild_id=?
              AND rumor_id=?
              AND character_id=?
            """,
            (gid, rumor_id, character_id),
        ).fetchone()

    return int(row[0])


def list_pr_effects(
    guild_id: str | int,
    rumor_id: int,
    character_id: int,
    *,
    limit: int = 50,
) -> list[dict]:
    """取得公關行動明細。上游應先檢查查看權限。"""

    gid = str(guild_id).strip()
    if not gid:
        raise ValueError("伺服器編號不可為空")
    if type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("案件編號無效")
    if type(character_id) is not int or character_id <= 0:
        raise ValueError("角色編號無效")

    limit = max(1, min(int(limit), 100))

    with get_connection() as db:
        exists = db.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table'
              AND name='rp_rumor_pr_effects'
            """
        ).fetchone()

        if not exists:
            return []

        rows = db.execute(
            """
            SELECT action_id, strategy, base_score,
                   multiplier, effective_score, created_at
            FROM rp_rumor_pr_effects
            WHERE guild_id=?
              AND rumor_id=?
              AND character_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (gid, rumor_id, character_id, limit),
        ).fetchall()

    return [dict(row) for row in rows]
