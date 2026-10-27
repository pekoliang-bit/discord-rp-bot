
"""Discord RP｜B08 娛樂收藏店。

指令：
/b08 上架
/b08 查詢

功能：
- 30 件娛樂收藏商品
- 使用後永久增加屬性
- 商品價格與加成數值公開
- 群主批量上架
- 重複上架不重複建立
- 沿用原本商城及背包
- 不修改角色、銀行或既有庫存

不註冊第二個 /商城。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.database import transaction
from modules import shop


LOG = logging.getLogger(__name__)

SHOP_CODE = "B08"
CATEGORY = "B08-娛樂收藏"
EXPECTED_COUNT = 30
MIN_PRICE_PER_POINT = 300


# ==========================================
# 商品資料
# ==========================================

# 格式：
# 商品代碼|商品名稱|增加屬性|增加點數|售價
#
# 每件商品使用一次。
# 使用後永久增加對應屬性。

CATALOG_TEXT = """
EN-001|電競反應訓練卡|個人才藝|3|1200
EN-002|遊戲戰術分析手冊|才智|4|1600
EN-003|競技遊戲訓練套組|個人才藝|5|2000
EN-004|實況直播企劃盒|流量熱度|6|2400
EN-005|遊戲劇情研究集|才智|7|2800
EN-006|多人競賽企劃書|綜藝|8|3200
EN-007|電競賽事解說教材|主持|9|3600
EN-008|遊戲社群經營手冊|路人緣|10|4000
EN-009|高階電競戰術研習組|才智|12|4800
EN-010|職業實況主培訓套件|流量熱度|15|6000
EN-011|經典動畫鑑賞指南|才智|3|1200
EN-012|角色造型收藏企劃盒|個人才藝|4|1600
EN-013|動漫社群交流手冊|路人緣|5|2000
EN-014|限量模型收藏研究冊|商業價值|6|2400
EN-015|角色周邊設計教材|個人才藝|7|2800
EN-016|動漫活動企劃套組|流量熱度|8|3200
EN-017|收藏品市場分析手冊|商業價值|9|3600
EN-018|大型同人展交流套件|路人緣|10|4000
EN-019|珍藏藝術品鑑賞課程|才智|12|4800
EN-020|典藏品牌經營研習盒|商業價值|15|6000
EN-021|派對破冰遊戲卡|路人緣|3|1200
EN-022|娛樂訪談情境題庫|主持|4|1600
EN-023|即興搞笑挑戰卡|綜藝|5|2000
EN-024|粉絲社群互動企劃盒|流量熱度|6|2400
EN-025|社交活動主持教材|主持|7|2800
EN-026|娛樂輿論應對手冊|公關|8|3200
EN-027|大型活動控場套組|主持|9|3600
EN-028|娛樂節目企劃研究盒|綜藝|10|4000
EN-029|社群危機應對實戰題庫|公關|12|4800
EN-030|大型娛樂品牌企劃大全|商業價值|15|6000
""".strip()


# ==========================================
# 商品清單檢查
# ==========================================

def catalog():
    products = []
    seen = set()

    for index, raw in enumerate(
        CATALOG_TEXT.splitlines(),
        start=1,
    ):
        line = raw.strip()

        if not line:
            continue

        fields = [
            field.strip()
            for field in line.split("|")
        ]

        if len(fields) != 5:
            raise ValueError(
                f"第 {index} 行商品格式錯誤。"
            )

        sku, name, stat, bonus_raw, price_raw = fields

        if not sku or not name or not stat:
            raise ValueError(
                f"第 {index} 行存在空白欄位。"
            )

        if sku in seen:
            raise ValueError(
                f"商品代碼重複：{sku}"
            )

        seen.add(sku)

        bonus = int(bonus_raw)
        price = int(price_raw)

        if bonus <= 0:
            raise ValueError(
                f"{sku} 加成必須大於零。"
            )

        if price < bonus * MIN_PRICE_PER_POINT:
            raise ValueError(
                f"{sku} 售價低於最低加成定價。"
            )

        products.append(
            (
                sku,
                name,
                stat,
                bonus,
                price,
            )
        )

    if len(products) != EXPECTED_COUNT:
        raise ValueError(
            f"B08 必須有 {EXPECTED_COUNT} 件商品。"
        )

    expected = {
        f"EN-{i:03d}"
        for i in range(1, 31)
    }

    if seen != expected:
        raise ValueError(
            "B08 商品代碼必須是 EN-001～EN-030。"
        )

    return products


# ==========================================
# 建立商品登記表
# ==========================================

def init_db():
    shop.init_shop_db()

    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS b08_catalog (
                guild_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                product_id INTEGER NOT NULL,

                PRIMARY KEY(guild_id, sku),
                UNIQUE(guild_id, product_id)
            )
            """
        )


# ==========================================
# 批量上架
# ==========================================

def import_catalog(guild_id):
    gid = str(guild_id)

    init_db()

    created = 0
    skipped = 0
    products = catalog()

    with transaction() as conn:
        existing = {
            row["sku"]
            for row in conn.execute(
                """
                SELECT sku
                FROM b08_catalog
                WHERE guild_id=?
                """,
                (gid,),
            ).fetchall()
        }

        for sku, name, stat, bonus, price in products:

            if sku in existing:
                skipped += 1
                continue

            # 如果舊版已存在相同商品代碼，
            # 優先登記舊商品，不額外產生第二件。
            legacy = conn.execute(
                """
                SELECT id
                FROM shop_products
                WHERE guild_id=?
                  AND category LIKE 'B08%'
                  AND description LIKE ?
                ORDER BY id
                LIMIT 1
                """,
                (
                    gid,
                    f"%{sku}%",
                ),
            ).fetchone()

            if legacy is not None:
                conn.execute(
                    """
                    INSERT INTO b08_catalog (
                        guild_id,
                        sku,
                        product_id
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        gid,
                        sku,
                        int(legacy["id"]),
                    ),
                )

                skipped += 1
                continue

            now = datetime.now(
                timezone.utc
            ).isoformat()

            description = (
                f"B08 娛樂收藏｜{sku}｜"
                "一次性使用商品｜"
                f"使用後永久增加 {stat} +{bonus}"
            )

            result = conn.execute(
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
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    1, ?, ?
                )
                """,
                (
                    gid,
                    name,
                    CATEGORY,
                    description,
                    price,
                    None,
                    "public",
                    "instant",
                    stat,
                    bonus,
                    "背包使用後永久增加",
                    now,
                    now,
                ),
            )

            conn.execute(
                """
                INSERT INTO b08_catalog (
                    guild_id,
                    sku,
                    product_id
                )
                VALUES (?, ?, ?)
                """,
                (
                    gid,
                    sku,
                    int(result.lastrowid),
                ),
            )

            created += 1

    return (
        "✅ **B08 娛樂收藏店上架完成！**\n\n"
        f"新增商品：{created} 件\n"
        f"已登記略過：{skipped} 件\n"
        f"預設商品數：{EXPECTED_COUNT} 件\n\n"
        "🎮 所有商品使用後永久增加屬性。"
    )


# ==========================================
# 查詢商品
# ==========================================

def query_catalog(guild_id):
    gid = str(guild_id)

    init_db()

    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                b.sku,
                p.name,
                p.price,
                p.effect_stat,
                p.effect_value,
                p.active
            FROM b08_catalog AS b
            JOIN shop_products AS p
              ON p.guild_id=b.guild_id
             AND p.id=b.product_id
            WHERE b.guild_id=?
            ORDER BY b.sku
            """,
            (gid,),
        ).fetchall()

    total = len(rows)

    active = sum(
        1
        for row in rows
        if int(row["active"]) == 1
    )

    sample = [
        (
            f"{row['sku']}｜{row['name']}｜"
            f"{row['effect_stat']} "
            f"+{row['effect_value']}"
        )
        for row in rows[:8]
    ]

    return (
        "🎮 **B08 娛樂收藏店**\n\n"
        f"已登記商品：{total}/{EXPECTED_COUNT} 件\n"
        f"目前上架：{active} 件\n\n"
        + (
            "\n".join(sample)
            if sample
            else "目前尚未上架商品。"
        )
    )


# ==========================================
# 群主權限
# ==========================================

def require_owner(
    interaction: discord.Interaction,
):
    if (
        interaction.guild is None
        or interaction.user.id
        != interaction.guild.owner_id
    ):
        raise ValueError(
            "只有伺服器群主可以批量上架。"
        )


# ==========================================
# Discord 指令
# ==========================================

class B08Shop(commands.Cog):

    group = app_commands.Group(
        name="b08",
        description="B08 娛樂收藏商品管理",
        guild_only=True,
    )

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            init_db
        )

    @group.command(
        name="上架",
        description="一次上架 30 件娛樂收藏商品",
    )
    async def publish(
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

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            message = await asyncio.to_thread(
                import_catalog,
                interaction.guild_id,
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            message = f"⚠️ 上架失敗：{exc}"

        except Exception:
            LOG.exception(
                "B08 批量上架發生錯誤"
            )

            message = (
                "⚠️ 上架失敗，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )

    @group.command(
        name="查詢",
        description="查詢 B08 商品上架進度",
    )
    async def query(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            message = await asyncio.to_thread(
                query_catalog,
                interaction.guild_id,
            )

        except Exception:
            LOG.exception(
                "B08 商品查詢失敗"
            )

            message = (
                "⚠️ 查詢失敗，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )


# ==========================================
# 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        B08Shop(bot)
    )
