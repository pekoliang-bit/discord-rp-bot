
"""Discord RP｜B02、B04 商品批量設定。

檔案：modules/item_batch.py

指令：
/商品批次 匯入
/商品批次 說明

每行格式：
商品編號,使用次數

特點：
- 不註冊第二個 /商城
- 僅群主及授權管理員操作
- 一次最多 100 件
- 自動檢查商品分類、效果及價格
- 已有庫存或實例的商品不得更改規則
- 整批使用同一筆資料庫交易
- 不刪除商品、不修改玩家背包
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import transaction
from core.permissions import Actor

from modules.zzzz_item_durability import (
    init_durability_tables,
)

LOG = logging.getLogger(__name__)

LIMITS = {
    "B02": (2, 7),
    "B04": (3, 6),
}

KEYWORDS = {
    "B02": ("B02", "美妝", "保養"),
    "B04": ("B04", "數位", "科技"),
}

MAX_ITEMS = 100
MIN_PRICE_PER_POINT = 300


def require_staff(interaction):
    if interaction.guild is None:
        raise ValueError("請在伺服器內操作。")

    actor = Actor.from_interaction(interaction)

    is_owner = (
        interaction.user.id
        == interaction.guild.owner_id
    )

    if not (is_owner or actor.is_admin):
        raise ValueError(
            "只有群主或授權管理員可以批量設定商品。"
        )

    return actor


def parse_rows(source):
    """將多行商品編號與次數解析成資料。"""

    rows = []
    seen = set()

    for line_number, raw in enumerate(
        source.splitlines(), 1
    ):
        line = raw.strip()

        if not line or line.startswith("#"):
            continue

        line = line.replace("，", ",")

        parts = [
            part.strip()
            for part in line.split(",")
        ]

        if len(parts) != 2:
            raise ValueError(
                f"第 {line_number} 行格式錯誤。"
                "請使用：商品編號,使用次數"
            )

        try:
            product_id = int(parts[0])
            uses = int(parts[1])
        except ValueError:
            raise ValueError(
                f"第 {line_number} 行必須填寫整數。"
            ) from None

        if product_id <= 0 or uses <= 0:
            raise ValueError(
                f"第 {line_number} 行數值必須大於零。"
            )

        if product_id in seen:
            raise ValueError(
                f"商品 #{product_id} 重複出現。"
            )

        seen.add(product_id)
        rows.append((product_id, uses))

    if not rows:
        raise ValueError("沒有找到可匯入的資料。")

    if len(rows) > MAX_ITEMS:
        raise ValueError(
            f"每次最多設定 {MAX_ITEMS} 件商品。"
        )

    return rows


def detect_category(value):
    category = str(value or "").upper()

    matches = []

    for code, words in KEYWORDS.items():
        if any(
            word.upper() in category
            for word in words
        ):
            matches.append(code)

    # 分類有歧義時拒絕猜測。
    if len(matches) != 1:
        return None

    return matches[0]


def validate_one(conn, guild_id, product_id, uses):
    """驗證單件商品，但不修改資料。"""

    product = conn.execute(
        """
        SELECT
            id,
            name,
            category,
            price,
            effect_kind,
            effect_stat,
            effect_value
        FROM shop_products
        WHERE guild_id=? AND id=?
        """,
        (guild_id, product_id),
    ).fetchone()

    if product is None:
        raise ValueError(
            f"商品 #{product_id} 不存在。"
        )

    code = detect_category(
        product["category"]
    )

    if code is None:
        raise ValueError(
            f"商品 #{product_id} 不是明確的"
            " B02／B04 商品。"
        )

    low, high = LIMITS[code]

    if not low <= uses <= high:
        raise ValueError(
            f"商品 #{product_id} 屬於 {code}，"
            f"只能設定 {low}～{high} 次。"
        )

    if product["effect_kind"] != "instant":
        raise ValueError(
            f"商品 #{product_id} 不是"
            " instant 消耗型效果商品。"
        )

    stat = product["effect_stat"]
    effect = int(product["effect_value"])

    if not stat or effect <= 0:
        raise ValueError(
            f"商品 #{product_id} 尚未設定"
            "有效的每次屬性加成。"
        )

    price = int(product["price"])
    total_gain = effect * uses
    minimum_price = (
        total_gain * MIN_PRICE_PER_POINT
    )

    if price < minimum_price:
        raise ValueError(
            f"商品 #{product_id} 售價不足。"
            f"目前 {price:,} 金幣，"
            f"至少需要 {minimum_price:,} 金幣。"
        )

    # 已有玩家庫存的商品禁止轉換或更改，
    # 避免舊庫存與商品實例資料不一致。
    inventory = conn.execute(
        """
        SELECT COALESCE(SUM(quantity), 0)
        FROM shop_inventory
        WHERE guild_id=? AND product_id=?
        """,
        (guild_id, product_id),
    ).fetchone()[0]

    instance_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM item_instances
        WHERE guild_id=? AND product_id=?
        """,
        (guild_id, product_id),
    ).fetchone()[0]

    existing = conn.execute(
        """
        SELECT category_code, max_uses
        FROM item_use_settings
        WHERE guild_id=? AND product_id=?
        """,
        (guild_id, product_id),
    ).fetchone()

    if inventory > 0 or instance_count > 0:
        # 完全相同的規則允許重複匯入，
        # 不會更動既有商品實例。
        if not (
            existing
            and existing["category_code"] == code
            and int(existing["max_uses"]) == uses
        ):
            raise ValueError(
                f"商品 #{product_id} 已有玩家持有，"
                "不能直接修改使用次數。"
            )

    return {
        "id": product_id,
        "name": product["name"],
        "code": code,
        "uses": uses,
        "stat": stat,
        "effect": effect,
        "price": price,
        "total_gain": total_gain,
    }


def import_rules(guild_id, source):
    guild_id = str(guild_id)
    rows = parse_rows(source)

    with transaction() as conn:
        # 第一階段：全部驗證。
        checked = [
            validate_one(
                conn,
                guild_id,
                product_id,
                uses,
            )
            for product_id, uses in rows
        ]

        # 第二階段：驗證通過後才一起寫入。
        for item in checked:
            conn.execute(
                """
                INSERT INTO item_use_settings(
                    guild_id,
                    product_id,
                    category_code,
                    max_uses
                )
                VALUES (?,?,?,?)
                ON CONFLICT(guild_id, product_id)
                DO UPDATE SET
                    category_code=excluded.category_code,
                    max_uses=excluded.max_uses
                """,
                (
                    guild_id,
                    item["id"],
                    item["code"],
                    item["uses"],
                ),
            )

    b02 = sum(
        item["code"] == "B02"
        for item in checked
    )

    b04 = sum(
        item["code"] == "B04"
        for item in checked
    )

    lines = [
        "✅ **商品批量設定成功**",
        "",
        f"成功：{len(checked)} 件",
        f"B02 美妝保養：{b02} 件",
        f"B04 數位科技：{b04} 件",
        "",
        "商品設定：",
    ]

    # Discord 訊息長度有限，只顯示前 25 件。
    for item in checked[:25]:
        lines.append(
            f"#{item['id']}｜"
            f"{item['name']}｜"
            f"{item['uses']} 次｜"
            f"{item['stat']} +{item['effect']}/次"
        )

    if len(checked) > 25:
        lines.append(
            f"其餘 {len(checked) - 25} 件"
            "已成功登記。"
        )

    lines.extend([
        "",
        "使用次數歸零時，商品會從背包消失。",
    ])

    return "\n".join(lines)[:1900]


class BatchModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(
            title="📦 批量設定商品使用次數",
            timeout=300,
        )

        self.data = discord.ui.TextInput(
            label="每行：商品編號,使用次數",
            placeholder=(
                "51,5\n"
                "52,4\n"
                "53,7\n"
                "101,3"
            ),
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
        )

        self.add_item(self.data)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = require_staff(interaction)
        except ValueError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                import_rules,
                actor.guild_id,
                str(self.data.value),
            )

        except (ValueError, sqlite3.Error) as exc:
            result = (
                "⚠️ **整批設定取消**\n"
                f"{exc}\n\n"
                "沒有套用部分成功的設定。"
            )

        except Exception:
            LOG.exception(
                "商品批量設定發生未預期錯誤"
            )
            result = (
                "⚠️ 商品設定失敗，"
                "請檢查 Railway 日誌。"
            )

        await interaction.followup.send(
            result[:1900],
            ephemeral=True,
        )


class ItemBatch(commands.Cog):
    group = app_commands.Group(
        name="商品批次",
        description="批量設定商品使用次數",
        guild_only=True,
    )

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            init_durability_tables
        )

    @group.command(
        name="匯入",
        description="一次設定多件 B02／B04 商品",
    )
    async def batch_import(
        self,
        interaction: discord.Interaction,
    ):
        try:
            require_staff(interaction)
        except ValueError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            BatchModal()
        )

    @group.command(
        name="說明",
        description="查看商品批量設定格式",
    )
    async def help_command(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.send_message(
            (
                "📦 **商品批量設定說明**\n\n"
                "使用 `/商品批次 匯入`，"
                "在表單中每行輸入：\n"
                "商品編號,使用次數\n\n"
                "範例：\n"
                "51,5\n52,4\n53,7\n\n"
                "B02：2～7 次\n"
                "B04：3～6 次\n"
                "總加成每點至少 300 金幣。\n"
                "整批驗證通過後才會寫入。"
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ItemBatch(bot))
    LOG.info(
        "商品批量設定工具已載入。"
    )
