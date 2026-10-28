
"""Discord RP｜成就稱號自動結算排程。

檔案：
    modules/achievement_scheduler.py

依賴：
    core.database
    modules.achievement_core
    modules.achievement_effects

功能：
1. 機器人啟動後掃描待結算稱號。
2. 每 60 秒檢查一次。
3. 首次穿戴後每滿 24 小時結算。
4. 每個稱號最多 7 輪。
5. 卸下不暫停、不重置計時。
6. 原定結算時間沒有穿戴，永久錯過。
7. 同類每日加成由效果模組取最高值。
8. 利用資料庫紀錄防止重複發獎。
9. 失敗時記錄錯誤，下一輪可重試。
10. 不阻塞 Discord 機器人的事件循環。

重要：
此模組只負責自動呼叫效果結算。

粉絲、大眾認知度等尚未完成適配的獎勵，
會繼續保留在 pending 狀態。

商城折扣不透過此排程發放，
而是在購買結帳時即時計算。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading

from datetime import datetime, timezone

from discord.ext import commands, tasks

from core.database import (
    get_connection,
    transaction,
)

from modules.achievement_effects import (
    init_db as init_effects,
    settle_daily_effects,
)


LOG = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60
GUILD_BATCH_SIZE = 100

# 避免同一 Python 程序內，
# 排程和手動呼叫同時重複執行。
PROCESS_LOCK = threading.Lock()


# ==========================================
# 時間工具
# ==========================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_text():
    return utc_now().isoformat(
        timespec="seconds"
    )


# ==========================================
# 排程執行紀錄
# ==========================================

def init_db():
    init_effects()

    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_achievement_scheduler_runs (
                id INTEGER PRIMARY KEY
                    AUTOINCREMENT,

                started_at TEXT NOT NULL,

                finished_at TEXT,

                state TEXT NOT NULL
                    CHECK(state IN (
                        'running',
                        'done',
                        'partial',
                        'failed'
                    )),

                guild_count INTEGER NOT NULL
                    DEFAULT 0,

                cycle_count INTEGER NOT NULL
                    DEFAULT 0,

                queued_count INTEGER NOT NULL
                    DEFAULT 0,

                missed_count INTEGER NOT NULL
                    DEFAULT 0,

                grant_count INTEGER NOT NULL
                    DEFAULT 0,

                pending_count INTEGER NOT NULL
                    DEFAULT 0,

                error_count INTEGER NOT NULL
                    DEFAULT 0,

                error_message TEXT
            )
        """)


# ==========================================
# 取得需要結算的伺服器
# ==========================================

def get_target_guild_ids():
    """只掃描有稱號紀錄的伺服器。

    不需要為所有 Discord 伺服器
    建立空白的稱號資料。
    """

    with get_connection() as db:
        rows = db.execute(
            """
            SELECT guild_id
            FROM rp_title_equipment

            UNION

            SELECT guild_id
            FROM rp_title_daily_claims

            ORDER BY guild_id
            """
        ).fetchall()

    return [
        str(row["guild_id"])
        for row in rows
    ]


# ==========================================
# 寫入執行紀錄
# ==========================================

def start_run():
    with transaction() as db:
        cursor = db.execute(
            """
            INSERT INTO
            rp_achievement_scheduler_runs (
                started_at,
                state
            )
            VALUES (?, 'running')
            """,
            (utc_text(),),
        )

        return int(cursor.lastrowid)


def finish_run(
    run_id,
    summary,
    *,
    state="done",
    error_message=None,
):
    with transaction() as db:
        db.execute(
            """
            UPDATE rp_achievement_scheduler_runs

            SET
                finished_at=?,
                state=?,
                guild_count=?,
                cycle_count=?,
                queued_count=?,
                missed_count=?,
                grant_count=?,
                pending_count=?,
                error_count=?,
                error_message=?

            WHERE id=?
            """,
            (
                utc_text(),
                state,
                summary["guild_count"],
                summary["cycle_count"],
                summary["queued_count"],
                summary["missed_count"],
                summary["grant_count"],
                summary["pending_count"],
                summary["error_count"],
                (
                    str(error_message)[:1000]
                    if error_message
                    else None
                ),
                int(run_id),
            ),
        )


# ==========================================
# 單個伺服器結算
# ==========================================

def settle_one_guild(guild_id):
    """呼叫現有每日效果模組。

    效果模組本身會記錄：
    - 每個稱號的輪次
    - 符合資格或錯過
    - 已發放及待發放紀錄

    排程不自行重寫發獎規則。
    """

    result = settle_daily_effects(
        guild_id,
        now=utc_now(),
    )

    cycles = result.get(
        "cycles",
        {},
    )

    rewards = result.get(
        "rewards",
        {},
    )

    return {
        "cycles": int(
            cycles.get("processed", 0)
        ),
        "queued": int(
            cycles.get("queued", 0)
        ),
        "missed": int(
            cycles.get("missed", 0)
        ),
        "granted": len(
            rewards.get("done", [])
        ),
        "pending": len(
            rewards.get("pending", [])
        ),
        "errors": len(
            rewards.get("errors", [])
        ),
    }


# ==========================================
# 完整排程
# ==========================================

def run_achievement_settlement():
    """可供背景排程或官方維護工具使用。

    如果同一程序已經有排程在執行，
    會直接回傳 skipped。

    注意：
    threading.Lock 只保護單一程序。
    如果部署多個 Railway 副本，
    必須額外使用資料庫層級的排程鎖。
    """

    if not PROCESS_LOCK.acquire(
        blocking=False
    ):
        return {
            "status": "skipped",
            "reason": "已有結算程序正在執行",
        }

    try:
        return _run_settlement_locked()
    finally:
        PROCESS_LOCK.release()


def _run_settlement_locked():
    init_db()

    summary = {
        "guild_count": 0,
        "cycle_count": 0,
        "queued_count": 0,
        "missed_count": 0,
        "grant_count": 0,
        "pending_count": 0,
        "error_count": 0,
    }

    run_id = start_run()

    failures = []

    try:
        guild_ids = get_target_guild_ids()

        for guild_id in guild_ids:
            try:
                result = settle_one_guild(
                    guild_id
                )

                summary["guild_count"] += 1

                summary["cycle_count"] += (
                    result["cycles"]
                )

                summary["queued_count"] += (
                    result["queued"]
                )

                summary["missed_count"] += (
                    result["missed"]
                )

                summary["grant_count"] += (
                    result["granted"]
                )

                summary["pending_count"] += (
                    result["pending"]
                )

                summary["error_count"] += (
                    result["errors"]
                )

                if result["errors"]:
                    failures.append(
                        f"伺服器 {guild_id}："
                        f"{result['errors']} 項發獎失敗"
                    )

            except Exception as exc:
                summary["error_count"] += 1

                failures.append(
                    f"伺服器 {guild_id}：{exc}"
                )

                LOG.exception(
                    "稱號每日結算失敗：guild=%s",
                    guild_id,
                )

        state = (
            "partial"
            if failures
            else "done"
        )

        finish_run(
            run_id,
            summary,
            state=state,
            error_message=(
                "\n".join(failures)
                if failures
                else None
            ),
        )

        LOG.info(
            "稱號結算完成："
            "伺服器=%s 輪次=%s "
            "符合=%s 錯過=%s "
            "入帳=%s 待接入=%s 錯誤=%s",
            summary["guild_count"],
            summary["cycle_count"],
            summary["queued_count"],
            summary["missed_count"],
            summary["grant_count"],
            summary["pending_count"],
            summary["error_count"],
        )

        return {
            "status": state,
            "run_id": run_id,
            **summary,
        }

    except Exception as exc:
        LOG.exception(
            "稱號排程發生重大錯誤"
        )

        summary["error_count"] += 1

        try:
            finish_run(
                run_id,
                summary,
                state="failed",
                error_message=str(exc),
            )
        except sqlite3.Error:
            LOG.exception(
                "稱號排程失敗紀錄寫入失敗"
            )

        return {
            "status": "failed",
            "run_id": run_id,
            "error": str(exc),
            **summary,
        }


# ==========================================
# 查詢最近執行紀錄
# ==========================================

def recent_scheduler_runs(limit=10):
    init_db()

    limit = max(
        1,
        min(int(limit), 50),
    )

    with get_connection() as db:
        rows = db.execute(
            """
            SELECT *
            FROM rp_achievement_scheduler_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ==========================================
# 清理過舊的排程紀錄
# ==========================================

def prune_scheduler_logs(keep=500):
    """只清理排程執行日誌。

    不會刪除稱號獲得紀錄、
    穿戴歷史或每日發獎紀錄。
    """

    keep = max(
        100,
        int(keep),
    )

    with transaction() as db:
        db.execute(
            """
            DELETE FROM rp_achievement_scheduler_runs
            WHERE id NOT IN (
                SELECT id
                FROM rp_achievement_scheduler_runs
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (keep,),
        )


# ==========================================
# Discord 自動排程 Cog
# ==========================================

class AchievementScheduler(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        init_db()

        # 啟動後會在機器人 ready 時
        # 執行第一次補查。
        if not self.settlement_loop.is_running():
            self.settlement_loop.start()

    async def cog_unload(self):
        if self.settlement_loop.is_running():
            self.settlement_loop.cancel()

    @tasks.loop(
        seconds=CHECK_INTERVAL_SECONDS,
        reconnect=True,
    )
    async def settlement_loop(self):
        """在工作執行緒呼叫 SQLite 結算。

        避免同步資料庫操作阻塞
        Discord WebSocket 心跳。
        """

        result = await asyncio.to_thread(
            run_achievement_settlement
        )

        if result["status"] == "failed":
            LOG.error(
                "稱號排程執行失敗：%s",
                result.get("error"),
            )

        elif result["status"] == "partial":
            LOG.warning(
                "稱號排程部分完成，"
                "下次將重試失敗項目。"
            )

    @settlement_loop.before_loop
    async def before_settlement(self):
        await self.bot.wait_until_ready()

    @settlement_loop.error
    async def settlement_error(self, error):
        LOG.exception(
            "稱號自動結算循環錯誤：%s",
            error,
        )


# ==========================================
# 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        AchievementScheduler(bot)
    )

    LOG.info(
        "稱號每日效果自動排程已載入。"
    )
