
"""Discord RP｜稱號每日追蹤數值結算。

檔案位置：
modules/achievement_daily_metrics.py

功能：
- 自動處理每日粉絲加成。
- 自動處理每日大眾認知度加成。
- 使用 core.stat_rewards 統一發獎。
- 防止機器人重新啟動後重複發放。
- 保留原本 168 小時的穿戴時間規則。
- 不修改既有商城折扣邏輯。

注意：
每日加成是否符合穿戴條件，
仍由 achievement_effects.py 判斷。

本模組只處理已建立的 pending 獎勵。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from threading import Lock

from discord.ext import commands, tasks

from core.database import (
    get_connection,
    transaction,
)

from core.stat_rewards import (
    init_db as init_stat_rewards,
    grant_stats,
)


LOG = logging.getLogger(__name__)

SETTLEMENT_LOCK = Lock()

CHECK_INTERVAL_MINUTES = 2

METRIC_NAMES = {
    "fans": "粉絲",
    "recognition": "大眾認知度",
}

MAX_BATCH_SIZE = 100


# ==========================================
# 資料表檢查
# ==========================================

def table_exists(db, name):
    row = db.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table'
          AND name=?
        """,
        (name,),
    ).fetchone()

    return row is not None


def validate_claim_schema(db):
    """確認原稱號每日獎勵資料表可用。"""

    if not table_exists(
        db,
        "rp_title_daily_claims",
    ):
        return False

    columns = {
        row["name"]
        for row in db.execute(
            """
            PRAGMA table_info(
                rp_title_daily_claims
            )
            """
        ).fetchall()
    }

    required = {
        "guild_id",
        "character_id",
        "cycle_key",
        "stat_key",
        "granted_amount",
        "state",
    }

    if not required.issubset(columns):
        missing = required - columns

        raise RuntimeError(
            "稱號每日獎勵資料表缺少欄位："
            + ", ".join(sorted(missing))
        )

    return True


# ==========================================
# 讀取待發獎勵
# ==========================================

def list_pending_metrics(
    limit=MAX_BATCH_SIZE,
):
    with get_connection() as db:
        if not validate_claim_schema(db):
            return []

        rows = db.execute(
            """
            SELECT
                guild_id,
                character_id,
                cycle_key,
                stat_key,
                granted_amount

            FROM rp_title_daily_claims

            WHERE state='pending'
              AND stat_key IN (
                  'fans',
                  'recognition'
              )

            ORDER BY
                guild_id,
                character_id,
                cycle_key,
                stat_key

            LIMIT ?
            """,
            (
                max(
                    1,
                    min(
                        int(limit),
                        MAX_BATCH_SIZE,
                    ),
                ),
            ),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ==========================================
# 確認獎勵尚未完成
# ==========================================

def read_current_claim(
    guild_id,
    character_id,
    cycle_key,
    stat_key,
):
    with get_connection() as db:
        row = db.execute(
            """
            SELECT
                granted_amount,
                state

            FROM rp_title_daily_claims

            WHERE guild_id=?
              AND character_id=?
              AND cycle_key=?
              AND stat_key=?
            """,
            (
                str(guild_id),
                int(character_id),
                str(cycle_key),
                str(stat_key),
            ),
        ).fetchone()

    return (
        dict(row)
        if row is not None
        else None
    )


# ==========================================
# 標記獎勵完成
# ==========================================

def mark_claim_done(
    guild_id,
    character_id,
    cycle_key,
    stat_key,
    expected_amount,
):
    """只標記數額未變動的待發獎勵。"""

    with transaction() as db:
        result = db.execute(
            """
            UPDATE rp_title_daily_claims

            SET state='done'

            WHERE guild_id=?
              AND character_id=?
              AND cycle_key=?
              AND stat_key=?
              AND granted_amount=?
              AND state='pending'
            """,
            (
                str(guild_id),
                int(character_id),
                str(cycle_key),
                str(stat_key),
                int(expected_amount),
            ),
        )

        return result.rowcount == 1


# ==========================================
# 單筆結算
# ==========================================

def settle_one_metric(claim):
    guild_id = str(
        claim["guild_id"]
    )

    character_id = int(
        claim["character_id"]
    )

    cycle_key = str(
        claim["cycle_key"]
    )

    stat_key = str(
        claim["stat_key"]
    )

    amount = int(
        claim["granted_amount"]
    )

    if stat_key not in METRIC_NAMES:
        return "skipped"

    # 不處理影響指數，也不接受負數。
    if amount < 0:
        raise ValueError(
            "稱號每日加成不可為負數。"
        )

    current = read_current_claim(
        guild_id,
        character_id,
        cycle_key,
        stat_key,
    )

    if current is None:
        return "skipped"

    if current["state"] != "pending":
        return "already_done"

    if int(current["granted_amount"]) != amount:
        return "changed"

    if amount == 0:
        if mark_claim_done(
            guild_id,
            character_id,
            cycle_key,
            stat_key,
            amount,
        ):
            return "done"

        return "changed"

    metric_name = METRIC_NAMES[
        stat_key
    ]

    # 同一角色、日期及數值，
    # 使用固定的發獎事件編號。
    #
    # 如果入帳後機器人重新啟動，
    # grant_stats 會辨識同一事件，
    # 避免再次增加數值。
    source_id = (
        f"daily:"
        f"{guild_id}:"
        f"{character_id}:"
        f"{cycle_key}:"
        f"{stat_key}"
    )

    result = grant_stats(
        guild_id,
        character_id,
        {
            metric_name: amount,
        },
        source="system_rule",
        source_id=source_id,
        reason=(
            f"稱號每日加成："
            f"{cycle_key} "
            f"{metric_name}+{amount}"
        ),
        random=False,
        purchased=False,
    )

    if (
        not result["applied"]
        and not result["duplicate"]
    ):
        return "pending"

    if mark_claim_done(
        guild_id,
        character_id,
        cycle_key,
        stat_key,
        amount,
    ):
        return "done"

    # 數值已經入帳，但結算紀錄可能
    # 在同時更新，保留給下次檢查。
    return "changed"


# ==========================================
# 批次結算
# ==========================================

def settle_pending_metrics():
    """結算本次掃描到的待發項目。"""

    if not SETTLEMENT_LOCK.acquire(
        blocking=False
    ):
        return {
            "busy": True,
            "checked": 0,
            "done": 0,
            "failed": 0,
        }

    try:
        init_stat_rewards()

        claims = list_pending_metrics()

        summary = {
            "busy": False,
            "checked": len(claims),
            "done": 0,
            "already_done": 0,
            "changed": 0,
            "skipped": 0,
            "failed": 0,
        }

        for claim in claims:
            try:
                result = settle_one_metric(
                    claim
                )

                if result in summary:
                    summary[result] += 1

            except Exception:
                summary["failed"] += 1

                LOG.exception(
                    "稱號每日數值結算失敗："
                    "guild=%s character=%s "
                    "cycle=%s stat=%s",
                    claim["guild_id"],
                    claim["character_id"],
                    claim["cycle_key"],
                    claim["stat_key"],
                )

        return summary

    finally:
        SETTLEMENT_LOCK.release()


# ==========================================
# Discord 背景工作
# ==========================================

class AchievementDailyMetrics(
    commands.Cog
):
    """每日粉絲與認知度獎勵結算。"""

    def __init__(self, bot):
        self.bot = bot

        self.settlement_loop.start()

    def cog_unload(self):
        self.settlement_loop.cancel()

    @tasks.loop(
        minutes=CHECK_INTERVAL_MINUTES
    )
    async def settlement_loop(self):
        try:
            result = await asyncio.to_thread(
                settle_pending_metrics
            )

            if (
                result.get("done", 0)
                or result.get("failed", 0)
                or result.get("changed", 0)
            ):
                LOG.info(
                    "稱號每日追蹤數值：%s",
                    result,
                )

        except Exception:
            LOG.exception(
                "稱號每日追蹤數值"
                "背景結算發生錯誤。"
            )

    @settlement_loop.before_loop
    async def before_settlement(self):
        await self.bot.wait_until_ready()


# ==========================================
# 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        AchievementDailyMetrics(bot)
    )
