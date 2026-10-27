
"""Discord RP｜多次使用商品交易整合。

檔案：modules/zzzzz_multiuse_bridge.py

依賴：
- modules.shop
- modules.zzzz_item_durability

不註冊 Discord 斜線指令。
不建立第二套商品實例資料表。

功能：
1. 購買時建立每件商品的使用次數
2. 使用時優先消耗剩餘次數最少的商品
3. 最後一次使用後，從背包刪除一件
4. 贈送時連同剩餘次數轉移
5. 每次使用產生永久屬性加成
6. 利用交易唯一鍵防止重複執行
7. 同一資料庫交易內完成扣款及商品登記
"""

from __future__ import annotations

import logging
import sqlite3

import discord
from discord.ext import commands

from core.database import (
    transaction,
    get_or_create_account,
)
from core.economy import _payment
from core.permissions import (
    InvalidState,
    PermissionDenied,
)

from modules import shop
from modules import zzzz_item_durability as durability


LOG = logging.getLogger(__name__)

_original_buy = shop.buy
_original_gift = shop.gift
_original_use = shop.use_item

_installed = False


def get_rule(conn, guild_id, product_id):
    return conn.execute(
        """
        SELECT category_code, max_uses
        FROM item_use_settings
        WHERE guild_id=? AND product_id=?
        """,
        (str(guild_id), product_id),
    ).fetchone()


def inventory_quantity(
    conn,
    guild_id,
    character_id,
    product_id,
):
    row = conn.execute(
        """
        SELECT quantity
        FROM shop_inventory
        WHERE guild_id=?
          AND character_id=?
          AND product_id=?
        """,
        (
            str(guild_id),
            character_id,
            product_id,
        ),
    ).fetchone()

    return int(row["quantity"]) if row else 0


def instance_count(
    conn,
    guild_id,
    character_id,
    product_id,
):
    return conn.execute(
        """
        SELECT COUNT(*)
        FROM item_instances
        WHERE guild_id=?
          AND character_id=?
          AND product_id=?
        """,
        (
            str(guild_id),
            character_id,
            product_id,
        ),
    ).fetchone()[0]


def check_consistency(
    conn,
    guild_id,
    character_id,
    product_id,
):
    """避免混用舊背包與未登記的多次使用商品。"""

    held = inventory_quantity(
        conn,
        guild_id,
        character_id,
        product_id,
    )

    instances = instance_count(
        conn,
        guild_id,
        character_id,
        product_id,
    )

    if held != instances:
        raise InvalidState(
            "此商品的背包數量與使用次數紀錄不一致。"
            "為避免物品或加成損失，交易已取消。"
            "請由管理員檢查舊商品資料。"
        )


def buy(
    actor,
    character_id,
    product_id,
    quantity,
    request_key,
):
    """購買及建立商品實例，全部在同一交易內。"""

    with transaction() as conn:
        rule = get_rule(
            conn,
            actor.guild_id,
            product_id,
        )

        if rule is None:
            # 非多次使用商品，沿用原商城。
            # 離開此交易後才呼叫原函式。
            special = False
        else:
            special = True

    if not special:
        return _original_buy(
            actor,
            character_id,
            product_id,
            quantity,
            request_key,
        )

    if not 1 <= quantity <= 10000:
        raise ValueError(
            "每次購買數量必須介於 1～10000。"
        )

    with transaction() as conn:
        shop.require_character(
            conn,
            actor,
            character_id,
        )

        key = f"buy:{request_key}"

        if shop.recorded(
            conn,
            actor.guild_id,
            key,
        ):
            return "這筆購買已完成，不會再次扣款。"

        item = shop.get_product(
            conn,
            actor.guild_id,
            product_id,
        )

        if not item["active"]:
            raise InvalidState("商品已下架。")

        if item["effect_kind"] != "instant":
            raise InvalidState(
                "此商品不是可消耗商品。"
            )

        if item["scope"] == "black":
            if not shop.has_black_access(
                conn,
                actor.guild_id,
                character_id,
            ):
                raise PermissionDenied(
                    "需要有效黑卡會員資格。"
                )

        if (
            item["stock"] is not None
            and item["stock"] < quantity
        ):
            raise InvalidState("商品庫存不足。")

        # 若是舊商品，不能默默將舊庫存當作新實例。
        check_consistency(
            conn,
            actor.guild_id,
            character_id,
            product_id,
        )

        total = int(item["price"]) * quantity

        wallet = get_or_create_account(
            conn,
            actor.guild_id,
            "character",
            str(character_id),
            "wallet",
        )

        treasury = get_or_create_account(
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
                treasury,
                total,
                f"官方商城購買：{item['name']} x{quantity}",
            )

        shop.adjust_inventory(
            conn,
            actor.guild_id,
            character_id,
            product_id,
            quantity,
        )

        created = durability.create_item_instances(
            conn,
            actor.guild_id,
            character_id,
            product_id,
            quantity,
        )

        if created != quantity:
            raise InvalidState(
                "商品實例建立不完整，交易已取消。"
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
                    shop.utc_now(),
                    actor.guild_id,
                    product_id,
                ),
            )

        shop.log_event(
            conn,
            actor.guild_id,
            key,
            "purchase",
            character_id,
            product_id,
            quantity,
        )

        return (
            f"✅ 購買 {item['name']} ×{quantity}\n"
            f"💰 支付 {total:,} 金幣\n"
            f"📦 每件可使用 {rule['max_uses']} 次\n"
            f"共新增 {quantity * rule['max_uses']} 次"
            "使用機會。"
        )


def gift(
    actor,
    sender_id,
    recipient_id,
    product_id,
    quantity,
    request_key,
):
    """贈送商品時保留該件商品的使用次數。"""

    with transaction() as conn:
        special = get_rule(
            conn,
            actor.guild_id,
            product_id,
        ) is not None

    if not special:
        return _original_gift(
            actor,
            sender_id,
            recipient_id,
            product_id,
            quantity,
            request_key,
        )

    if sender_id == recipient_id:
        raise ValueError("不可贈送給相同角色。")

    if not 1 <= quantity <= 10000:
        raise ValueError("贈送數量不合法。")

    with transaction() as conn:
        shop.require_character(
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
            (actor.guild_id, recipient_id),
        ).fetchone()

        if not target or target["status"] != "active":
            raise InvalidState(
                "接收角色不存在或尚未啟用。"
            )

        key = f"gift:{request_key}"

        if shop.recorded(
            conn,
            actor.guild_id,
            key,
        ):
            return "這筆贈送已經完成。"

        check_consistency(
            conn,
            actor.guild_id,
            sender_id,
            product_id,
        )

        check_consistency(
            conn,
            actor.guild_id,
            recipient_id,
            product_id,
        )

        rows = conn.execute(
            """
            SELECT id
            FROM item_instances
            WHERE guild_id=?
              AND character_id=?
              AND product_id=?
            ORDER BY remaining_uses ASC, id ASC
            LIMIT ?
            """,
            (
                actor.guild_id,
                sender_id,
                product_id,
                quantity,
            ),
        ).fetchall()

        if len(rows) < quantity:
            raise InvalidState(
                "可贈送的商品數量不足。"
            )

        shop.adjust_inventory(
            conn,
            actor.guild_id,
            sender_id,
            product_id,
            -quantity,
        )

        shop.adjust_inventory(
            conn,
            actor.guild_id,
            recipient_id,
            product_id,
            quantity,
        )

        for row in rows:
            conn.execute(
                """
                UPDATE item_instances
                SET character_id=?
                WHERE id=?
                  AND guild_id=?
                  AND character_id=?
                """,
                (
                    recipient_id,
                    row["id"],
                    actor.guild_id,
                    sender_id,
                ),
            )

        shop.log_event(
            conn,
            actor.guild_id,
            key,
            "gift",
            sender_id,
            product_id,
            quantity,
        )

        return (
            f"🎁 已贈送 {quantity} 件商品"
            f"給角色 #{recipient_id}。\n"
            "每件剩餘使用次數完整保留。"
        )


def use_item(
    actor,
    character_id,
    product_id,
    request_key,
):
    """消耗一次使用次數，最後一次才移除商品。"""

    with transaction() as conn:
        special = get_rule(
            conn,
            actor.guild_id,
            product_id,
        ) is not None

    if not special:
        return _original_use(
            actor,
            character_id,
            product_id,
            request_key,
        )

    with transaction() as conn:
        shop.require_character(
            conn,
            actor,
            character_id,
        )

        key = f"use:{request_key}"

        if shop.recorded(
            conn,
            actor.guild_id,
            key,
        ):
            return "這次使用已完成，不會重複生效。"

        item = shop.get_product(
            conn,
            actor.guild_id,
            product_id,
        )

        if item["effect_kind"] != "instant":
            raise InvalidState(
                "此商品不能透過消耗次數使用。"
            )

        check_consistency(
            conn,
            actor.guild_id,
            character_id,
            product_id,
        )

        if not item["effect_stat"]:
            raise InvalidState(
                "商品尚未設定加成屬性。"
            )

        # effect_value 為每次使用加成。
        effect_value = int(item["effect_value"])

        if effect_value <= 0:
            raise InvalidState(
                "商品尚未設定有效的每次加成。"
            )

        result = durability.consume_instance(
            conn,
            actor.guild_id,
            character_id,
            product_id,
        )

        if result["exhausted"]:
            shop.adjust_inventory(
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
                item["effect_stat"],
                effect_value,
            ),
        )

        shop.log_event(
            conn,
            actor.guild_id,
            key,
            "use",
            character_id,
            product_id,
            1,
        )

        if result["exhausted"]:
            condition = (
                "這件商品的次數已歸零，"
                "已從背包消失。"
            )
        else:
            condition = (
                "這件商品還剩下 "
                f"{result['remaining']} 次。"
            )

        return (
            f"✨ 已使用 {item['name']}\n"
            f"{item['effect_stat']} +{effect_value}\n"
            f"📦 {condition}"
        )


class MultiuseBridge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        global _installed

        if _installed:
            return

        # 只更換 Python 交易函式，
        # 不重新註冊任何 Discord 指令。
        durability.init_durability_tables()

        shop.buy = buy
        shop.gift = gift
        shop.use_item = use_item

        _installed = True

        LOG.info(
            "多次使用商品已接入購買、贈送及使用流程。"
        )

    async def cog_unload(self):
        global _installed

        if _installed:
            shop.buy = _original_buy
            shop.gift = _original_gift
            shop.use_item = _original_use
            _installed = False


async def setup(bot: commands.Bot):
    await bot.add_cog(
        MultiuseBridge(bot)
    )
