
"""傳聞｜可重試的角色數值結算協調器（開發版）。

安裝：modules/rumor_settlement.py
純工具模組，勿作為 discord.py extension 自動載入。

只允許由可信任的公關結算器呼叫。
不可向玩家公開 grade/changes 參數。

本版僅處理主要當事人的既有角色數值：
- 不發放金幣
- 不決定公關評級
- 不自行更改傳聞案件狀態
- 不處理尚未實作的特殊粉絲數值

既有 grant_stats() 會自行開啟 SQLite 交易。
因此本模組採用持久化工作紀錄及來源去重，
讓中斷後能安全重試，而非宣稱跨函式單筆交易。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from core.database import get_connection, transaction
from modules.rumor_rewards import (
    preview_settlement,
    apply_settlement,
)


PHASE = "final"


def initialize() -> None:
    """建立傳聞獎懲工作紀錄表。"""

    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_rumor_reward_jobs (
                guild_id TEXT NOT NULL,
                rumor_id INTEGER NOT NULL,
                phase TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                grade TEXT NOT NULL,
                changes_json TEXT NOT NULL,

                state TEXT NOT NULL DEFAULT 'prepared'
                    CHECK(state IN ('prepared','applied')),

                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,

                PRIMARY KEY(
                    guild_id,
                    rumor_id,
                    phase,
                    character_id
                )
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_rumor_reward_jobs_pending
            ON rp_rumor_reward_jobs(
                state,
                updated_at
            )
        """)


@dataclass(frozen=True)
class RewardJobResult:
    rumor_id: int
    character_id: int
    grade: str
    state: str
    duplicate: bool


def _canonical(changes: dict[str, int]) -> str:
    """將數值變動轉成固定格式。"""

    return json.dumps(
        changes,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def settle_character_reward(
    guild_id: str | int,
    rumor_id: int,
    character_id: int,
    grade: str,
    changes: dict[str, int],
    *,
    trusted: bool = False,
) -> RewardJobResult:
    """執行角色的正式傳聞數值獎懲。

    必須由公關核心先確認案件評級與獎懲內容。

    第一次執行：
        1. 儲存不可變更的結算工作
        2. 發放或扣除角色數值
        3. 標記工作完成

    如果中途當機：
        下次可用同一份內容重新執行。

    core.stat_rewards 會以固定 source_id
    避免重複加減數值。
    """

    if not trusted:
        raise PermissionError(
            "只有可信任的系統結算器可以呼叫。"
        )

    initialize()

    preview = preview_settlement(
        guild_id,
        rumor_id,
        character_id,
        grade,
        changes,
        phase=PHASE,
    )

    gid = preview.guild_id
    rid = preview.rumor_id
    cid = preview.character_id

    payload = _canonical(preview.changes)
    now = int(time.time())

    # 第一階段：建立固定結算工作。
    with transaction() as db:

        rumor = db.execute(
            """
            SELECT status
            FROM rp_company_rumors
            WHERE guild_id=?
              AND id=?
            """,
            (gid, rid),
        ).fetchone()

        if (
            rumor is None
            or rumor["status"] not in (
                "active",
                "settled",
            )
        ):
            raise ValueError(
                "此案件目前不能進行獎懲結算。"
            )

        db.execute(
            """
            INSERT OR IGNORE INTO rp_rumor_reward_jobs (
                guild_id,
                rumor_id,
                phase,
                character_id,
                grade,
                changes_json,
                created_at,
                updated_at
            )
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                gid,
                rid,
                PHASE,
                cid,
                preview.grade,
                payload,
                now,
                now,
            ),
        )

        job = db.execute(
            """
            SELECT *
            FROM rp_rumor_reward_jobs
            WHERE guild_id=?
              AND rumor_id=?
              AND phase=?
              AND character_id=?
            """,
            (
                gid,
                rid,
                PHASE,
                cid,
            ),
        ).fetchone()

        # 同一結算工作禁止改變原定評級或獎懲。
        if (
            job["grade"] != preview.grade
            or job["changes_json"] != payload
        ):
            raise ValueError(
                "結算工作已存在，"
                "不能更改評級或獎懲內容。"
            )

        # 已經完成的案件，不再重複發放。
        if job["state"] == "applied":
            return RewardJobResult(
                rid,
                cid,
                preview.grade,
                "applied",
                True,
            )

    # 第二階段：正式發放或扣除角色數值。
    #
    # grant_stats 本身具有持久化去重功能。
    # 如果發放完成後程式當機，下一次重試
    # 會辨識為 duplicate，不會再次增減數值。

    result = apply_settlement(
        preview,
        authorized=True,
    )

    # 第三階段：寫入已完成狀態。
    with transaction() as db:

        db.execute(
            """
            UPDATE rp_rumor_reward_jobs
            SET state='applied',
                updated_at=?
            WHERE guild_id=?
              AND rumor_id=?
              AND phase=?
              AND character_id=?
              AND grade=?
              AND changes_json=?
            """,
            (
                int(time.time()),
                gid,
                rid,
                PHASE,
                cid,
                preview.grade,
                payload,
            ),
        )

    return RewardJobResult(
        rid,
        cid,
        preview.grade,
        "applied",
        bool(result.get("duplicate", False)),
    )


def list_unfinished(
    limit: int = 100,
) -> list[dict]:
    """列出尚未完成的獎懲工作。

    供可信任的結算排程檢查。
    不會自行推定案件評級或獎勵。
    """

    initialize()

    limit = max(
        1,
        min(int(limit), 500),
    )

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT
                guild_id,
                rumor_id,
                character_id,
                grade,
                changes_json,
                created_at
            FROM rp_rumor_reward_jobs
            WHERE state='prepared'
            ORDER BY
                updated_at,
                rumor_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]
