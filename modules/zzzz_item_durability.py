
"""Discord RP｜商品使用次數管理。

B02 美妝保養：每件 2～7 次。
B04 數位科技：每件 3～6 次。

每件商品獨立記錄剩餘次數。
剩餘次數歸零後，刪除該商品實例。

本模組不註冊 /商城、/查看。
本階段僅建立資料層，不直接扣背包或發放屬性。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from discord.ext import commands

from core.database import transaction

LOG = logging.getLogger(__name__)

CATEGORY_LIMITS = {
    "B02": (2, 7),
    "B04": (3, 6),
}


def init_durability_tables():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS item_use_settings (
                guild_id TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                category_code TEXT NOT NULL,
                max_uses INTEGER NOT NULL
                    CHECK(max_uses >= 1),
                PRIMARY KEY (guild_id, product_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS item_instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                remaining_uses INTEGER NOT NULL
                    CHECK(remaining_uses > 0),
                max_uses INTEGER NOT NULL
                    CHECK(max_uses > 0)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_item_instances
            ON item_instances(
                guild_id,
                character_id,
                product_id,
                remaining_uses
            )
        """)


def configure_item(
    guild_id: str,
    product_id: int,
    category_code: str,
    max_uses: int,
):
    """設定商品每件可使用的次數。"""

    guild_id = str(guild_id)
    category_code = category_code.upper()

    if category_code not in CATEGORY_LIMITS:
        raise ValueError(
            "只有 B02、B04 支援此使用次數設定。"
        )

    low, high = CATEGORY_LIMITS[category_code]

    if not low <= max_uses <= high:
        raise ValueError(
            f"{category_code} 商品使用次數"
            f"必須介於 {low}～{high} 次。"
        )

    if product_id <= 0:
        raise ValueError("商品編號不正確。")

    with transaction() as conn:
        product = conn.execute(
            """
            SELECT id, category, effect_kind
            FROM shop_products
            WHERE guild_id=? AND id=?
            """,
            (guild_id, product_id),
        ).fetchone()

        if product is None:
            raise ValueError("找不到商品。")

        category = str(product["category"]).upper()

        if category_code not in category:
            raise ValueError(
                "商品所屬分類與設定的商店不一致。"
            )

        if product["effect_kind"] != "instant":
            raise ValueError(
                "只能為消耗型效果商品設定使用次數。"
            )

        existing = conn.execute(
            """
            SELECT 1 FROM item_instances
            WHERE guild_id=? AND product_id=?
            LIMIT 1
            """,
            (guild_id, product_id),
        ).fetchone()

        if existing:
            raise ValueError(
                "此商品已有獨立使用紀錄，"
                "不能直接更改使用次數。"
            )

        conn.execute(
            """
            INSERT INTO item_use_settings (
                guild_id, product_id,
                category_code, max_uses
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, product_id)
            DO UPDATE SET
                category_code=excluded.category_code,
                max_uses=excluded.max_uses
            """,
            (
                guild_id,
                product_id,
                category_code,
                max_uses,
            ),
        )


def get_max_uses(conn, guild_id, product_id):
    row = conn.execute(
        """
        SELECT max_uses
        FROM item_use_settings
        WHERE guild_id=? AND product_id=?
        """,
        (str(guild_id), product_id),
    ).fetchone()

    return int(row["max_uses"]) if row else None


def create_item_instances(
    conn,
    guild_id,
    character_id,
    product_id,
    quantity,
):
    """為新取得的商品建立獨立使用紀錄。

    必須由購買或轉移流程在同一筆交易中呼叫。
    """

    if not 1 <= quantity <= 10000:
        raise ValueError("商品數量不合法。")

    max_uses = get_max_uses(
        conn, guild_id, product_id
    )

    if max_uses is None:
        return 0

    conn.executemany(
        """
        INSERT INTO item_instances (
            guild_id,
            character_id,
            product_id,
            remaining_uses,
            max_uses
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                str(guild_id),
                character_id,
                product_id,
                max_uses,
                max_uses,
            )
            for _ in range(quantity)
        ],
    )

    return quantity


def get_item_instances(
    conn,
    guild_id,
    character_id,
    product_id,
):
    return conn.execute(
        """
        SELECT id, remaining_uses, max_uses
        FROM item_instances
        WHERE guild_id=?
          AND character_id=?
          AND product_id=?
        ORDER BY remaining_uses ASC, id ASC
        """,
        (
            str(guild_id),
            character_id,
            product_id,
        ),
    ).fetchall()


def consume_instance(
    conn,
    guild_id,
    character_id,
    product_id,
):
    """消耗一次使用次數。

    必須在呼叫端的資料庫交易內執行。
    此函式只修改實例，背包扣件與屬性發放
    必須在同一交易中由商城核心完成。
    """

    row = conn.execute(
        """
        SELECT id, remaining_uses
        FROM item_instances
        WHERE guild_id=?
          AND character_id=?
          AND product_id=?
        ORDER BY remaining_uses ASC, id ASC
        LIMIT 1
        """,
        (
            str(guild_id),
            character_id,
            product_id,
        ),
    ).fetchone()

    if row is None:
        raise ValueError(
            "沒有可使用的商品實例。"
        )

    instance_id = row["id"]
    remaining = int(row["remaining_uses"]) - 1

    if remaining == 0:
        conn.execute(
            """
            DELETE FROM item_instances
            WHERE id=?
            """,
            (instance_id,),
        )

        return {
            "remaining": 0,
            "exhausted": True,
        }

    conn.execute(
        """
        UPDATE item_instances
        SET remaining_uses=?
        WHERE id=?
        """,
        (remaining, instance_id),
    )

    return {
        "remaining": remaining,
        "exhausted": False,
    }


async def setup(bot: commands.Bot):
    await asyncio.to_thread(
        init_durability_tables
    )

    LOG.info(
        "商品使用次數資料層已載入："
        "B02 2～7 次、B04 3～6 次。"
    )
