"""Discord RP｜商品使用次數管理。

檔案：modules/item_admin.py

指令：
/商品次數 設定
/商品次數 查詢

B02：2～7 次
B04：3～6 次

每點總屬性加成至少 300 金幣。

此模組不註冊 /商城。
不修改玩家背包及資產。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)

from modules import zzzz_item_durability as durability


LOG = logging.getLogger(__name__)

MIN_PRICE_PER_POINT = 300

CATEGORY_NAMES = {
    "B02": ("B02", "美妝", "保養"),
    "B04": ("B04", "數位", "科技"),
}


def check_permission(interaction):
    """只有群主或管理員可以設定商品。"""

    actor = Actor.from_interaction(interaction)

    guild = interaction.guild

    is_owner = (
        guild is not None
        and guild.owner_id == interaction.user.id
    )

    if not (is_owner or actor.is_admin):
        raise PermissionDenied(
            "只有群主或伺服器管理員可以管理商品。"
        )

    return actor


def find_product(conn, guild_id, product_id):
    row = conn.execute(
        """
        SELECT
            id,
            name,
            category,
            price,
            effect_kind,
            effect_stat,
            effect_value,
            scope,
            active
        FROM shop_products
        WHERE guild_id=? AND id=?
        """,
        (str(guild_id), product_id),
    ).fetchone()

    if row is None:
        raise InvalidState(
            "找不到這件商品。"
        )

    return row


def get_category_code(category):
    category = str(category).upper()

    for code, keywords in CATEGORY_NAMES.items():
        if any(
            keyword.upper() in category
            for keyword in keywords
        ):
            return code

    return None


def count_existing_inventory(
    conn,
    guild_id,
    product_id,
):
    return conn.execute(
        """
        SELECT COALESCE(SUM(quantity), 0)
        FROM shop_inventory
        WHERE guild_id=?
          AND product_id=?
        """,
        (str(guild_id), product_id),
    ).fetchone()[0]


def configure_product(
    guild_id,
    product_id,
    uses,
):
    """驗證價格及庫存後，設定使用次數。"""

    guild_id = str(guild_id)

    with get_connection() as conn:
        product = find_product(
            conn,
            guild_id,
            product_id,
        )

        code = get_category_code(
            product["category"]
        )

        if code not in durability.CATEGORY_LIMITS:
            raise InvalidState(
                "只有 B02 美妝保養與 "
                "B04 數位科技商品可使用此設定。"
            )

        minimum, maximum = (
            durability.CATEGORY_LIMITS[code]
        )

        if not minimum <= uses <= maximum:
            raise ValueError(
                f"{code} 的使用次數"
                f"必須介於 {minimum}～{maximum}。"
            )

        if product["effect_kind"] != "instant":
            raise InvalidState(
                "此功能只適用消耗型商品。"
            )

        if (
            not product["effect_stat"]
            or int(product["effect_value"]) <= 0
        ):
            raise InvalidState(
                "商品必須先設定有效的屬性加成。"
            )

        effect_per_use = int(
            product["effect_value"]
        )

        total_effect = effect_per_use * uses

        minimum_price = (
            total_effect * MIN_PRICE_PER_POINT
        )

        actual_price = int(
            product["price"]
        )

        if actual_price < minimum_price:
            raise InvalidState(
                f"商品售價不足。\n"
                f"每次加成：{effect_per_use} 點\n"
                f"使用次數：{uses} 次\n"
                f"總加成：{total_effect} 點\n"
                f"最低售價：{minimum_price:,} 金幣\n"
                f"目前售價：{actual_price:,} 金幣\n\n"
                "請先提高商品售價，"
                "再設定使用次數。"
            )

        owned = count_existing_inventory(
            conn,
            guild_id,
            product_id,
        )

        existing = conn.execute(
            """
            SELECT max_uses
            FROM item_use_settings
            WHERE guild_id=? AND product_id=?
            """,
            (guild_id, product_id),
        ).fetchone()

        # 首次設定時，舊庫存尚無實例紀錄。
        # 禁止直接轉換，避免玩家物品遺失。
        if owned > 0 and existing is None:
            raise InvalidState(
                f"這件商品目前已有 {owned} 件"
                "存放在玩家背包。\n"
                "尚未建立獨立使用次數紀錄，"
                "不能直接啟用新規則。"
            )

    # 使用共用模組提供的資料庫交易。
    # 該函式會阻止修改已有實例的商品。
    durability.configure_item(
        guild_id=guild_id,
        product_id=product_id,
        category_code=code,
        max_uses=uses,
    )

    return (
        "✅ 商品使用次數設定成功！\n\n"
        f"商品：{product['name']}\n"
        f"商品編號：#{product_id}\n"
        f"所屬商店：{code}\n"
        f"每件可使用：{uses} 次\n"
        f"每次增加：{product['effect_stat']} "
        f"+{effect_per_use}\n"
        f"每件總加成：{total_effect} 點\n"
        f"商品售價：{actual_price:,} 金幣\n\n"
        "當使用次數歸零時，"
        "商品將從背包消失。"
    )


def inspect_product(guild_id, product_id):
    guild_id = str(guild_id)

    with get_connection() as conn:
        product = find_product(
            conn,
            guild_id,
            product_id,
        )

        rule = conn.execute(
            """
            SELECT category_code, max_uses
            FROM item_use_settings
            WHERE guild_id=? AND product_id=?
            """,
            (guild_id, product_id),
        ).fetchone()

        instances = conn.execute(
            """
            SELECT
                COUNT(*) AS pieces,
                COALESCE(
                    SUM(remaining_uses), 0
                ) AS remaining
            FROM item_instances
            WHERE guild_id=? AND product_id=?
            """,
            (guild_id, product_id),
        ).fetchone()

        owned = count_existing_inventory(
            conn,
            guild_id,
            product_id,
        )

    if rule:
        rule_text = (
            f"{rule['max_uses']} 次／件"
        )
    else:
        rule_text = "尚未設定"

    return (
        f"📦 **商品使用次數查詢**\n\n"
        f"商品：{product['name']}\n"
        f"編號：#{product_id}\n"
        f"分類：{product['category']}\n"
        f"售價：{int(product['price']):,} 金幣\n"
        f"效果類型：{product['effect_kind']}\n"
        f"加成：{product['effect_stat'] or '無'} "
        f"+{product['effect_value']}\n\n"
        f"使用次數設定：{rule_text}\n"
        f"玩家背包總件數：{owned}\n"
        f"已登記獨立實例：{instances['pieces']}\n"
        f"全部實例剩餘次數："
        f"{instances['remaining']}"
    )


class ItemAdmin(commands.Cog):
    """商品使用次數管理介面。"""

    item_uses = app_commands.Group(
        name="商品次數",
        description="設定及查看商品使用次數",
        guild_only=True,
    )

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            durability.init_durability_tables
        )

    @item_uses.command(
        name="設定",
        description="設定 B02／B04 消耗品使用次數",
    )
    @app_commands.describe(
        商品編號="商品的數字編號",
        使用次數="每件商品可使用幾次",
    )
    async def set_uses(
        self,
        interaction: discord.Interaction,
        商品編號: app_commands.Range[int, 1, None],
        使用次數: app_commands.Range[int, 1, 100],
    ):
        try:
            actor = check_permission(
                interaction
            )

            await interaction.response.defer(
                ephemeral=True
            )

            result = await asyncio.to_thread(
                configure_product,
                actor.guild_id,
                商品編號,
                使用次數,
            )

            await interaction.followup.send(
                result[:1900],
                ephemeral=True,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            message = f"⚠️ {exc}"

            if interaction.response.is_done():
                await interaction.followup.send(
                    message[:1900],
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    message[:1900],
                    ephemeral=True,
                )

    @item_uses.command(
        name="查詢",
        description="查看商品使用次數及實例紀錄",
    )
    @app_commands.describe(
        商品編號="商品的數字編號",
    )
    async def view_uses(
        self,
        interaction: discord.Interaction,
        商品編號: app_commands.Range[int, 1, None],
    ):
        try:
            actor = check_permission(
                interaction
            )

            await interaction.response.defer(
                ephemeral=True
            )

            result = await asyncio.to_thread(
                inspect_product,
                actor.guild_id,
                商品編號,
            )

            await interaction.followup.send(
                result[:1900],
                ephemeral=True,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            message = f"⚠️ {exc}"

            if interaction.response.is_done():
                await interaction.followup.send(
                    message[:1900],
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    message[:1900],
                    ephemeral=True,
                )


async def setup(bot: commands.Bot):
    # 只初始化商品使用次數資料表，不再註冊 /商品次數。
    await asyncio.to_thread(durability.init_durability_tables)
    LOG.info("商品使用次數後端已載入（不註冊獨立指令）。")
