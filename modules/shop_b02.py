
"""B02 美妝保養店｜商品批量上架。

指令：
/B02 匯入
/B02 查詢

每行格式：
代碼|品牌|名稱|售價|類型|屬性|每次加成|使用次數

類型：
消耗：2～7 次
持有：不消耗，使用次數填 0

使用原商城 shop_products。
消耗品使用 item_use_settings。
持有型加成另存設定，等待角色屬性系統接入。

同一代碼不能重複建立。
不重設玩家庫存或使用次數。
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
from core.permissions import Actor
from modules import shop
from modules.zzzz_item_durability import (
    init_durability_tables,
)

LOG = logging.getLogger(__name__)

BRANDS = {
    "珀光之鏡",
    "墨涅索斯",
    "CYBER NOIR",
    "浮嶼",
    "緋鑰",
}

CODE_RE = re.compile(r"^BT-(\d{3})$")


def init_db():
    shop.init_shop_db()
    init_durability_tables()

    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS b02_catalog (
                guild_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                brand TEXT NOT NULL,
                item_type TEXT NOT NULL,
                bonus_stat TEXT NOT NULL,
                bonus_value INTEGER NOT NULL,
                PRIMARY KEY(guild_id, sku),
                UNIQUE(guild_id, product_id)
            )
        """)


def parse_data(raw):
    items = []
    seen = set()

    for number, line in enumerate(raw.splitlines(), 1):
        line = line.strip()

        if not line:
            continue

        parts = [x.strip() for x in line.split("|")]

        if len(parts) != 8:
            raise ValueError(
                f"第 {number} 行需要 8 個欄位，"
                "以 | 分隔。"
            )

        (
            sku, brand, name, price_raw,
            kind, stat, bonus_raw, uses_raw
        ) = parts

        match = CODE_RE.fullmatch(sku.upper())

        if not match:
            raise ValueError(
                f"第 {number} 行商品代碼錯誤。"
            )

        index = int(match.group(1))

        if not 1 <= index <= 50:
            raise ValueError(
                "B02 只接受 BT-001～BT-050。"
            )

        sku = f"BT-{index:03}"

        if sku in seen:
            raise ValueError(
                f"{sku} 在本次資料中重複。"
            )

        seen.add(sku)

        if brand not in BRANDS:
            raise ValueError(
                f"{sku} 的品牌不在既定品牌名單內。"
            )

        if not name or len(name) > 100:
            raise ValueError(
                f"{sku} 的商品名稱不合法。"
            )

        if not stat or len(stat) > 60:
            raise ValueError(
                f"{sku} 的屬性名稱不合法。"
            )

        try:
            price = int(price_raw)
            bonus = int(bonus_raw)
            uses = int(uses_raw)
        except ValueError:
            raise ValueError(
                f"{sku} 的價格、加成及次數"
                "必須為整數。"
            ) from None

        if price <= 0 or bonus <= 0:
            raise ValueError(
                f"{sku} 的價格及加成必須大於 0。"
            )

        if kind == "消耗":
            if not 2 <= uses <= 7:
                raise ValueError(
                    f"{sku} 必須可使用 2～7 次。"
                )
            effect_kind = "instant"
            total_bonus = bonus * uses

        elif kind == "持有":
            if uses != 0:
                raise ValueError(
                    f"{sku} 是持有型商品，"
                    "使用次數必須填 0。"
                )
            effect_kind = "none"
            total_bonus = bonus

        else:
            raise ValueError(
                f"{sku} 類型只能填消耗或持有。"
            )

        minimum_price = total_bonus * 300

        if price < minimum_price:
            raise ValueError(
                f"{sku} 最低售價必須為 "
                f"{minimum_price:,} 金幣。"
            )

        items.append({
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

    if not items:
        raise ValueError("沒有可上架的商品。")

    if len(items) > 50:
        raise ValueError(
            "B02 最多登記 50 件不同商品。"
        )

    return items


def import_items(guild_id, raw):
    guild_id = str(guild_id)
    items = parse_data(raw)

    with transaction() as conn:
        existing_codes = {
            row["sku"]
            for row in conn.execute(
                """
                SELECT sku
                FROM b02_catalog
                WHERE guild_id=?
                """,
                (guild_id,),
            ).fetchall()
        }

        duplicates = [
            item["sku"] for item in items
            if item["sku"] in existing_codes
        ]

        if duplicates:
            raise ValueError(
                "以下代碼已登記，整批取消："
                + "、".join(duplicates)
            )

        # 全部驗證完成後，在同一交易中寫入。
        for item in items:
            description = (
                f"B02 美妝保養｜{item['brand']}｜"
                f"{item['sku']}｜"
                f"{item['kind']}型商品"
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
                    ?,?,?,?,?,?,?,?,?,?,
                    ?,1,?,?
                )
                """,
                (
                    guild_id,
                    item["name"],
                    f"B02-{item['brand']}",
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
                    shop.utc_now(),
                    shop.utc_now(),
                ),
            )

            product_id = cursor.lastrowid

            conn.execute(
                """
                INSERT INTO b02_catalog (
                    guild_id, sku, product_id,
                    brand, item_type,
                    bonus_stat, bonus_value
                )
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    guild_id,
                    item["sku"],
                    product_id,
                    item["brand"],
                    item["kind"],
                    item["stat"],
                    item["bonus"],
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
                        "B02",
                        item["uses"],
                    ),
                )

    return (
        "✅ B02 商品批量上架成功！\n"
        f"新增商品：{len(items)} 件\n"
        "已自動登記消耗型商品使用次數。\n"
        "持有型商品目前僅登記加成設定，"
        "尚未啟用持有屬性效果。"
    )


def catalog_status(guild_id):
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                sku, brand, item_type,
                bonus_stat, bonus_value
            FROM b02_catalog
            WHERE guild_id=?
            ORDER BY sku
            """,
            (str(guild_id),),
        ).fetchall()

    if not rows:
        return "B02 尚未匯入任何商品。"

    lines = [
        f"💄 **B02 美妝保養店**",
        f"已登記：{len(rows)}/50 件",
        "",
    ]

    for row in rows[:35]:
        lines.append(
            f"{row['sku']}｜{row['brand']}｜"
            f"{row['item_type']}｜"
            f"{row['bonus_stat']} "
            f"+{row['bonus_value']}"
        )

    if len(rows) > 35:
        lines.append(
            f"其餘 {len(rows)-35} 件省略顯示。"
        )

    return "\n".join(lines)[:1900]


def require_owner(interaction):
    if (
        interaction.guild is None
        or interaction.user.id
        != interaction.guild.owner_id
    ):
        raise ValueError(
            "此批量上架功能僅限伺服器群主。"
        )

    return Actor.from_interaction(interaction)


class B02Modal(discord.ui.Modal):
    def __init__(self):
        super().__init__(
            title="B02 美妝保養商品上架",
            timeout=300,
        )

        self.data = discord.ui.TextInput(
            label="商品資料，每行一件",
            style=discord.TextStyle.paragraph,
            placeholder=(
                "BT-001|珀光之鏡|商品名稱|6000"
                "|消耗|容貌|5|4"
            ),
            max_length=4000,
            required=True,
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
                import_items,
                str(interaction.guild_id),
                str(self.data.value),
            )
        except (ValueError, sqlite3.Error) as exc:
            message = f"⚠️ 上架取消：{exc}"
        except Exception:
            LOG.exception("B02 批量上架失敗")
            message = (
                "⚠️ 上架失敗，請檢查 Railway 日誌。"
            )

        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )


class B02Shop(commands.Cog):
    group = app_commands.Group(
        name="B02",
        description="B02 美妝保養商品管理",
        guild_only=True,
    )

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(init_db)

    @group.command(
        name="匯入",
        description="群主批量上架 B02 商品",
    )
    async def import_command(self, interaction):
        try:
            require_owner(interaction)
        except ValueError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            B02Modal()
        )

    @group.command(
        name="查詢",
        description="查看 B02 商品登記進度",
    )
    async def status_command(self, interaction):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                catalog_status,
                str(interaction.guild_id),
            )
        except Exception:
            LOG.exception("B02 查詢失敗")
            result = "⚠️ 查詢失敗，請檢查日誌。"

        await interaction.followup.send(
            result[:1900],
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(B02Shop(bot))
