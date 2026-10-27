
"""Discord RP｜B04 數位科技店。

檔案：modules/shop_b04.py

管理指令：
/B04 匯入
/B04 查詢

每行格式：
商品代碼|品牌|商品名稱|售價|類型|屬性|加成|次數

消耗型：
- 每件使用 3～6 次
- 加成數值代表每次使用的永久加成
- 次數歸零即從背包消失

持有型：
- 次數填 0
- 本模組只記錄持有加成
- 實際屬性計算須由後續模組整合

所有商品每 1 點總加成，
售價不得低於 300 金幣。

本模組不建立第二個 /商城。
不修改玩家現有物品及金幣。
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import transaction
from modules import shop
from modules.zzzz_item_durability import (
    init_durability_tables,
)

LOG = logging.getLogger(__name__)

BRANDS = {
    "星雲科技": 13,
    "享樂主義": 13,
    "聲域紀元": 12,
    "零界工坊": 12,
}

BRAND_ALIASES = {
    "NEBULA TECH": "星雲科技",
    "HEDONISM": "享樂主義",
    "SONOVERSE": "聲域紀元",
    "ZEROBOUND": "零界工坊",
}

CODE_PATTERN = re.compile(
    r"^DT-(\d{3})$",
    re.IGNORECASE,
)

MAX_PER_IMPORT = 50
MIN_PRICE_PER_POINT = 300


def initialize():
    shop.init_shop_db()
    init_durability_tables()

    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS b04_catalog (
                guild_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                brand TEXT NOT NULL,
                item_type TEXT NOT NULL,
                bonus_stat TEXT NOT NULL,
                bonus_value INTEGER NOT NULL,
                max_uses INTEGER NOT NULL,
                PRIMARY KEY(guild_id, sku),
                UNIQUE(guild_id, product_id)
            )
        """)


def canonical_brand(value):
    value = value.strip()

    if value in BRANDS:
        return value

    return BRAND_ALIASES.get(
        value.upper()
    )


def expected_brand(sku_number):
    if 1 <= sku_number <= 13:
        return "星雲科技"

    if 14 <= sku_number <= 26:
        return "享樂主義"

    if 27 <= sku_number <= 38:
        return "聲域紀元"

    if 39 <= sku_number <= 50:
        return "零界工坊"

    return None


def parse_items(text):
    result = []
    seen = set()

    for line_no, raw in enumerate(
        text.splitlines(),
        start=1,
    ):
        line = raw.strip()

        if not line or line.startswith("#"):
            continue

        fields = [
            part.strip()
            for part in line.split("|")
        ]

        if len(fields) != 8:
            raise ValueError(
                f"第 {line_no} 行格式不正確，"
                "必須有 8 個欄位。"
            )

        (
            sku,
            brand_text,
            name,
            price_text,
            kind,
            stat,
            bonus_text,
            uses_text,
        ) = fields

        match = CODE_PATTERN.fullmatch(
            sku
        )

        if match is None:
            raise ValueError(
                f"第 {line_no} 行的商品代碼不正確。"
            )

        number = int(
            match.group(1)
        )

        if not 1 <= number <= 50:
            raise ValueError(
                "B04 只接受 DT-001 至 DT-050。"
            )

        sku = f"DT-{number:03d}"

        if sku in seen:
            raise ValueError(
                f"{sku} 在這次匯入中重複。"
            )

        seen.add(sku)

        brand = canonical_brand(
            brand_text
        )

        if brand is None:
            raise ValueError(
                f"{sku} 的品牌不存在。"
            )

        if brand != expected_brand(number):
            raise ValueError(
                f"{sku} 的品牌與預定編號範圍不符。"
            )

        if not 1 <= len(name) <= 100:
            raise ValueError(
                f"{sku} 的名稱不合法。"
            )

        if not 1 <= len(stat) <= 60:
            raise ValueError(
                f"{sku} 的屬性不合法。"
            )

        try:
            price = int(price_text)
            bonus = int(bonus_text)
            uses = int(uses_text)
        except ValueError:
            raise ValueError(
                f"{sku} 的價格、加成與次數"
                "必須填寫整數。"
            ) from None

        if price <= 0 or bonus <= 0:
            raise ValueError(
                f"{sku} 的價格及加成"
                "必須大於零。"
            )

        if kind == "消耗":
            if not 3 <= uses <= 6:
                raise ValueError(
                    f"{sku} 只能使用 3～6 次。"
                )

            effect_kind = "instant"
            total_bonus = bonus * uses

        elif kind == "持有":
            if uses != 0:
                raise ValueError(
                    f"{sku} 是持有型商品，"
                    "次數必須填 0。"
                )

            effect_kind = "none"
            total_bonus = bonus

        else:
            raise ValueError(
                f"{sku} 的類型只能填"
                "「消耗」或「持有」。"
            )

        minimum_price = (
            total_bonus * MIN_PRICE_PER_POINT
        )

        if price < minimum_price:
            raise ValueError(
                f"{sku} 的價格不足。\n"
                f"總加成：{total_bonus} 點\n"
                f"最低售價：{minimum_price:,} 金幣\n"
                f"實際售價：{price:,} 金幣"
            )

        result.append({
            "sku": sku,
            "brand": brand,
            "name": name,
            "price": price,
            "kind": kind,
            "stat": stat,
            "bonus": bonus,
            "uses": uses,
            "effect_kind": effect_kind,
        })

    if not result:
        raise ValueError(
            "沒有找到可匯入的商品。"
        )

    if len(result) > MAX_PER_IMPORT:
        raise ValueError(
            "每次最多匯入 50 件商品。"
        )

    return result


def import_products(guild_id, text):
    guild_id = str(guild_id)
    items = parse_items(text)

    with transaction() as conn:
        # 同一批資料全部驗證後才寫入。
        existing = {
            row["sku"]
            for row in conn.execute(
                """
                SELECT sku
                FROM b04_catalog
                WHERE guild_id=?
                """,
                (guild_id,),
            ).fetchall()
        }

        duplicates = [
            item["sku"]
            for item in items
            if item["sku"] in existing
        ]

        if duplicates:
            raise ValueError(
                "以下商品已經上架，整批取消："
                + "、".join(duplicates)
            )

        current_count = len(existing)

        if current_count + len(items) > 50:
            raise ValueError(
                "B04 最多只能建立 50 件商品。"
            )

        created_at = shop.utc_now()

        for item in items:
            description = (
                f"B04 數位科技｜"
                f"{item['brand']}｜"
                f"{item['sku']}｜"
                f"{item['kind']}型"
            )

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
                    active,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,1,?,?
                )
                """,
                (
                    guild_id,
                    item["name"],
                    f"B04-{item['brand']}",
                    description,
                    item["price"],
                    None,
                    "public",
                    item["effect_kind"],
                    (
                        item["stat"]
                        if item["kind"] == "消耗"
                        else None
                    ),
                    (
                        item["bonus"]
                        if item["kind"] == "消耗"
                        else 0
                    ),
                    None,
                    created_at,
                    created_at,
                ),
            )

            product_id = cursor.lastrowid

            conn.execute(
                """
                INSERT INTO b04_catalog (
                    guild_id,
                    sku,
                    product_id,
                    brand,
                    item_type,
                    bonus_stat,
                    bonus_value,
                    max_uses
                )
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    guild_id,
                    item["sku"],
                    product_id,
                    item["brand"],
                    item["kind"],
                    item["stat"],
                    item["bonus"],
                    item["uses"],
                ),
            )

            if item["kind"] == "消耗":
                conn.execute(
                    """
                    INSERT INTO item_use_settings (
                        guild_id,
                        product_id,
                        category_code,
                        max_uses
                    )
                    VALUES (?,?,?,?)
                    """,
                    (
                        guild_id,
                        product_id,
                        "B04",
                        item["uses"],
                    ),
                )

    consumables = sum(
        item["kind"] == "消耗"
        for item in items
    )

    persistent = len(items) - consumables

    return (
        "✅ **B04 數位科技上架成功**\n\n"
        f"新增商品：{len(items)} 件\n"
        f"消耗型商品：{consumables} 件\n"
        f"持有型商品：{persistent} 件\n\n"
        "消耗品已登記 3～6 次使用限制。\n"
        "次數歸零後，商品將消失。\n\n"
        "持有型商品尚待整合"
        "實際屬性加成。"
    )


def status(guild_id):
    guild_id = str(guild_id)

    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                c.sku,
                c.brand,
                c.item_type,
                c.bonus_stat,
                c.bonus_value,
                c.max_uses,
                p.name,
                p.price
            FROM b04_catalog AS c
            JOIN shop_products AS p
              ON p.guild_id=c.guild_id
             AND p.id=c.product_id
            WHERE c.guild_id=?
            ORDER BY c.sku
            """,
            (guild_id,),
        ).fetchall()

    if not rows:
        return (
            "📱 B04 數位科技店"
            "尚未匯入商品。"
        )

    lines = [
        "📱 **B04 數位科技店**",
        f"已登記：{len(rows)}/50 件",
        "",
    ]

    for row in rows:
        mode = (
            f"{row['max_uses']} 次"
            if row["item_type"] == "消耗"
            else "持有型"
        )

        next_line = (
            f"{row['sku']}｜"
            f"{row['name']}｜"
            f"{mode}"
        )

        if sum(
            len(line) + 1 for line in lines
        ) + len(next_line) > 1750:
            lines.append(
                "其餘商品請至商城查看。"
            )
            break

        lines.append(next_line)

    return "\n".join(lines)


def require_owner(interaction):
    if (
        interaction.guild is None
        or interaction.user.id
        != interaction.guild.owner_id
    ):
        raise ValueError(
            "僅限伺服器群主批量上架 B04 商品。"
        )


class B04Modal(discord.ui.Modal):
    def __init__(self):
        super().__init__(
            title="B04 數位科技商品匯入",
            timeout=300,
        )

        self.data = discord.ui.TextInput(
            label="每行填寫一件商品",
            style=discord.TextStyle.paragraph,
            placeholder=(
                "DT-001|星雲科技|商品名稱"
                "|6000|消耗|才智|5|4"
            ),
            required=True,
            max_length=4000,
        )

        self.add_item(self.data)

    async def on_submit(self, interaction):
        try:
            require_owner(interaction)

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
            message = await asyncio.to_thread(
                import_products,
                interaction.guild_id,
                str(self.data.value),
            )

        except (ValueError, sqlite3.Error) as exc:
            message = (
                f"⚠️ 匯入失敗：{exc}\n"
                "本次未新增任何商品。"
            )

        except Exception:
            LOG.exception(
                "B04 商品批量匯入失敗"
            )
            message = (
                "⚠️ 發生系統錯誤，"
                "請檢查 Railway 日誌。"
            )

        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )


class B04Shop(commands.Cog):
    group = app_commands.Group(
        name="b04",
        description="B04 數位科技商品管理",
        guild_only=True,
    )

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            initialize
        )

    @group.command(
        name="匯入",
        description="群主批量上架 B04 商品",
    )
    async def import_command(
        self,
        interaction: discord.Interaction,
    ):
        try:
            require_owner(interaction)

        except ValueError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            B04Modal()
        )

    @group.command(
        name="查詢",
        description="查看 B04 商品上架進度",
    )
    async def status_command(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                status,
                interaction.guild_id,
            )

        except Exception:
            LOG.exception(
                "B04 商品查詢失敗"
            )
            result = (
                "⚠️ 查詢失敗，"
                "請檢查 Railway 日誌。"
            )

        await interaction.followup.send(
            result[:1900],
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        B04Shop(bot)
    )

    LOG.info(
        "B04 數位科技商品管理模組已載入。"
    )
