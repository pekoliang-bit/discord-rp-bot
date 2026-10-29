"""公司 VIP 會員制度。

規則：
- 公司與品牌分開管理。
- VIP 等級由持有人或具 vip 權限的管理層設定。
- 人數不限。
- 消費達標自動升級，不自動降級。
- 折扣不得超過 50%。
- 免費商品福利每 30 天最多一次。
- VIP 消費不額外增加品牌熱度。
- 所有重大異動保留紀錄。

注意：
本模組不直接扣款、退款或發送商品。
正式結帳成功後，才可呼叫 record_purchase。
"""

from __future__ import annotations

import json
import sqlite3
import re
import time

from core.database import get_connection
from modules.world_terms import organization_terms
from modules.company_business import initialize, has_permission


def _world_display(guild_id, message: str) -> str:
    """只轉換使用者可見文字，不變更資料庫欄位或內部識別值。"""
    labels = organization_terms(guild_id)
    replacements = {
        "公司": labels["company"],
        "品牌": labels["brand"],
        "集團": labels["group"],
        "金幣": labels["currency"],
    }
    return re.sub(r"公司|品牌|集團|金幣", lambda m: replacements[m.group()], message)


MAX_DISCOUNT = 50
FREE_ITEM_COOLDOWN = 30 * 24 * 60 * 60
MAX_VIP_LEVEL = 20


def initialize_vip():
    initialize()

    with get_connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_company_vip_tiers (
                guild_id TEXT NOT NULL,
                business_id TEXT NOT NULL,
                level INTEGER NOT NULL,
                title TEXT NOT NULL,
                required_spending INTEGER NOT NULL DEFAULT 0,
                discount_percent INTEGER NOT NULL DEFAULT 0,
                free_item_enabled INTEGER NOT NULL DEFAULT 0,
                benefits TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, business_id, level)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_company_vip_members (
                guild_id TEXT NOT NULL,
                business_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                level INTEGER NOT NULL DEFAULT 0,
                lifetime_spending INTEGER NOT NULL DEFAULT 0,
                joined_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, business_id, character_id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_company_vip_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                business_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                order_key TEXT NOT NULL,
                amount INTEGER NOT NULL,
                kind TEXT NOT NULL
                    CHECK (kind IN ('purchase', 'refund')),
                created_at INTEGER NOT NULL,
                UNIQUE (guild_id, business_id, order_key, kind)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_company_vip_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                business_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                benefit_key TEXT NOT NULL DEFAULT 'free_item',
                product_reference TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'reserved',
                claimed_at INTEGER NOT NULL,
                completed_at INTEGER
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_company_vip_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                business_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_vip_claims_cooldown
            ON rp_company_vip_claims (
                guild_id, business_id, character_id, claimed_at
            )
        """)


def _business(db, guild_id, business_id):
    row = db.execute("""
        SELECT *
        FROM rp_business_entities
        WHERE guild_id = ?
          AND business_id = ?
          AND status = 'active'
    """, (str(guild_id), str(business_id))).fetchone()

    if not row:
        raise ValueError("找不到有效的公司或品牌。")

    return row


def _can_manage(db, guild_id, business_id, user_id):
    business = _business(db, guild_id, business_id)

    if business["owner_id"] == str(user_id):
        return True

    return has_permission(
        db,
        str(guild_id),
        str(business_id),
        str(user_id),
        "vip",
    )


def _audit(db, guild_id, business_id, actor_id, action, details):
    db.execute("""
        INSERT INTO rp_company_vip_audit (
            guild_id, business_id, actor_id,
            action, details, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        str(guild_id),
        str(business_id),
        str(actor_id),
        action,
        json.dumps(details, ensure_ascii=False),
        int(time.time()),
    ))


def set_vip_tier(
    guild_id,
    business_id,
    actor_id,
    level,
    title,
    required_spending,
    discount_percent=0,
    free_item_enabled=False,
    benefits="",
):
    """建立或修改一級 VIP。"""

    initialize_vip()

    level = int(level)
    required_spending = int(required_spending)
    discount_percent = int(discount_percent)
    title = str(title).strip()
    benefits = str(benefits).strip()

    if not 1 <= level <= MAX_VIP_LEVEL:
        raise ValueError("VIP 等級必須介於 1～20。")

    if required_spending < 0:
        raise ValueError("累積消費門檻不能是負數。")

    if not 0 <= discount_percent <= MAX_DISCOUNT:
        raise ValueError("VIP 折扣最高只能設定為 50%。")

    if not title or len(title) > 60:
        raise ValueError("VIP 等級名稱必須是 1～60 字。")

    now = int(time.time())

    with get_connection() as db:
        if not _can_manage(
            db, guild_id, business_id, actor_id
        ):
            raise PermissionError("你沒有此公司的 VIP 管理權限。")

        previous = db.execute("""
            SELECT required_spending
            FROM rp_company_vip_tiers
            WHERE guild_id = ?
              AND business_id = ?
              AND level = ?
        """, (
            str(guild_id), str(business_id), level
        )).fetchone()

        lower = db.execute("""
            SELECT MAX(required_spending) AS value
            FROM rp_company_vip_tiers
            WHERE guild_id = ?
              AND business_id = ?
              AND level < ?
        """, (
            str(guild_id), str(business_id), level
        )).fetchone()["value"]

        higher = db.execute("""
            SELECT MIN(required_spending) AS value
            FROM rp_company_vip_tiers
            WHERE guild_id = ?
              AND business_id = ?
              AND level > ?
        """, (
            str(guild_id), str(business_id), level
        )).fetchone()["value"]

        if lower is not None and required_spending <= lower:
            raise ValueError(
                "本級門檻必須高於所有較低 VIP 等級。"
            )

        if higher is not None and required_spending >= higher:
            raise ValueError(
                "本級門檻必須低於所有較高 VIP 等級。"
            )

        db.execute("""
            INSERT INTO rp_company_vip_tiers (
                guild_id, business_id, level, title,
                required_spending, discount_percent,
                free_item_enabled, benefits, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, business_id, level)
            DO UPDATE SET
                title = excluded.title,
                required_spending = excluded.required_spending,
                discount_percent = excluded.discount_percent,
                free_item_enabled = excluded.free_item_enabled,
                benefits = excluded.benefits,
                updated_at = excluded.updated_at
        """, (
            str(guild_id),
            str(business_id),
            level,
            title,
            required_spending,
            discount_percent,
            int(bool(free_item_enabled)),
            benefits,
            now,
        ))

        _audit(
            db, guild_id, business_id, actor_id,
            "set_vip_tier",
            {
                "level": level,
                "title": title,
                "required_spending": required_spending,
                "discount_percent": discount_percent,
                "free_item_enabled": bool(free_item_enabled),
                "previous_requirement": (
                    previous["required_spending"]
                    if previous else None
                ),
            },
        )

    return f"✅ VIP LV.{level}「{title}」設定完成。"


def list_vip_tiers(guild_id, business_id):
    initialize_vip()

    with get_connection() as db:
        _business(db, guild_id, business_id)

        return db.execute("""
            SELECT *
            FROM rp_company_vip_tiers
            WHERE guild_id = ?
              AND business_id = ?
            ORDER BY level
        """, (
            str(guild_id), str(business_id)
        )).fetchall()


def _eligible_level(db, guild_id, business_id, spending):
    row = db.execute("""
        SELECT MAX(level) AS level
        FROM rp_company_vip_tiers
        WHERE guild_id = ?
          AND business_id = ?
          AND required_spending <= ?
    """, (
        str(guild_id),
        str(business_id),
        int(spending),
    )).fetchone()

    return int(row["level"] or 0)


def record_purchase(
    guild_id,
    business_id,
    character_id,
    order_key,
    amount,
    kind="purchase",
):
    """記錄已確認的真實交易。

    kind:
        purchase：完成付款
        refund：完成退款

    order_key 必須是實際交易的唯一編號。
    重複傳入不會重複計分。

    退款會減少有效累積消費，但不會降低已獲得的等級。
    """

    initialize_vip()

    character_id = int(character_id)
    amount = int(amount)
    order_key = str(order_key).strip()

    if amount <= 0:
        raise ValueError("交易金額必須大於零。")

    if not order_key:
        raise ValueError("必須提供真實訂單編號。")

    if kind not in ("purchase", "refund"):
        raise ValueError("交易類型只能是 purchase 或 refund。")

    now = int(time.time())

    with get_connection() as db:
        _business(db, guild_id, business_id)

        character = db.execute("""
            SELECT id
            FROM characters
            WHERE guild_id = ?
              AND id = ?
        """, (
            str(guild_id), character_id
        )).fetchone()

        if not character:
            raise ValueError("找不到指定角色。")

        existing = db.execute("""
            SELECT id
            FROM rp_company_vip_transactions
            WHERE guild_id = ?
              AND business_id = ?
              AND order_key = ?
              AND kind = ?
        """, (
            str(guild_id),
            str(business_id),
            order_key,
            kind,
        )).fetchone()

        if existing:
            return "ℹ️ 這筆交易已登記，不會重複計算。"

        if kind == "refund":
            original = db.execute("""
                SELECT COALESCE(SUM(
                    CASE
                        WHEN kind = 'purchase' THEN amount
                        ELSE -amount
                    END
                ), 0) AS net_amount
                FROM rp_company_vip_transactions
                WHERE guild_id = ?
                  AND business_id = ?
                  AND character_id = ?
                  AND order_key = ?
            """, (
                str(guild_id),
                str(business_id),
                character_id,
                order_key,
            )).fetchone()

            if amount > int(original["net_amount"]):
                raise ValueError(
                    "退款不能超過該訂單尚未退還的金額。"
                )

        try:
            db.execute("""
                INSERT INTO rp_company_vip_transactions (
                    guild_id, business_id,
                    character_id, order_key,
                    amount, kind, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                str(guild_id),
                str(business_id),
                character_id,
                order_key,
                amount,
                kind,
                now,
            ))
        except sqlite3.IntegrityError:
            return "ℹ️ 這筆交易已登記，不會重複計算。"

        member = db.execute("""
            SELECT level, lifetime_spending
            FROM rp_company_vip_members
            WHERE guild_id = ?
              AND business_id = ?
              AND character_id = ?
        """, (
            str(guild_id),
            str(business_id),
            character_id,
        )).fetchone()

        old_level = int(member["level"]) if member else 0
        old_spending = (
            int(member["lifetime_spending"]) if member else 0
        )

        difference = amount if kind == "purchase" else -amount
        new_spending = max(0, old_spending + difference)

        eligible_level = _eligible_level(
            db, guild_id, business_id, new_spending
        )

        new_level = max(old_level, eligible_level)

        db.execute("""
            INSERT INTO rp_company_vip_members (
                guild_id, business_id, character_id,
                level, lifetime_spending,
                joined_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, business_id, character_id)
            DO UPDATE SET
                level = excluded.level,
                lifetime_spending = excluded.lifetime_spending,
                updated_at = excluded.updated_at
        """, (
            str(guild_id),
            str(business_id),
            character_id,
            new_level,
            new_spending,
            now,
            now,
        ))

        _audit(
            db, guild_id, business_id,
            "system",
            "vip_transaction",
            {
                "character_id": character_id,
                "order_key": order_key,
                "kind": kind,
                "amount": amount,
                "old_level": old_level,
                "new_level": new_level,
            },
        )

    message = (
        f"✅ 交易已計入 VIP 消費。\n"
        f"角色 #{character_id}\n"
        f"有效累積消費：{new_spending:,} {organization_terms(guild_id)['currency']}\n"
        f"目前 VIP LV.{new_level}"
    )

    if new_level > old_level:
        message += f"\n🎉 VIP 升級：LV.{old_level} → LV.{new_level}"

    return message


def get_vip_member(guild_id, business_id, character_id):
    initialize_vip()

    with get_connection() as db:
        _business(db, guild_id, business_id)

        member = db.execute("""
            SELECT *
            FROM rp_company_vip_members
            WHERE guild_id = ?
              AND business_id = ?
              AND character_id = ?
        """, (
            str(guild_id),
            str(business_id),
            int(character_id),
        )).fetchone()

        if not member:
            return None

        tier = db.execute("""
            SELECT *
            FROM rp_company_vip_tiers
            WHERE guild_id = ?
              AND business_id = ?
              AND level = ?
        """, (
            str(guild_id),
            str(business_id),
            member["level"],
        )).fetchone()

        return {
            "character_id": int(member["character_id"]),
            "level": int(member["level"]),
            "lifetime_spending": int(
                member["lifetime_spending"]
            ),
            "title": tier["title"] if tier else "一般會員",
            "discount_percent": (
                int(tier["discount_percent"]) if tier else 0
            ),
            "free_item_enabled": (
                bool(tier["free_item_enabled"]) if tier else False
            ),
            "benefits": tier["benefits"] if tier else "",
        }


def check_free_item(guild_id, business_id, character_id):
    """僅檢查領取資格，不會發放商品。"""

    member = get_vip_member(
        guild_id, business_id, character_id
    )

    if not member:
        return False, "尚未建立 VIP 會員資料。"

    if not member["free_item_enabled"]:
        return False, "目前 VIP 等級沒有免費商品福利。"

    with get_connection() as db:
        last = db.execute("""
            SELECT MAX(claimed_at) AS claimed_at
            FROM rp_company_vip_claims
            WHERE guild_id = ?
              AND business_id = ?
              AND character_id = ?
              AND benefit_key = 'free_item'
              AND status IN ('reserved', 'completed')
        """, (
            str(guild_id),
            str(business_id),
            int(character_id),
        )).fetchone()

    if not last["claimed_at"]:
        return True, "✅ 可以領取本期免費商品。"

    remaining = (
        int(last["claimed_at"])
        + FREE_ITEM_COOLDOWN
        - int(time.time())
    )

    if remaining <= 0:
        return True, "✅ 可以領取本期免費商品。"

    days = (remaining + 86399) // 86400

    return False, f"⏳ 距離下次可領取約 {days} 天。"


def reserve_free_item(
    guild_id,
    business_id,
    character_id,
    actor_user_id,
    product_reference,
):
    """保留免費商品領取名額。

    只記錄福利資格，不發送商品。
    商品系統完成後，需另接正式發貨程序。
    """

    initialize_vip()

    product_reference = str(product_reference).strip()

    if not product_reference:
        raise ValueError("請提供商品編號或識別資料。")

    allowed, message = check_free_item(
        guild_id, business_id, character_id
    )

    if not allowed:
        raise ValueError(message)

    now = int(time.time())

    with get_connection() as db:
        character = db.execute("""
            SELECT owner_id
            FROM characters
            WHERE guild_id = ?
              AND id = ?
        """, (
            str(guild_id), int(character_id)
        )).fetchone()

        if not character:
            raise ValueError("找不到角色。")

        owner_is_actor = (
            str(character["owner_id"]) == str(actor_user_id)
        )

        manager_is_actor = _can_manage(
            db, guild_id, business_id, actor_user_id
        )

        if not (owner_is_actor or manager_is_actor):
            raise PermissionError("你不能替此角色登記免費商品。")

        recent = db.execute("""
            SELECT id
            FROM rp_company_vip_claims
            WHERE guild_id = ?
              AND business_id = ?
              AND character_id = ?
              AND benefit_key = 'free_item'
              AND status IN ('reserved', 'completed')
              AND claimed_at > ?
            LIMIT 1
        """, (
            str(guild_id),
            str(business_id),
            int(character_id),
            now - FREE_ITEM_COOLDOWN,
        )).fetchone()

        if recent:
            raise ValueError("30 天內已登記過免費商品。")

        db.execute("""
            INSERT INTO rp_company_vip_claims (
                guild_id, business_id, character_id,
                benefit_key, product_reference,
                status, claimed_at
            )
            VALUES (?, ?, ?, 'free_item', ?, 'reserved', ?)
        """, (
            str(guild_id),
            str(business_id),
            int(character_id),
            product_reference,
            now,
        ))

        _audit(
            db, guild_id, business_id,
            actor_user_id,
            "reserve_free_item",
            {
                "character_id": int(character_id),
                "product_reference": product_reference,
            },
        )

    return (
        "✅ 免費商品資格已登記。\n"
        "目前狀態：待商品系統發放。\n"
        "這筆登記不會直接新增背包物品。"
    )


async def setup(bot):
    """Discord 擴充模組入口；保留原有 VIP 會員計算函式。"""
    import asyncio

    await asyncio.to_thread(initialize_vip)
