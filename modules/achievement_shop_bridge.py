
"""Discord RP｜商城消費與稱號自動解鎖橋接模組。

功能：
- 購買成功後，以實付金額記錄稱號進度
- 訂單防重複
- 付款交易與待處理事件同步提交
- 定時處理尚未完成的稱號事件
- 達成門檻後自動解鎖稱號

依賴：
- core.database
- modules.achievement_triggers

重要：
本模組不直接扣款。
扣款仍由原本 modules/shop.py 處理。
"""

from __future__ import annotations

import logging
import sqlite3

import discord
from discord.ext import commands, tasks

from core.database import get_connection, transaction


LOG = logging.getLogger(__name__)


# ==========================================
# 待處理購物事件資料表
# ==========================================

def init_db():
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_achievement_shop_outbox (
                guild_id TEXT NOT NULL,
                order_key TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                amount INTEGER NOT NULL
                    CHECK(amount >= 0),
                status TEXT NOT NULL
                    DEFAULT 'pending'
                    CHECK(status IN (
                        'pending',
                        'done'
                    )),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                processed_at TEXT,

                PRIMARY KEY (
                    guild_id,
                    order_key
                )
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_rp_achievement_shop_outbox_pending
            ON rp_achievement_shop_outbox (
                status,
                created_at
            )
        """)


# ==========================================
# 同筆購物交易內寫入事件
# ==========================================

def record_shop_purchase_in_transaction(
    conn,
    guild_id,
    character_id,
    order_key,
    actual_paid,
):
    """由 shop.buy() 在原本交易中呼叫。

    conn 必須是商城目前使用中的 SQLite 連線。
    不可另開 transaction()。

    因此：
    - 購買成功，事件一起提交。
    - 購買失敗，事件一起回滾。
    """

    amount = int(actual_paid)

    if amount < 0:
        raise ValueError(
            "稱號累積消費不可為負數。"
        )

    key = str(order_key).strip()

    if not key:
        raise ValueError(
            "購物訂單編號不可空白。"
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS
        rp_achievement_shop_outbox (
            guild_id TEXT NOT NULL,
            order_key TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            amount INTEGER NOT NULL
                CHECK(amount >= 0),
            status TEXT NOT NULL
                DEFAULT 'pending'
                CHECK(status IN ('pending', 'done')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,
            processed_at TEXT,

            PRIMARY KEY(guild_id, order_key)
        )
        """
    )

    existing = conn.execute(
        """
        SELECT character_id, amount
        FROM rp_achievement_shop_outbox
        WHERE guild_id=? AND order_key=?
        """,
        (
            str(guild_id),
            key,
        ),
    ).fetchone()

    if existing is not None:
        if (
            int(existing["character_id"])
            != int(character_id)
            or int(existing["amount"]) != amount
        ):
            raise ValueError(
                "訂單編號已存在，但角色或付款金額不同。"
            )

        return False

    conn.execute(
        """
        INSERT INTO rp_achievement_shop_outbox (
            guild_id,
            order_key,
            character_id,
            amount
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            str(guild_id),
            key,
            int(character_id),
            amount,
        ),
    )

    return True


# ==========================================
# 單筆事件處理
# ==========================================

def process_one_purchase(
    guild_id,
    order_key,
):
    """記錄消費事件並重新檢查角色稱號。

    事件 ID 使用原始訂單編號，確保相同
    購物訂單無法被重複累積。
    """

    from modules.achievement_triggers import (
        record_event,
        check_and_unlock,
    )

    gid = str(guild_id)
    key = str(order_key)

    with get_connection() as db:
        row = db.execute(
            """
            SELECT *
            FROM rp_achievement_shop_outbox
            WHERE guild_id=? AND order_key=?
            """,
            (gid, key),
        ).fetchone()

    if row is None:
        raise ValueError("找不到購買事件。")

    if row["status"] == "done":
        return False

    cid = int(row["character_id"])
    paid = int(row["amount"])

    # 免費商品不增加累計消費。
    if paid > 0:
        record_event(
            gid,
            cid,
            "shop_purchase",
            key,
            amount=paid,
        )

        # 即使事件已存在，也重新檢查稱號。
        # 可用於恢復付款後中斷的解鎖流程。
        check_and_unlock(gid, cid)

    with transaction() as db:
        db.execute(
            """
            UPDATE rp_achievement_shop_outbox
            SET status='done',
                last_error=NULL,
                processed_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND order_key=?
              AND status='pending'
            """,
            (gid, key),
        )

    return True


# ==========================================
# 批次處理待完成事件
# ==========================================

def process_pending_purchases(limit=100):
    init_db()

    with get_connection() as db:
        rows = db.execute(
            """
            SELECT guild_id, order_key
            FROM rp_achievement_shop_outbox
            WHERE status='pending'
            ORDER BY created_at, rowid
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    result = {
        "processed": 0,
        "failed": 0,
    }

    for row in rows:
        gid = row["guild_id"]
        key = row["order_key"]

        try:
            process_one_purchase(gid, key)
            result["processed"] += 1

        except Exception as exc:
            LOG.exception(
                "商城稱號進度處理失敗："
                "guild=%s order=%s",
                gid,
                key,
            )

            result["failed"] += 1

            try:
                with transaction() as db:
                    db.execute(
                        """
                        UPDATE rp_achievement_shop_outbox
                        SET attempts=attempts+1,
                            last_error=?
                        WHERE guild_id=?
                          AND order_key=?
                          AND status='pending'
                        """,
                        (
                            str(exc)[:500],
                            gid,
                            key,
                        ),
                    )
            except sqlite3.Error:
                LOG.exception(
                    "無法記錄商城稱號事件錯誤"
                )

    return result


# ==========================================
# 自動處理循環
# ==========================================

class AchievementShopBridge(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        init_db()
        self.process_loop.start()

    async def cog_unload(self):
        self.process_loop.cancel()

    @tasks.loop(seconds=60)
    async def process_loop(self):
        # SQLite 與稱號獎勵均為同步操作。
        # 放到執行緒避免阻塞 Discord 心跳。
        result = await __import__(
            "asyncio"
        ).to_thread(
            process_pending_purchases,
            100,
        )

        if result["processed"] or result["failed"]:
            LOG.info(
                "商城稱號事件：成功=%s 失敗=%s",
                result["processed"],
                result["failed"],
            )

    @process_loop.before_loop
    async def before_process(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(
        AchievementShopBridge(bot)
    )
