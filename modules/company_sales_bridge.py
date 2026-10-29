
"""個人店鋪與品牌營業額連動。

將已完成的 personal_shop_sales 交易同步至品牌。
不負責收款、退款或商品交付，避免重複金流。
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlite3

from core.database import (
    transaction,
    get_connection,
)

from modules.company_business import (
    initialize as initialize_business,
)


def utc_now():
    return datetime.now(timezone.utc)


def parse_timestamp(value):
    """將既有資料庫時間轉為 UTC datetime。"""

    text = str(value).strip()

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    parsed = datetime.fromisoformat(text)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def initialize():

    initialize_business()

    with transaction() as db:

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS rp_brand_shop_links (
                guild_id TEXT NOT NULL,
                shop_id INTEGER NOT NULL,
                brand_id TEXT NOT NULL,
                linked_by TEXT NOT NULL,
                linked_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                PRIMARY KEY(guild_id, shop_id)
            )
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_brand_shop_link
            ON rp_brand_shop_links(
                guild_id,
                brand_id,
                status
            )
            """
        )


# ============================================================
# 品牌與店鋪驗證
# ============================================================

def verify_shop_brand(
    db,
    guild_id,
    shop_id,
    brand_id,
    owner_user_id,
):

    guild_id = str(guild_id)
    owner_user_id = str(owner_user_id)

    brand = db.execute(
        """
        SELECT
            business_id,
            owner_id,
            name
        FROM rp_business_entities
        WHERE guild_id = ?
          AND business_id = ?
          AND kind = 'brand'
          AND status = 'active'
        """,
        (
            guild_id,
            brand_id,
        ),
    ).fetchone()

    if not brand:
        raise ValueError(
            "找不到已核准成立的品牌。"
        )

    if str(brand["owner_id"]) != owner_user_id:
        raise PermissionError(
            "只有品牌持有人可以綁定店鋪。"
        )

    shop = db.execute(
        """
        SELECT
            s.id,
            s.character_id,
            s.name,
            s.status,
            c.owner_id
        FROM personal_shops s
        JOIN characters c
            ON c.guild_id = s.guild_id
            AND c.id = s.character_id
        WHERE s.guild_id = ?
          AND s.id = ?
        """,
        (
            guild_id,
            int(shop_id),
        ),
    ).fetchone()

    if not shop:
        raise ValueError(
            "找不到這間玩家店鋪。"
        )

    if str(shop["owner_id"]) != owner_user_id:
        raise PermissionError(
            "這間店鋪不屬於品牌持有人。"
        )

    if shop["status"] not in ("open", "supervised"):
        raise ValueError(
            "這間店鋪目前無法正常營業。"
        )

    return brand, shop


# ============================================================
# 綁定店鋪
# ============================================================

def bind_shop(
    guild_id,
    shop_id,
    brand_id,
    owner_user_id,
):

    guild_id = str(guild_id)
    brand_id = str(brand_id)

    with transaction() as db:

        brand, shop = verify_shop_brand(
            db,
            guild_id,
            shop_id,
            brand_id,
            owner_user_id,
        )

        existing = db.execute(
            """
            SELECT brand_id, status
            FROM rp_brand_shop_links
            WHERE guild_id = ?
              AND shop_id = ?
            """,
            (
                guild_id,
                int(shop_id),
            ),
        ).fetchone()

        if existing:
            if (
                existing["brand_id"] == brand_id
                and existing["status"] == "active"
            ):
                return (
                    "✅ 這間店鋪已經綁定至該品牌，"
                    "不需要重複設定。"
                )

            raise ValueError(
                "店鋪已有品牌綁定紀錄，"
                "變更歸屬需要另外申請。"
            )

        db.execute(
            """
            INSERT INTO rp_brand_shop_links (
                guild_id,
                shop_id,
                brand_id,
                linked_by,
                linked_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                int(shop_id),
                brand_id,
                str(owner_user_id),
                utc_now().isoformat(),
            ),
        )

        db.execute(
            """
            INSERT INTO rp_business_audit (
                guild_id,
                business_id,
                action,
                actor_id,
                details
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                brand_id,
                "shop_linked",
                str(owner_user_id),
                f"shop:{shop_id}",
            ),
        )

        return (
            f"✅ 店鋪 #{shop_id}「{shop['name']}」"
            f"已綁定品牌「{brand['name']}」。\n"
            "綁定後的正式銷售才能計入品牌業績。"
        )


# ============================================================
# 同步已確認的銷售與退款
# ============================================================

def sync_brand_sales():

    with get_connection() as db:

        links = db.execute(
            """
            SELECT *
            FROM rp_brand_shop_links
            WHERE status = 'active'
            """
        ).fetchall()

        targets = [
            dict(row)
            for row in links
        ]

    synced = 0

    for link in targets:

        guild_id = link["guild_id"]
        shop_id = link["shop_id"]
        brand_id = link["brand_id"]

        linked_at = parse_timestamp(
            link["linked_at"]
        )

        with transaction() as db:

            # 同步前再次確認品牌仍然存在。
            active_brand = db.execute(
                """
                SELECT 1
                FROM rp_business_entities
                WHERE guild_id = ?
                  AND business_id = ?
                  AND kind = 'brand'
                  AND status = 'active'
                """,
                (
                    guild_id,
                    brand_id,
                ),
            ).fetchone()

            if not active_brand:
                continue

            sales = db.execute(
                """
                SELECT
                    order_key,
                    kind,
                    amount,
                    occurred_at
                FROM personal_shop_sales
                WHERE guild_id = ?
                  AND shop_id = ?
                ORDER BY id
                """,
                (
                    guild_id,
                    shop_id,
                ),
            ).fetchall()

            for sale in sales:

                occurred_at = parse_timestamp(
                    sale["occurred_at"]
                )

                if occurred_at < linked_at:
                    continue

                event_key = (
                    f"personal:{shop_id}:"
                    f"{sale['order_key']}"
                )

                reference_key = None

                if sale["kind"] == "refund":

                    # 舊店鋪退款格式：
                    # refund:原始訂單編號:唯一識別
                    raw_key = str(sale["order_key"])

                    if raw_key.startswith("refund:"):
                        original = raw_key[7:].rpartition(":")[0]

                        if original:
                            reference_key = (
                                f"personal:{shop_id}:{original}"
                            )

                inserted = db.execute(
                    """
                    INSERT OR IGNORE INTO rp_brand_sales (
                        guild_id,
                        brand_id,
                        event_key,
                        kind,
                        amount,
                        reference_key,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        brand_id,
                        event_key,
                        sale["kind"],
                        sale["amount"],
                        reference_key,
                        occurred_at.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    ),
                )

                if inserted.rowcount:
                    synced += 1

    return synced


# ============================================================
# 查詢品牌綁定店鋪
# ============================================================

def list_brand_shops(guild_id, brand_id):

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT
                l.shop_id,
                s.name,
                s.category,
                s.status,
                l.linked_at
            FROM rp_brand_shop_links l
            JOIN personal_shops s
                ON s.guild_id = l.guild_id
                AND s.id = l.shop_id
            WHERE l.guild_id = ?
              AND l.brand_id = ?
              AND l.status = 'active'
            ORDER BY l.shop_id
            """,
            (
                str(guild_id),
                str(brand_id),
            ),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]


# ============================================================
# 初始化
# ============================================================

async def setup(bot):

    initialize()
