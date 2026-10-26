
"""玩家個人商店：商品、庫存、購買與營業額。

此模組提供可供 /商城 UI 呼叫的交易函式。
不單獨註冊玩家斜線指令。

依賴：
- core.database
- core.economy
- core.permissions
- modules.player_shops
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from core.database import (
    transaction,
    get_connection,
    get_or_create_account,
    account_balance,
)
from core.economy import _payment
from core.permissions import (
    InvalidState,
    PermissionDenied,
)
from modules.player_shops import (
    record_completed_sale,
)


MAX_PRICE = 10**15
MAX_QUANTITY = 10000

EFFECT_KINDS = (
    "none",
    "instant",
    "weekly",
    "context",
)


def utc_stamp():
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")


def initialize_storefront():
    with transaction() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS
        personal_store_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            price INTEGER NOT NULL,
            stock INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            effect_kind TEXT NOT NULL DEFAULT 'none',
            effect_stat TEXT,
            effect_value INTEGER NOT NULL DEFAULT 0,
            effect_context TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(guild_id, id),
            CHECK(price >= 0),
            CHECK(stock IS NULL OR stock >= 0),
            CHECK(active IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS
        personal_store_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            buyer_character_id INTEGER NOT NULL,
            seller_character_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price INTEGER NOT NULL,
            total INTEGER NOT NULL,
            external_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(guild_id, external_key)
        );

        CREATE TABLE IF NOT EXISTS
        personal_store_inventory (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            PRIMARY KEY(
                guild_id,
                character_id,
                product_id
            ),
            CHECK(quantity >= 0)
        );

        CREATE TABLE IF NOT EXISTS
        personal_store_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            actor_id TEXT NOT NULL,
            action TEXT NOT NULL,
            product_id INTEGER,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS
        idx_personal_store_product_listing
        ON personal_store_products(
            guild_id,
            shop_id,
            active
        );

        CREATE INDEX IF NOT EXISTS
        idx_personal_store_orders
        ON personal_store_orders(
            guild_id,
            shop_id,
            created_at
        );
        """)


def require_shop(db, guild_id, shop_id):
    shop = db.execute(
        """
        SELECT *
        FROM personal_shops
        WHERE guild_id = ?
          AND id = ?
        """,
        (str(guild_id), shop_id),
    ).fetchone()

    if shop is None:
        raise InvalidState(
            "找不到指定的個人商店。"
        )

    return shop


def require_shop_manager(
    db,
    actor,
    shop_id,
):
    """目前由店主及官方管理員操作。

    授權店長的權限將由員工合約模組接入。
    """
    shop = require_shop(
        db,
        actor.guild_id,
        shop_id,
    )

    owner = db.execute(
        """
        SELECT owner_id
        FROM characters
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            actor.guild_id,
            shop["character_id"],
        ),
    ).fetchone()

    if owner is None:
        raise InvalidState(
            "商店經營者的角色不存在。"
        )

    if (
        owner["owner_id"] != actor.user_id
        and not actor.is_admin
    ):
        raise PermissionDenied(
            "只有店主或獲授權人員"
            "才能管理這間商店。"
        )

    return shop


def require_buyer(
    db,
    actor,
    character_id,
):
    buyer = db.execute(
        """
        SELECT id, owner_id, status
        FROM characters
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            actor.guild_id,
            character_id,
        ),
    ).fetchone()

    if buyer is None:
        raise InvalidState(
            "購買角色不存在。"
        )

    if buyer["status"] != "active":
        raise InvalidState(
            "購買角色尚未啟用。"
        )

    if (
        buyer["owner_id"] != actor.user_id
        and not actor.is_admin
    ):
        raise PermissionDenied(
            "你沒有操作購買角色的權限。"
        )

    return buyer


def require_product(
    db,
    guild_id,
    product_id,
):
    row = db.execute(
        """
        SELECT *
        FROM personal_store_products
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            str(guild_id),
            product_id,
        ),
    ).fetchone()

    if row is None:
        raise InvalidState(
            "找不到指定商品。"
        )

    return row


def audit(
    db,
    guild_id,
    shop_id,
    actor_id,
    action,
    product_id=None,
    details="",
):
    db.execute(
        """
        INSERT INTO personal_store_events(
            guild_id,
            shop_id,
            actor_id,
            action,
            product_id,
            details,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(guild_id),
            shop_id,
            str(actor_id),
            action,
            product_id,
            details[:500],
            utc_stamp(),
        ),
    )


def validate_effect(
    kind,
    stat,
    value,
    context,
):
    if kind not in EFFECT_KINDS:
        raise ValueError(
            "不支援的商品效果類型。"
        )

    stat = (stat or "").strip()
    context = (context or "").strip()

    if kind == "none":
        return None, 0, None

    if not stat or len(stat) > 60:
        raise ValueError(
            "效果屬性名稱不合法。"
        )

    if (
        type(value) is not int
        or not 1 <= value <= 1_000_000
    ):
        raise ValueError(
            "加成數值必須為正整數。"
        )

    if kind == "context" and not context:
        raise ValueError(
            "情境效果必須設定適用場合。"
        )

    if len(context) > 200:
        raise ValueError(
            "適用場合說明不能超過200字。"
        )

    return (
        stat,
        value,
        context or None,
    )


def create_product(
    actor,
    shop_id,
    name,
    category,
    price,
    stock=None,
    description="",
    effect_kind="none",
    effect_stat="",
    effect_value=0,
    effect_context="",
):
    """玩家可自行上架，無須官方逐件審核。"""

    name = name.strip()
    category = category.strip()

    if not 1 <= len(name) <= 100:
        raise ValueError(
            "商品名稱需為1至100字。"
        )

    if not 1 <= len(category) <= 60:
        raise ValueError(
            "商品分類需為1至60字。"
        )

    if len(description) > 500:
        raise ValueError(
            "商品介紹不得超過500字。"
        )

    if (
        type(price) is not int
        or not 0 <= price <= MAX_PRICE
    ):
        raise ValueError(
            "商品價格不合法。"
        )

    if (
        stock is not None
        and (
            type(stock) is not int
            or stock < 0
        )
    ):
        raise ValueError(
            "庫存不能為負數。"
        )

    stat, amount, context = validate_effect(
        effect_kind,
        effect_stat,
        effect_value,
        effect_context,
    )

    with transaction() as db:
        shop = require_shop_manager(
            db,
            actor,
            shop_id,
        )

        if shop["status"] not in (
            "open",
            "supervised",
        ):
            raise InvalidState(
                "商店目前無法上架商品。"
            )

        cursor = db.execute(
            """
            INSERT INTO personal_store_products(
                guild_id,
                shop_id,
                name,
                category,
                description,
                price,
                stock,
                effect_kind,
                effect_stat,
                effect_value,
                effect_context,
                created_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                actor.guild_id,
                shop_id,
                name,
                category,
                description,
                price,
                stock,
                effect_kind,
                stat,
                amount,
                context,
                utc_stamp(),
                utc_stamp(),
            ),
        )

        product_id = cursor.lastrowid

        audit(
            db,
            actor.guild_id,
            shop_id,
            actor.user_id,
            "create_product",
            product_id,
            name,
        )

        return product_id


def update_product(
    actor,
    shop_id,
    product_id,
    *,
    price=None,
    stock=None,
    active=None,
):
    """修改價格、剩餘庫存及上下架狀態。"""

    if price is not None and (
        type(price) is not int
        or not 0 <= price <= MAX_PRICE
    ):
        raise ValueError(
            "商品價格不合法。"
        )

    if stock is not None and (
        type(stock) is not int
        or stock < 0
    ):
        raise ValueError(
            "商品庫存不合法。"
        )

    if active is not None and (
        type(active) is not bool
    ):
        raise ValueError(
            "上架狀態必須為布林值。"
        )

    with transaction() as db:
        require_shop_manager(
            db,
            actor,
            shop_id,
        )

        item = require_product(
            db,
            actor.guild_id,
            product_id,
        )

        if item["shop_id"] != shop_id:
            raise PermissionDenied(
                "商品不屬於這間商店。"
            )

        values = []
        changes = []

        if price is not None:
            changes.append("price = ?")
            values.append(price)

        if stock is not None:
            changes.append("stock = ?")
            values.append(stock)

        if active is not None:
            changes.append("active = ?")
            values.append(int(active))

        if not changes:
            return "沒有指定要修改的項目。"

        changes.append("updated_at = ?")
        values.append(utc_stamp())

        values.extend(
            [
                actor.guild_id,
                product_id,
            ]
        )

        db.execute(
            f"""
            UPDATE personal_store_products
            SET {", ".join(changes)}
            WHERE guild_id = ?
              AND id = ?
            """,
            values,
        )

        audit(
            db,
            actor.guild_id,
            shop_id,
            actor.user_id,
            "update_product",
            product_id,
            (
                f"price={price}, "
                f"stock={stock}, "
                f"active={active}"
            ),
        )

        return "商品已更新。"


def list_stores(guild_id):
    """供 /商城 的玩家商店分頁使用。"""

    with get_connection() as db:
        return [
            dict(row)
            for row in db.execute(
                """
                SELECT
                    id,
                    name,
                    category,
                    level,
                    status
                FROM personal_shops
                WHERE guild_id = ?
                  AND status IN (
                      'open',
                      'supervised'
                  )
                ORDER BY id DESC
                LIMIT 100
                """,
                (str(guild_id),),
            ).fetchall()
        ]


def list_products(guild_id, shop_id):
    with get_connection() as db:
        shop = require_shop(
            db,
            guild_id,
            shop_id,
        )

        if shop["status"] not in (
            "open",
            "supervised",
        ):
            raise InvalidState(
                "商店目前未營業。"
            )

        return [
            dict(row)
            for row in db.execute(
                """
                SELECT
                    id,
                    name,
                    category,
                    description,
                    price,
                    stock,
                    effect_kind,
                    effect_stat,
                    effect_value
                FROM personal_store_products
                WHERE guild_id = ?
                  AND shop_id = ?
                  AND active = 1
                  AND (
                      stock IS NULL
                      OR stock > 0
                  )
                ORDER BY id
                LIMIT 100
                """,
                (
                    str(guild_id),
                    shop_id,
                ),
            ).fetchall()
        ]


def inventory_adjust(
    db,
    guild_id,
    character_id,
    product_id,
    delta,
):
    previous = db.execute(
        """
        SELECT quantity
        FROM personal_store_inventory
        WHERE guild_id = ?
          AND character_id = ?
          AND product_id = ?
        """,
        (
            str(guild_id),
            character_id,
            product_id,
        ),
    ).fetchone()

    before = (
        previous["quantity"]
        if previous else 0
    )

    new_quantity = before + delta

    if new_quantity < 0:
        raise InvalidState(
            "背包物品數量不足。"
        )

    if new_quantity == 0:
        db.execute(
            """
            DELETE FROM personal_store_inventory
            WHERE guild_id = ?
              AND character_id = ?
              AND product_id = ?
            """,
            (
                str(guild_id),
                character_id,
                product_id,
            ),
        )
        return

    # 預設無容量限制。
    # 日後併入統一背包時，需接入各群組容量設定。

    db.execute(
        """
        INSERT INTO personal_store_inventory(
            guild_id,
            character_id,
            product_id,
            quantity
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(
            guild_id,
            character_id,
            product_id
        )
        DO UPDATE SET
            quantity = excluded.quantity
        """,
        (
            str(guild_id),
            character_id,
            product_id,
            new_quantity,
        ),
    )


def purchase(
    actor,
    buyer_character_id,
    product_id,
    quantity,
    request_key,
):
    """扣款、交貨、記營業額在同一筆交易完成。"""

    if (
        type(quantity) is not int
        or not 1 <= quantity <= MAX_QUANTITY
    ):
        raise ValueError(
            "每次購買數量需為1至10000。"
        )

    request_key = str(request_key)

    if (
        not request_key
        or len(request_key) > 150
    ):
        raise ValueError(
            "交易編號不合法。"
        )

    with transaction() as db:
        existing = db.execute(
            """
            SELECT *
            FROM personal_store_orders
            WHERE guild_id = ?
              AND external_key = ?
            """,
            (
                actor.guild_id,
                request_key,
            ),
        ).fetchone()

        if existing:
            if (
                existing["buyer_character_id"]
                != buyer_character_id
                or existing["product_id"]
                != product_id
                or existing["quantity"]
                != quantity
            ):
                raise InvalidState(
                    "交易編號已被其他訂單使用。"
                )

            return (
                f"訂單 #{existing['id']} "
                "先前已完成，"
                "不會重複扣款。"
            )

        buyer = require_buyer(
            db,
            actor,
            buyer_character_id,
        )

        item = require_product(
            db,
            actor.guild_id,
            product_id,
        )

        shop = require_shop(
            db,
            actor.guild_id,
            item["shop_id"],
        )

        if shop["status"] not in (
            "open",
            "supervised",
        ):
            raise InvalidState(
                "這間商店目前無法交易。"
            )

        if not item["active"]:
            raise InvalidState(
                "商品已下架。"
            )

        if (
            item["stock"] is not None
            and item["stock"] < quantity
        ):
            raise InvalidState(
                "商品庫存不足。"
            )

        seller = db.execute(
            """
            SELECT owner_id, status
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                actor.guild_id,
                shop["character_id"],
            ),
        ).fetchone()

        if not seller or seller["status"] != "active":
            raise InvalidState(
                "商店經營角色無法交易。"
            )

        if (
            buyer_character_id
            == shop["character_id"]
            or buyer["owner_id"]
            == seller["owner_id"]
        ):
            raise InvalidState(
                "不能用店主本人或"
                "同一玩家控制的角色"
                "購買自己的商品。"
            )

        total = item["price"] * quantity

        if total > MAX_PRICE:
            raise ValueError(
                "單筆訂單金額超過限制。"
            )

        buyer_wallet = get_or_create_account(
            db,
            actor.guild_id,
            "character",
            str(buyer_character_id),
            "wallet",
        )

        seller_wallet = get_or_create_account(
            db,
            actor.guild_id,
            "character",
            str(shop["character_id"]),
            "wallet",
        )

        if account_balance(
            db,
            actor.guild_id,
            buyer_wallet,
        ) < total:
            raise InvalidState(
                "購買角色的錢包餘額不足。"
            )

        if total:
            _payment(
                db,
                actor.guild_id,
                f"store:purchase:{request_key}",
                buyer_wallet,
                seller_wallet,
                total,
                (
                    f"玩家商店#{shop['id']} "
                    f"購買{item['name']} "
                    f"×{quantity}"
                ),
            )

        if item["stock"] is not None:
            db.execute(
                """
                UPDATE personal_store_products
                SET stock = stock - ?,
                    updated_at = ?
                WHERE guild_id = ?
                  AND id = ?
                """,
                (
                    quantity,
                    utc_stamp(),
                    actor.guild_id,
                    product_id,
                ),
            )

        inventory_adjust(
            db,
            actor.guild_id,
            buyer_character_id,
            product_id,
            quantity,
        )

        cursor = db.execute(
            """
            INSERT INTO personal_store_orders(
                guild_id,
                shop_id,
                buyer_character_id,
                seller_character_id,
                product_id,
                product_name,
                quantity,
                unit_price,
                total,
                external_key,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor.guild_id,
                shop["id"],
                buyer_character_id,
                shop["character_id"],
                product_id,
                item["name"],
                quantity,
                item["price"],
                total,
                request_key,
                utc_stamp(),
            ),
        )

        order_id = cursor.lastrowid

        # 免費商品不會憑空產生營業額。
        if total:
            record_completed_sale(
                db,
                actor.guild_id,
                shop["id"],
                total,
                f"storefront:{request_key}",
                "sale",
            )

        audit(
            db,
            actor.guild_id,
            shop["id"],
            actor.user_id,
            "purchase",
            product_id,
            (
                f"order={order_id}, "
                f"buyer={buyer_character_id}, "
                f"qty={quantity}, "
                f"total={total}"
            ),
        )

        return (
            f"✅ 訂單 #{order_id} "
            "購買成功！\n"
            f"商品：{item['name']}\n"
            f"數量：{quantity}\n"
            f"支付：{total:,} 金幣\n"
            f"商品已交付角色 "
            f"#{buyer_character_id}。"
        )


def personal_inventory(
    actor,
    character_id,
):
    with get_connection() as db:
        require_buyer(
            db,
            actor,
            character_id,
        )

        rows = db.execute(
            """
            SELECT
                p.id,
                p.name,
                p.shop_id,
                i.quantity
            FROM personal_store_inventory i
            JOIN personal_store_products p
              ON p.guild_id = i.guild_id
             AND p.id = i.product_id
            WHERE i.guild_id = ?
              AND i.character_id = ?
              AND i.quantity > 0
            ORDER BY p.id
            LIMIT 100
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchall()

        if not rows:
            return "目前沒有玩家商店商品。"

        return "\n".join(
            (
                f"#{row['id']}｜"
                f"{row['name']} "
                f"×{row['quantity']} "
                f"（商店#{row['shop_id']}）"
            )
            for row in rows
        )


def store_statistics(
    actor,
    shop_id,
):
    with get_connection() as db:
        shop = require_shop_manager(
            db,
            actor,
            shop_id,
        )

        sales = db.execute(
            """
            SELECT
                COUNT(*) AS order_count,
                COALESCE(SUM(total), 0)
                    AS gross
            FROM personal_store_orders
            WHERE guild_id = ?
              AND shop_id = ?
            """,
            (
                actor.guild_id,
                shop_id,
            ),
        ).fetchone()

        return (
            f"🏬 {shop['name']}\n"
            f"等級：LV.{shop['level']}\n"
            f"狀態：{shop['status']}\n"
            f"完成訂單："
            f"{sales['order_count']} 筆\n"
            f"歷史訂單總額："
            f"{sales['gross']:,}\n"
            f"有效累計營業額："
            f"{shop['sales_total']:,}\n"
            f"首月營業額："
            f"{shop['first_month_sales']:,}\n"
            f"保證金返還門檻："
            f"{shop['deposit'] * 2:,}\n"
            f"首月獎勵門檻："
            f"{shop['deposit'] * 3:,}\n"
            "保證金狀態："
            + (
                "已返還"
                if shop["deposit_returned_at"]
                else "未返還"
            )
        )


class StorefrontService:
    """方便 /商城 介面統一呼叫。"""

    initialize = staticmethod(
        initialize_storefront
    )

    stores = staticmethod(
        list_stores
    )

    products = staticmethod(
        list_products
    )

    create_product = staticmethod(
        create_product
    )

    update_product = staticmethod(
        update_product
    )

    purchase = staticmethod(
        purchase
    )

    inventory = staticmethod(
        personal_inventory
    )

    statistics = staticmethod(
        store_statistics
    )


async def setup(bot):
    """初始化玩家商店交易資料表。

    此模組沒有額外註冊玩家斜線指令；
    玩家操作入口維持 /商城。
    """
    await __import__(
        "asyncio"
    ).to_thread(
        initialize_storefront
    )
