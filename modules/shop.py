
"""Discord RP 官方商城主程式。

功能：
- 官方商城商品資料庫
- 商品新增、修改與下架
- 商品購買與批量購買
- 角色背包
- 贈送物品
- 使用物品與停用效果
- 黑卡商品權限
- 金幣交易
- 十間商店首頁整合

正式指令：/商城

注意：
本模組是唯一註冊 /商城 的模組。
十間商店分類由 zzz_shop_home.py 提供。
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.database import (
    transaction,
    get_connection,
    get_or_create_account,
)
from core.economy import _payment
from core.permissions import (
    Actor,
    require_admin,
    PermissionDenied,
    InvalidState,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def init_shop_db():
    with transaction() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS shop_config (
                guild_id TEXT PRIMARY KEY,
                capacity INTEGER,
                CHECK (
                    capacity IS NULL
                    OR capacity >= 1
                )
            );

            CREATE TABLE IF NOT EXISTS shop_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price INTEGER NOT NULL CHECK(price >= 0),
                stock INTEGER,
                scope TEXT NOT NULL DEFAULT 'public'
                    CHECK(scope IN ('public','black')),
                effect_kind TEXT NOT NULL DEFAULT 'none'
                    CHECK(effect_kind IN (
                        'none','instant','weekly','context'
                    )),
                effect_stat TEXT,
                effect_value INTEGER NOT NULL DEFAULT 0,
                effect_context TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(stock IS NULL OR stock >= 0),
                CHECK(length(name) BETWEEN 1 AND 100),
                CHECK(length(category) BETWEEN 1 AND 60),
                UNIQUE(guild_id, id)
            );

            CREATE INDEX IF NOT EXISTS idx_shop_catalog
                ON shop_products(
                    guild_id, scope, active, category
                );

            CREATE TABLE IF NOT EXISTS shop_inventory (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity >= 0),
                PRIMARY KEY(
                    guild_id, character_id, product_id
                )
            );

            CREATE TABLE IF NOT EXISTS shop_events (
                guild_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, event_key)
            );

            CREATE TABLE IF NOT EXISTS shop_active_bonuses (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                stat TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                activated_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, character_id, stat)
            );

            CREATE TABLE IF NOT EXISTS shop_instant_effects (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                stat TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(guild_id, character_id, stat)
            );

            CREATE TABLE IF NOT EXISTS shop_context_equipment (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                equipped_at TEXT NOT NULL,
                PRIMARY KEY(
                    guild_id, character_id, product_id
                )
            );
        """)


def require_character(conn, actor, character_id):
    if not actor.guild_id:
        raise InvalidState(
            "請在伺服器內使用指令。"
        )

    row = conn.execute(
        """
        SELECT *
        FROM characters
        WHERE guild_id=? AND id=?
        """,
        (actor.guild_id, character_id),
    ).fetchone()

    if not row or row["status"] != "active":
        raise InvalidState(
            "找不到已啟用的角色。"
        )

    if (
        row["owner_id"] != actor.user_id
        and not actor.is_admin
    ):
        raise PermissionDenied(
            "你沒有此角色的操作權限。"
        )

    return row


def get_product(conn, guild_id, product_id):
    row = conn.execute(
        """
        SELECT *
        FROM shop_products
        WHERE guild_id=? AND id=?
        """,
        (guild_id, product_id),
    ).fetchone()

    if row is None:
        raise InvalidState(
            "商品不存在。"
        )

    return row


def has_black_access(conn, guild_id, character_id):
    from modules.black_card import is_black_card_active

    return is_black_card_active(
        conn,
        guild_id,
        character_id,
    )


def validate_effect(kind, stat, value, context):
    if kind not in (
        "none",
        "instant",
        "weekly",
        "context",
    ):
        raise ValueError(
            "商品效果類型不支援。"
        )

    stat = stat.strip() if stat else ""
    context = context.strip() if context else ""

    if kind == "none":
        return None, 0, None

    if (
        not stat
        or len(stat) > 60
        or not (0 < value <= 1_000_000)
    ):
        raise ValueError(
            "效果商品需要屬性名稱與正整數加成值。"
        )

    if kind == "context" and not context:
        raise ValueError(
            "情境加成必須填寫適用場合。"
        )

    return stat, value, context or None


def add_product(
    actor,
    name,
    category,
    price,
    stock,
    scope,
    effect_kind,
    effect_stat,
    effect_value,
    effect_context,
    description,
):
    require_admin(actor)

    name = name.strip()
    category = category.strip()

    if not (
        1 <= len(name) <= 100
        and 1 <= len(category) <= 60
    ):
        raise ValueError(
            "商品名稱與分類不得空白且不能過長。"
        )

    if price < 0 or price > 10**15:
        raise ValueError(
            "商品價格不合法。"
        )

    if stock is not None and stock < 0:
        raise ValueError(
            "庫存不能是負數。"
        )

    if scope not in ("public", "black"):
        raise ValueError(
            "商品商城分類不合法。"
        )

    if len(description) > 500:
        raise ValueError(
            "商品介紹不能超過500字。"
        )

    stat, amount, context = validate_effect(
        effect_kind,
        effect_stat,
        effect_value,
        effect_context,
    )

    with transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO shop_products (
                guild_id,
                name,
                category,
                description,
                price,
                stock,
                scope,
                effect_kind,
                effect_stat,
                effect_value,
                effect_context,
                created_at,
                updated_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                actor.guild_id,
                name,
                category,
                description,
                price,
                stock,
                scope,
                effect_kind,
                stat,
                amount,
                context,
                utc_now(),
                utc_now(),
            ),
        )

        return cursor.lastrowid


def edit_product(
    actor,
    product_id,
    price,
    stock,
    active,
):
    require_admin(actor)

    if price is not None and not (
        0 <= price <= 10**15
    ):
        raise ValueError(
            "商品價格不合法。"
        )

    if stock is not None and stock < 0:
        raise ValueError(
            "庫存不可為負數。"
        )

    with transaction() as conn:
        product = get_product(
            conn,
            actor.guild_id,
            product_id,
        )

        if (
            price is not None
            and price < product["price"]
        ):
            raise ValueError(
                "商品價格只能提高，不能降低。"
            )

        if price is not None:
            conn.execute(
                """
                UPDATE shop_products
                SET price=?, updated_at=?
                WHERE guild_id=? AND id=?
                """,
                (
                    price,
                    utc_now(),
                    actor.guild_id,
                    product_id,
                ),
            )

        if stock is not None:
            conn.execute(
                """
                UPDATE shop_products
                SET stock=?, updated_at=?
                WHERE guild_id=? AND id=?
                """,
                (
                    stock,
                    utc_now(),
                    actor.guild_id,
                    product_id,
                ),
            )

        if active is not None:
            conn.execute(
                """
                UPDATE shop_products
                SET active=?, updated_at=?
                WHERE guild_id=? AND id=?
                """,
                (
                    int(active),
                    utc_now(),
                    actor.guild_id,
                    product_id,
                ),
            )

    return "商品資料已更新。"


def adjust_inventory(
    conn,
    guild_id,
    character_id,
    product_id,
    delta,
):
    if not delta:
        return

    before = conn.execute(
        """
        SELECT quantity
        FROM shop_inventory
        WHERE guild_id=?
          AND character_id=?
          AND product_id=?
        """,
        (
            guild_id,
            character_id,
            product_id,
        ),
    ).fetchone()

    qty = (
        before["quantity"] if before else 0
    ) + delta

    if qty < 0:
        raise InvalidState(
            "背包內的商品數量不足。"
        )

    if qty and delta > 0:
        settings = conn.execute(
            """
            SELECT capacity
            FROM shop_config
            WHERE guild_id=?
            """,
            (guild_id,),
        ).fetchone()

        if (
            settings
            and settings["capacity"] is not None
            and not before
        ):
            used = conn.execute(
                """
                SELECT COUNT(*)
                FROM shop_inventory
                WHERE guild_id=?
                  AND character_id=?
                  AND quantity>0
                """,
                (
                    guild_id,
                    character_id,
                ),
            ).fetchone()[0]

            if used >= settings["capacity"]:
                raise InvalidState(
                    "背包已達伺服器設定的種類容量上限。"
                )

    if qty:
        conn.execute(
            """
            INSERT INTO shop_inventory(
                guild_id,
                character_id,
                product_id,
                quantity
            )
            VALUES (?,?,?,?)
            ON CONFLICT(
                guild_id,
                character_id,
                product_id
            )
            DO UPDATE SET
                quantity=excluded.quantity
            """,
            (
                guild_id,
                character_id,
                product_id,
                qty,
            ),
        )

    else:
        conn.execute(
            """
            DELETE FROM shop_inventory
            WHERE guild_id=?
              AND character_id=?
              AND product_id=?
            """,
            (
                guild_id,
                character_id,
                product_id,
            ),
        )


def recorded(conn, guild_id, key):
    return conn.execute(
        """
        SELECT 1
        FROM shop_events
        WHERE guild_id=? AND event_key=?
        """,
        (guild_id, key),
    ).fetchone() is not None


def log_event(
    conn,
    guild_id,
    key,
    kind,
    character_id,
    product_id,
    quantity,
):
    conn.execute(
        """
        INSERT INTO shop_events(
            guild_id,
            event_key,
            kind,
            character_id,
            product_id,
            quantity,
            created_at
        )
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            guild_id,
            key,
            kind,
            character_id,
            product_id,
            quantity,
            utc_now(),
        ),
    )


# ==========================================
# 商店位置與購買限制
# ==========================================

# 只有能明確辨識的分類才套用搬遷規則，避免錯擋未分類舊商品。
_STORE_WORDS = {
    "B01": ("服飾", "精品"),
    "B02": ("美妝", "保養"),
    "B03": ("珠寶", "鐘錶"),
    "B04": ("數位", "科技"),
    "B05": ("演藝",),
    "B06": ("書籍", "文具"),
    "B07": ("家居", "生活"),
    "B08": ("娛樂", "收藏"),
    "B09": ("特殊道具",),
}


def get_product_store_code(item):
    """依照商品 scope / category 辨認官方商店。"""
    if str(item["scope"] or "").lower() == "black":
        return "B10"

    category = str(item["category"] or "").strip()
    match = re.search(r"(?<![A-Z0-9])B(0[1-9]|10)(?![0-9])", category.upper())
    if match:
        return "B" + match.group(1)

    for code, words in _STORE_WORDS.items():
        if any(word in category for word in words):
            return code
    return None


def _store_location_in_transaction(conn, guild_id, code):
    """在既有 SQLite 交易內查詢，避免巢狀 transaction 造成鎖定。"""
    if not code:
        return "mall", ""

    # 相容尚未建立商店位置表的舊資料庫。
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='shop_locations'"
    ).fetchone()
    if not exists:
        return "mall", ""

    row = conn.execute(
        "SELECT placement, target_url FROM shop_locations "
        "WHERE guild_id=? AND store_code=?",
        (str(guild_id), code),
    ).fetchone()
    if not row:
        return "mall", ""
    return str(row["placement"]), str(row["target_url"] or "")


def require_store_purchase_location(conn, guild_id, item):
    """商城下單前驗證搬遷狀態；其他頻道僅提供連結。"""
    code = get_product_store_code(item)
    placement, url = _store_location_in_transaction(conn, guild_id, code)
    if placement == "channel":
        raise InvalidState(
            f"{code} 已移至其他頻道，不能從 /商城 購買。"
            + (f"\n商店連結：{url}" if url else "")
        )

# ==========================================
# 🎖️ 稱號商城折扣整合
# ==========================================

def achievement_checkout_price(
    conn,
    guild_id,
    character_id,
    item,
    quantity,
):
    """在既有商城交易內計算稱號優惠。

    不開啟第二筆資料庫交易。
    不改變庫存及付款流程。
    未建立稱號資料表時維持原價。
    """

    import json
    from datetime import datetime, timezone
    from decimal import Decimal, ROUND_FLOOR

    original = int(item["price"]) * int(quantity)

    if original <= 0:
        return original, None, 0

    required = (
        "rp_character_achievements",
        "rp_achievement_definitions",
        "rp_title_equipment",
        "rp_title_wear_history",
    )

    tables = {
        row["name"]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            """
        ).fetchall()
    }

    if not set(required).issubset(tables):
        return original, None, 0

    now = datetime.now(timezone.utc)

    def parse_time(value):
        if not value:
            return None

        result = datetime.fromisoformat(str(value))

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result.astimezone(timezone.utc)

    def applicable(discount, store_code):
        scope = discount.get("scope", "all")

        target = discount.get(
            "target",
            discount.get("target_id"),
        )

        if scope == "all":
            return True

        if not target:
            return False

        target = str(target)

        if scope == "category":
            return str(item["category"]) == target

        if scope == "brand":
            # 此專案版 shop_products 沒有保證
            # 存在品牌 ID 欄位，不應猜測品牌。
            return (
                "brand_id" in item.keys()
                and item["brand_id"] is not None
                and str(item["brand_id"]) == target
            )

        if scope == "shop":
            return (
                store_code is not None
                and store_code == target
            )

        if scope == "faction":
            # 官方商城不能假冒陣營商城。
            return False

        if scope == "event":
            return (
                "event_id" in item.keys()
                and item["event_id"] is not None
                and str(item["event_id"]) == target
            )

        return False

    def get_rate(discount):
        try:
            if "percent_off" in discount:
                percent = Decimal(
                    str(discount["percent_off"])
                )
                rate = (
                    Decimal("100") - percent
                ) / Decimal("100")

            elif "discount_percent" in discount:
                percent = Decimal(
                    str(discount["discount_percent"])
                )
                rate = (
                    Decimal("100") - percent
                ) / Decimal("100")

            else:
                rate = Decimal(
                    str(discount.get("rate", 1))
                )

            if (
                not rate.is_finite()
                or rate <= 0
                or rate > 1
            ):
                return None

            return rate

        except Exception:
            return None

    def active(discount, row):
        activation = discount.get(
            "activation",
            "equipped",
        )

        if activation == "owned":
            started = parse_time(row["obtained_at"])

        elif activation == "equipped":
            if not bool(row["equipped"]):
                return False

            started = parse_time(
                row["first_activated_at"]
            )

            if started is None:
                return False

            # 必須在結帳當下有正式穿戴紀錄。
            history = conn.execute(
                """
                SELECT 1
                FROM rp_title_wear_history
                WHERE guild_id=?
                  AND character_id=?
                  AND achievement_id=?
                  AND worn_from <= ?
                  AND (
                      worn_until IS NULL
                      OR worn_until > ?
                  )
                LIMIT 1
                """,
                (
                    str(guild_id),
                    int(character_id),
                    int(row["achievement_id"]),
                    now.isoformat(timespec="seconds"),
                    now.isoformat(timespec="seconds"),
                ),
            ).fetchone()

            if history is None:
                return False

        else:
            return False

        if started is None or now < started:
            return False

        duration = discount.get(
            "duration_hours",
            168,
        )

        if duration is None or duration == "permanent":
            return True

        try:
            from datetime import timedelta

            hours = int(duration)

            if hours <= 0:
                return False

            return now < (
                started + timedelta(hours=hours)
            )

        except (TypeError, ValueError, OverflowError):
            return False

    rows = conn.execute(
        """
        SELECT
            d.id AS achievement_id,
            d.code,
            d.name,
            d.effect_json,
            a.obtained_at,
            COALESCE(e.equipped, 0) AS equipped,
            e.first_activated_at
        FROM rp_character_achievements a
        JOIN rp_achievement_definitions d
          ON d.id=a.achievement_id
         AND d.guild_id=a.guild_id
        LEFT JOIN rp_title_equipment e
          ON e.guild_id=a.guild_id
         AND e.character_id=a.character_id
         AND e.achievement_id=a.achievement_id
        WHERE a.guild_id=?
          AND a.character_id=?
          AND d.enabled=1
        """,
        (
            str(guild_id),
            int(character_id),
        ),
    ).fetchall()

    best_rate = Decimal("1")
    best_title = None
    store_code = get_product_store_code(item)

    for row in rows:
        try:
            effect = json.loads(row["effect_json"])
        except (TypeError, ValueError):
            continue

        if not isinstance(effect, dict):
            continue

        discounts = []

        # 舊稱號格式：揮金如土九折。
        if effect.get("type") == "shop_discount":
            discounts.append(effect)

        # 新稱號格式：可同時有每日加成與折扣。
        extras = effect.get("discounts", [])

        if isinstance(extras, list):
            discounts.extend(
                entry
                for entry in extras
                if isinstance(entry, dict)
                and entry.get("type") == "shop_discount"
            )

        for discount in discounts:
            if not applicable(discount, store_code):
                continue

            if not active(discount, row):
                continue

            rate = get_rate(discount)

            if rate is None:
                continue

            if rate < best_rate:
                best_rate = rate
                best_title = str(row["name"])

    final_price = int(
        (
            Decimal(original) * best_rate
        ).to_integral_value(rounding=ROUND_FLOOR)
    )

    discount_amount = original - final_price

    return (
        final_price,
        best_title,
        discount_amount,
    )


def buy(
    actor,
    character_id,
    product_id,
    quantity,
    request_key,
):
    if not 1 <= quantity <= 10000:
        raise ValueError(
            "每次購買數量必須介於1至10000。"
        )

    with transaction() as conn:
        require_character(
            conn,
            actor,
            character_id,
        )

        key = f"buy:{request_key}"

        if recorded(
            conn,
            actor.guild_id,
            key,
        ):
            return (
                "這筆購買已完成，未再次扣款。"
            )

        item = get_product(
            conn,
            actor.guild_id,
            product_id,
        )

        # 在扣款、扣庫存、入背包之前，阻止已搬遷商店的商城購買。
        require_store_purchase_location(
            conn, actor.guild_id, item
        )

        if not item["active"]:
            raise InvalidState(
                "商品已下架。"
            )

        if item["scope"] == "black":
            if not has_black_access(
                conn,
                actor.guild_id,
                character_id,
            ):
                raise PermissionDenied(
                    "此商品僅限有效黑卡會員購買。"
                )

        if (
            item["stock"] is not None
            and item["stock"] < quantity
        ):
            raise InvalidState(
                "商品庫存不足。"
            )


        # 🎖️ 先依稱號計算真正應付金額。
        total, discount_title, saved_money = (
            achievement_checkout_price(
                conn,
                actor.guild_id,
                character_id,
                item,
                quantity,
            )
        )


        wallet = get_or_create_account(
            conn,
            actor.guild_id,
            "character",
            str(character_id),
            "wallet",
        )

        merchant = get_or_create_account(
            conn,
            actor.guild_id,
            "system",
            "official_shop",
            "treasury",
        )

        if total:
            _payment(
                conn,
                actor.guild_id,
                f"shop:{key}",
                wallet,
                merchant,
                total,
                (
                    f"官方商城購買："
                    f"{item['name']} x{quantity}"
                ),
            )

        adjust_inventory(
            conn,
            actor.guild_id,
            character_id,
            product_id,
            quantity,
        )

        if item["stock"] is not None:
            conn.execute(
                """
                UPDATE shop_products
                SET stock=stock-?,
                    updated_at=?
                WHERE guild_id=? AND id=?
                """,
                (
                    quantity,
                    utc_now(),
                    actor.guild_id,
                    product_id,
                ),
            )

        log_event(
            conn,
            actor.guild_id,
            key,
            "purchase",
            character_id,
            product_id,
            quantity,
        )

        if saved_money > 0:
            return (
                f"🛍️ 購買成功："
                f"{item['name']} ×{quantity}\n"
                f"🎖️ 使用稱號：{discount_title}\n"
                f"💰 實際支付：{total:,} 金幣\n"
                f"✨ 本次優惠：{saved_money:,} 金幣"
            )

        return (
            f"購買成功：{item['name']} ×{quantity}，"
            f"支付 {total:,} 金幣。"
        )

def gift(
    actor,
    sender_id,
    recipient_id,
    product_id,
    quantity,
    request_key,
):
    if sender_id == recipient_id:
        raise ValueError(
            "不能贈送給同一個角色。"
        )

    if not 1 <= quantity <= 10000:
        raise ValueError(
            "轉讓數量必須介於1至10000。"
        )

    with transaction() as conn:
        require_character(
            conn,
            actor,
            sender_id,
        )

        target = conn.execute(
            """
            SELECT status
            FROM characters
            WHERE guild_id=? AND id=?
            """,
            (
                actor.guild_id,
                recipient_id,
            ),
        ).fetchone()

        if (
            not target
            or target["status"] != "active"
        ):
            raise InvalidState(
                "接收角色不存在或尚未啟用。"
            )

        item = get_product(
            conn,
            actor.guild_id,
            product_id,
        )

        key = f"gift:{request_key}"

        if recorded(
            conn,
            actor.guild_id,
            key,
        ):
            return (
                "這筆贈送已完成，未再次轉移物品。"
            )

        equipped = conn.execute(
            """
            SELECT 1
            FROM shop_context_equipment
            WHERE guild_id=?
              AND character_id=?
              AND product_id=?
            """,
            (
                actor.guild_id,
                sender_id,
                product_id,
            ),
        ).fetchone()

        if equipped:
            raise InvalidState(
                "請先卸下此商品後再贈送。"
            )

        active = conn.execute(
            """
            SELECT 1
            FROM shop_active_bonuses
            WHERE guild_id=?
              AND character_id=?
              AND product_id=?
            """,
            (
                actor.guild_id,
                sender_id,
                product_id,
            ),
        ).fetchone()

        if active:
            raise InvalidState(
                "請先停用此商品的週期加成再贈送。"
            )

        adjust_inventory(
            conn,
            actor.guild_id,
            sender_id,
            product_id,
            -quantity,
        )

        adjust_inventory(
            conn,
            actor.guild_id,
            recipient_id,
            product_id,
            quantity,
        )

        log_event(
            conn,
            actor.guild_id,
            key,
            "gift",
            sender_id,
            product_id,
            quantity,
        )

        return (
            f"已贈送 {item['name']} ×{quantity} "
            f"給角色 #{recipient_id}。"
        )


def use_item(
    actor,
    character_id,
    product_id,
    request_key,
):
    with transaction() as conn:
        require_character(
            conn,
            actor,
            character_id,
        )

        key = f"use:{request_key}"

        if recorded(
            conn,
            actor.guild_id,
            key,
        ):
            return (
                "這次使用已經記錄，不會重複生效。"
            )

        item = get_product(
            conn,
            actor.guild_id,
            product_id,
        )

        held = conn.execute(
            """
            SELECT quantity
            FROM shop_inventory
            WHERE guild_id=?
              AND character_id=?
              AND product_id=?
            """,
            (
                actor.guild_id,
                character_id,
                product_id,
            ),
        ).fetchone()

        if (
            not held
            or held["quantity"] <= 0
        ):
            raise InvalidState(
                "背包中沒有這件商品。"
            )

        kind = item["effect_kind"]

        if kind == "none":
            raise InvalidState(
                "這件商品沒有可使用的數值效果。"
            )

        stat = item["effect_stat"]
        value = item["effect_value"]

        if kind == "instant":
            adjust_inventory(
                conn,
                actor.guild_id,
                character_id,
                product_id,
                -1,
            )

            conn.execute(
                """
                INSERT INTO shop_instant_effects(
                    guild_id,
                    character_id,
                    stat,
                    amount
                )
                VALUES (?,?,?,?)
                ON CONFLICT(
                    guild_id,
                    character_id,
                    stat
                )
                DO UPDATE SET
                    amount=amount+excluded.amount
                """,
                (
                    actor.guild_id,
                    character_id,
                    stat,
                    value,
                ),
            )

            msg = (
                f"已使用 {item['name']}，"
                f"記錄一次性 {stat} +{value}。"
            )

        elif kind == "weekly":
            conn.execute(
                """
                INSERT INTO shop_active_bonuses(
                    guild_id,
                    character_id,
                    stat,
                    product_id,
                    amount,
                    activated_at
                )
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(
                    guild_id,
                    character_id,
                    stat
                )
                DO UPDATE SET
                    product_id=excluded.product_id,
                    amount=excluded.amount,
                    activated_at=excluded.activated_at
                """,
                (
                    actor.guild_id,
                    character_id,
                    stat,
                    product_id,
                    value,
                    utc_now(),
                ),
            )

            msg = (
                f"已啟用每週 {stat} +{value}；"
                "同屬性舊加成已被替換。"
            )

        else:
            conn.execute(
                """
                INSERT INTO shop_context_equipment(
                    guild_id,
                    character_id,
                    product_id,
                    equipped_at
                )
                VALUES (?,?,?,?)
                ON CONFLICT(
                    guild_id,
                    character_id,
                    product_id
                )
                DO UPDATE SET
                    equipped_at=excluded.equipped_at
                """,
                (
                    actor.guild_id,
                    character_id,
                    product_id,
                    utc_now(),
                ),
            )

            msg = (
                f"已裝備 {item['name']}。"
                f"適用場合：{item['effect_context']}；"
                "僅於符合場合時加成。"
            )

        log_event(
            conn,
            actor.guild_id,
            key,
            "use",
            character_id,
            product_id,
            1,
        )

        return msg


def unequip(
    actor,
    character_id,
    product_id,
):
    with transaction() as conn:
        require_character(
            conn,
            actor,
            character_id,
        )

        product = get_product(
            conn,
            actor.guild_id,
            product_id,
        )

        if product["effect_kind"] == "weekly":
            count = conn.execute(
                """
                DELETE FROM shop_active_bonuses
                WHERE guild_id=?
                  AND character_id=?
                  AND product_id=?
                """,
                (
                    actor.guild_id,
                    character_id,
                    product_id,
                ),
            ).rowcount

        elif product["effect_kind"] == "context":
            count = conn.execute(
                """
                DELETE FROM shop_context_equipment
                WHERE guild_id=?
                  AND character_id=?
                  AND product_id=?
                """,
                (
                    actor.guild_id,
                    character_id,
                    product_id,
                ),
            ).rowcount

        else:
            raise InvalidState(
                "這件商品沒有可停用的效果。"
            )

        if count:
            return "已停用商品效果。"

        return "此商品目前未啟用。"


def catalog(
    actor,
    character_id,
    black,
    category,
):
    with transaction() as conn:
        require_character(
            conn,
            actor,
            character_id,
        )

        scope = "black" if black else "public"

        if black:
            if not has_black_access(
                conn,
                actor.guild_id,
                character_id,
            ):
                raise PermissionDenied(
                    "僅有效黑卡會員可進入隱藏商城。"
                )

        params = [
            actor.guild_id,
            scope,
        ]

        sql = """
            SELECT id,name,category,price,stock,scope
            FROM shop_products
            WHERE guild_id=?
              AND scope=?
              AND active=1
        """

        if category:
            sql += " AND category=?"
            params.append(category.strip())

        sql += " ORDER BY id LIMIT 25"

        rows = conn.execute(
            sql,
            params,
        ).fetchall()

        # 相容舊商城：不顯示搬遷商店的商品。
        rows = [
            row for row in rows
            if _store_location_in_transaction(
                conn,
                actor.guild_id,
                get_product_store_code(row),
            )[0] != "channel"
        ]

        if not rows:
            return (
                "此商城目前沒有上架商品。"
            )

        lines = []

        for row in rows:
            stock_text = (
                "不限"
                if row["stock"] is None
                else str(row["stock"])
            )

            lines.append(
                f"#{row['id']}｜"
                f"{row['name']}｜"
                f"{row['category']}｜"
                f"{row['price']:,}金幣｜"
                f"庫存：{stock_text}"
            )

        return "\n".join(lines)


def inventory(
    actor,
    character_id,
):
    with get_connection() as conn:
        require_character(
            conn,
            actor,
            character_id,
        )

        rows = conn.execute(
            """
            SELECT
                p.id,
                p.name,
                i.quantity
            FROM shop_inventory i
            JOIN shop_products p
              ON p.guild_id=i.guild_id
             AND p.id=i.product_id
            WHERE i.guild_id=?
              AND i.character_id=?
              AND i.quantity>0
            ORDER BY p.id
            LIMIT 40
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchall()

        if not rows:
            return "背包目前是空的。"

        return "\n".join(
            f"#{row['id']}｜"
            f"{row['name']} × {row['quantity']}"
            for row in rows
        )


class ShopActionModal(discord.ui.Modal):
    """商品操作表單。"""

    def __init__(self, menu, kind: str):
        titles = {
            "buy": "購買商品",
            "gift": "贈送物品",
            "use": "使用物品",
            "stop": "停用物品效果",
        }

        super().__init__(
            title=titles[kind],
            timeout=300,
        )

        self.menu = menu
        self.kind = kind

        self.product = discord.ui.TextInput(
            label="商品編號",
            placeholder="例如：12",
            max_length=12,
        )

        self.add_item(self.product)

        if kind in ("buy", "gift"):
            self.quantity = discord.ui.TextInput(
                label="數量",
                default="1",
                max_length=8,
            )
            self.add_item(self.quantity)

        if kind == "gift":
            self.recipient = discord.ui.TextInput(
                label="收禮角色編號",
                placeholder="例如：8",
                max_length=12,
            )
            self.add_item(self.recipient)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.menu.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        try:
            product_id = int(
                str(self.product.value)
            )

            if product_id <= 0:
                raise ValueError()

            if self.kind in ("buy", "gift"):
                quantity = int(
                    str(self.quantity.value)
                )

                if not 1 <= quantity <= 10000:
                    raise ValueError()

            if self.kind == "gift":
                recipient_id = int(
                    str(self.recipient.value)
                )

                if recipient_id <= 0:
                    raise ValueError()

        except ValueError:
            await interaction.response.send_message(
                "請輸入正確的商品、角色編號及數量。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        actor = Actor.from_interaction(
            interaction
        )

        try:
            if self.kind == "buy":
                result = await asyncio.to_thread(
                    buy,
                    actor,
                    self.menu.character_id,
                    product_id,
                    quantity,
                    str(interaction.id),
                )

            elif self.kind == "gift":
                result = await asyncio.to_thread(
                    gift,
                    actor,
                    self.menu.character_id,
                    recipient_id,
                    product_id,
                    quantity,
                    str(interaction.id),
                )

            elif self.kind == "use":
                result = await asyncio.to_thread(
                    use_item,
                    actor,
                    self.menu.character_id,
                    product_id,
                    str(interaction.id),
                )

            else:
                result = await asyncio.to_thread(
                    unequip,
                    actor,
                    self.menu.character_id,
                    product_id,
                )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            result = f"⚠️ {exc}"

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


class ShopMenu(discord.ui.View):
    """舊商城兼容介面。"""

    def __init__(
        self,
        owner_id: int,
        character_id: int,
        black: bool,
        category: str,
        guild_id=None,
    ):
        super().__init__(timeout=900)

        self.owner_id = owner_id
        self.character_id = character_id
        self.black = black
        self.category = category
        self.guild_id = guild_id

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "請使用自己的 /商城 選單。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="查看商品",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def browse(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                catalog,
                Actor.from_interaction(
                    interaction
                ),
                self.character_id,
                self.black,
                self.category,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            result = f"⚠️ {exc}"

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )

    @discord.ui.button(
        label="購買商品",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def buy_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_modal(
            ShopActionModal(self, "buy")
        )



# 商城角色選擇：不使用全域「目前角色」。
def _shop_owned_characters(actor):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name FROM characters "
            "WHERE guild_id=? AND owner_id=? AND status='active' "
            "ORDER BY id",
            (str(actor.guild_id), str(actor.user_id)),
        ).fetchall()
    return [(int(r["id"]), str(r["name"])) for r in rows]


async def _open_shop_for_character(interaction, character_id, black=False, category=""):
    from modules.zzz_shop_home import ShopHome, home_text, make_store_screen
    actor = Actor.from_interaction(interaction)
    with get_connection() as conn:
        require_character(conn, actor, character_id)
        black_allowed = has_black_access(conn, interaction.guild_id, character_id)
    code = "B10" if black else str(category or "").strip().upper()
    if code and code in {f"B{x:02}" for x in range(1, 11)}:
        content, view = make_store_screen(actor, character_id, code, 0)
    else:
        content = home_text(character_id, interaction.guild_id, black_allowed)
        view = ShopHome(interaction.user.id, character_id, False, "", interaction.guild_id)
    if interaction.response.is_done():
        await interaction.followup.send(content, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(content, view=view, ephemeral=True)


class ShopCharacterSelect(discord.ui.Select):
    def __init__(self, parent, current):
        options = [
            discord.SelectOption(label=f"#{cid}｜{name}"[:100], value=str(cid))
            for cid, name in current
        ]
        super().__init__(placeholder="選擇本次購物角色", options=options, row=0)
        self.parent_view = parent

    async def callback(self, interaction):
        view = self.parent_view
        try:
            cid = int(self.values[0])
            actor = Actor.from_interaction(interaction)
            with get_connection() as conn:
                require_character(conn, actor, cid)
            # 編輯本次選角訊息，不修改其他指令的角色選擇。
            await interaction.response.defer(ephemeral=True)
            await _open_shop_for_character(interaction, cid, view.black, view.category)
        except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as exc:
            if interaction.response.is_done():
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)


class ShopCharacterPicker(discord.ui.View):
    def __init__(self, interaction, characters, black=False, category=""):
        super().__init__(timeout=600)
        self.owner_id = interaction.user.id
        self.guild_id = interaction.guild_id
        self.characters = characters
        self.black = black
        self.category = category
        self.page = 0
        self.rebuild()

    def rebuild(self):
        for child in list(self.children):
            if isinstance(child, ShopCharacterSelect):
                self.remove_item(child)
        page_rows = self.characters[self.page * 25:(self.page + 1) * 25]
        self.add_item(ShopCharacterSelect(self, page_rows))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page + 1) * 25 >= len(self.characters)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or interaction.guild_id != self.guild_id:
            await interaction.response.send_message("⛔ 這不是你的角色選單。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction, button):
        self.page -= 1
        self.rebuild()
        await interaction.response.edit_message(
            content=f"🛍️ 請選擇購物角色（第 {self.page + 1} 頁）", view=self
        )

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction, button):
        self.page += 1
        self.rebuild()
        await interaction.response.edit_message(
            content=f"🛍️ 請選擇購物角色（第 {self.page + 1} 頁）", view=self
        )


class Shop(commands.Cog):
    """官方商城指令。"""

    management = app_commands.Group(
        name="管理",
        description="伺服器管理員操作",
    )

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            init_shop_db
        )

    async def reply(
        self,
        interaction,
        work,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "請在伺服器內使用此指令。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                work
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            result = f"⚠️ {exc}"

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )

    @app_commands.command(
        name="商城",
        description="指定角色購物；不填角色則使用選單選擇",
    )
    async def shop(
        self,
        interaction: discord.Interaction,
        角色編號: str = "",
        黑卡商城: bool = False,
        商品分類: str = "",
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message("請在伺服器內使用。", ephemeral=True)
            return
        try:
            actor = Actor.from_interaction(interaction)
            if str(角色編號).strip():
                from modules.economy import resolve_actor_character
                cid = resolve_actor_character(actor, 角色編號)
                await _open_shop_for_character(interaction, cid, 黑卡商城, 商品分類)
                return
            characters = _shop_owned_characters(actor)
            if not characters:
                await interaction.response.send_message(
                    "⚠️ 你目前沒有可用的已啟用角色。請先完成創角與審核。",
                    ephemeral=True,
                )
                return
            picker = ShopCharacterPicker(interaction, characters, 黑卡商城, 商品分類)
            await interaction.response.send_message(
                "🛍️ 請選擇本次購物角色（第 1 頁）",
                view=picker,
                ephemeral=True,
            )
        except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)

    @management.command(
        name="商城新增",
        description="管理員新增官方商城商品",
    )
    @app_commands.default_permissions(
        manage_guild=True
    )
    async def create(
        self,
        interaction: discord.Interaction,
        名稱: str,
        分類: str,
        價格: app_commands.Range[
            int, 0, 10**15
        ],
        庫存: app_commands.Range[
            int, 0, 100000000
        ] = None,
        黑卡專屬: bool = False,
        效果類型: str = "none",
        效果屬性: str = "",
        效果數值: int = 0,
        適用場合: str = "",
        商品介紹: str = "",
    ):
        actor = Actor.from_interaction(
            interaction
        )

        def work():
            product_id = add_product(
                actor,
                名稱,
                分類,
                價格,
                庫存,
                "black" if 黑卡專屬 else "public",
                效果類型,
                效果屬性,
                效果數值,
                適用場合,
                商品介紹,
            )

            return (
                f"✅ 商品編號 #{product_id} 已建立。"
            )

        await self.reply(
            interaction,
            work,
        )

    @management.command(
        name="商城修改",
        description="管理員修改商城商品",
    )
    @app_commands.default_permissions(
        manage_guild=True
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        商品編號: int,
        新價格: app_commands.Range[
            int, 0, 10**15
        ] = None,
        新庫存: app_commands.Range[
            int, 0, 100000000
        ] = None,
        是否上架: bool = None,
    ):
        await self.reply(
            interaction,
            lambda: edit_product(
                Actor.from_interaction(
                    interaction
                ),
                商品編號,
                新價格,
                新庫存,
                是否上架,
            ),
        )

    @management.command(
        name="商城下架",
        description="管理員停用商品販售",
    )
    @app_commands.default_permissions(
        manage_guild=True
    )
    async def disable(
        self,
        interaction: discord.Interaction,
        商品編號: int,
    ):
        await self.reply(
            interaction,
            lambda: edit_product(
                Actor.from_interaction(
                    interaction
                ),
                商品編號,
                None,
                None,
                False,
            ),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        Shop(bot)
    )
