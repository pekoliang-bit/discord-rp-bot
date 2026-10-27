
"""Discord RP｜B10 黑卡隱藏商店。

指令：
/b10 上架
/b10 查詢

功能：
- 20 件黑卡限定商品
- 使用後永久增加指定屬性
- 黑卡專屬商品範圍 black
- 群主批量上架
- 重複執行不重複建立
- 不清空角色、銀行、背包資料
- 沿用官方商城與黑卡權限系統

注意：
唯一 /商城 仍由 modules.shop 提供。
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


LOG = logging.getLogger(__name__)

SHOP_CODE = "B10"
CATEGORY = "B10-黑卡隱藏商店"
EXPECTED_COUNT = 20

# 每點加成至少 300 金幣。
MIN_PRICE_PER_POINT = 300


# ==========================================
# 黑卡限定商品
# ==========================================

# 格式：
# 代碼|名稱|價格|加成屬性|增加數值
#
# 全部商品：
# - 黑卡專屬
# - 一次性消耗
# - 使用後永久增加屬性

CATALOG_TEXT = """
BC-001|黑曜極光形象典藏盒|15000|容貌|30
BC-002|黑曜星光社交禮匣|18000|路人緣|36
BC-003|黑曜名流形象手冊|20000|容貌|40
BC-004|黑曜星際宣傳套組|25000|流量熱度|50
BC-005|黑曜傳奇形象研習盒|30000|容貌|60
BC-006|星冕殿堂聲樂教材|15000|歌唱|30
BC-007|星冕皇家舞蹈教本|18000|舞蹈|36
BC-008|星冕經典演技研修盒|20000|演繹|40
BC-009|星冕全能藝術研究集|25000|個人才藝|50
BC-010|星冕藝術大師典藏冊|30000|個人才藝|60
BC-011|王冠國際商務研習盒|15000|商業價值|30
BC-012|王冠媒體危機處理全書|18000|公關|36
BC-013|王冠頂級訪談訓練集|20000|主持|40
BC-014|王冠戰略決策研究冊|25000|才智|50
BC-015|王冠品牌經營典藏盒|30000|商業價值|60
BC-016|永夜明星綜藝企劃盒|15000|綜藝|30
BC-017|永夜傳奇社群互動集|18000|路人緣|36
BC-018|永夜星海實況企劃冊|20000|流量熱度|40
BC-019|永夜盛典綜藝研習盒|25000|綜藝|50
BC-020|永夜不朽人氣典藏冊|30000|流量熱度|60
""".strip()


# ==========================================
# 商品資料驗證
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

        parts = [
            part.strip()
            for part in line.split("|")
        ]

        if len(parts) != 5:
            raise ValueError(
                f"第 {index} 行商品格式錯誤。"
            )

        (
            sku,
            name,
            price_raw,
            stat,
            bonus_raw,
        ) = parts

        if (
            not sku
            or not name
            or not stat
        ):
            raise ValueError(
                f"第 {index} 行存在空白欄位。"
            )

        if sku in seen:
            raise ValueError(
                f"商品代碼重複：{sku}"
            )

        seen.add(sku)

        price = int(price_raw)
        bonus = int(bonus_raw)

        if price <= 0:
            raise ValueError(
                f"{sku} 售價必須大於零。"
            )

        if bonus <= 0:
            raise ValueError(
                f"{sku} 加成必須大於零。"
            )

        minimum = (
            bonus * MIN_PRICE_PER_POINT
        )

        if price < minimum:
            raise ValueError(
                f"{sku} 售價不得低於 "
                f"{minimum:,} 金幣。"
            )

        products.append(
            (
                sku,
                name,
                price,
                stat,
                bonus,
            )
        )

    if len(products) != EXPECTED_COUNT:
        raise ValueError(
            f"B10 必須有 {EXPECTED_COUNT} 件商品。"
        )

    expected = {
        f"BC-{number:03d}"
        for number in range(
            1,
            EXPECTED_COUNT + 1,
        )
    }

    if seen != expected:
        raise ValueError(
            "商品代碼必須為 BC-001～BC-020。"
        )

    return products


# ==========================================
# 資料表初始化
# ==========================================

def init_db():
    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            b10_catalog (
                guild_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                product_id INTEGER NOT NULL,

                PRIMARY KEY(guild_id, sku),
                UNIQUE(guild_id, product_id)
            )
            """
        )


# ==========================================
# 商品描述
# ==========================================

def description_for(
    sku,
    stat,
    bonus,
):
    return (
        f"B10 黑卡限定｜{sku}｜"
        "一次性消耗商品｜"
        f"使用後永久增加 {stat} +{bonus}｜"
        "僅限有效黑卡會員購買"
    )


# ==========================================
# 批量上架
# ==========================================

def import_catalog(guild_id):
    gid = str(guild_id)

    init_db()
    products = catalog()

    created = 0
    skipped = 0

    with transaction() as conn:
        existing = {
            str(row["sku"])
            for row in conn.execute(
                """
                SELECT sku
                FROM b10_catalog
                WHERE guild_id=?
                """,
                (gid,),
            ).fetchall()
        }

        for (
            sku,
            name,
            price,
            stat,
            bonus,
        ) in products:

            if sku in existing:
                skipped += 1
                continue

            # 比對舊版本的商品代碼，
            # 避免重複上架。
            legacy = conn.execute(
                """
                SELECT id
                FROM shop_products
                WHERE guild_id=?
                  AND scope='black'
                  AND category=?
                  AND description LIKE ?
                ORDER BY id
                """,
                (
                    gid,
                    CATEGORY,
                    f"B10 黑卡限定｜{sku}｜%",
                ),
            ).fetchall()

            if len(legacy) > 1:
                raise ValueError(
                    f"{sku} 存在多筆舊商品，"
                    "請先確認避免重複。"
                )

            if legacy:
                conn.execute(
                    """
                    INSERT INTO b10_catalog (
                        guild_id,
                        sku,
                        product_id
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        gid,
                        sku,
                        int(legacy[0]["id"]),
                    ),
                )

                skipped += 1
                continue

            now = datetime.now(
                timezone.utc
            ).isoformat()

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
                    description_for(
                        sku,
                        stat,
                        bonus,
                    ),
                    price,
                    None,
                    "black",
                    "instant",
                    stat,
                    bonus,
                    "使用後永久增加",
                    now,
                    now,
                ),
            )

            conn.execute(
                """
                INSERT INTO b10_catalog (
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
        "✅ **B10 黑卡隱藏商店上架完成！**\n\n"
        f"新建商品：{created} 件\n"
        f"已登記略過：{skipped} 件\n"
        f"預設商品：{EXPECTED_COUNT} 件\n\n"
        "🖤 所有商品均設定為 black 範圍。\n"
        "✨ 使用後永久增加指定屬性。"
    )


# ==========================================
# 查詢上架狀態
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
                p.scope,
                p.active
            FROM b10_catalog AS b
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

    black = sum(
        1
        for row in rows
        if row["scope"] == "black"
    )

    preview = [
        (
            f"{row['sku']}｜{row['name']}｜"
            f"{row['effect_stat']} "
            f"+{row['effect_value']}"
        )
        for row in rows[:5]
    ]

    return (
        "🖤 **B10 黑卡隱藏商店**\n\n"
        f"已登記：{total}/{EXPECTED_COUNT} 件\n"
        f"目前上架：{active} 件\n"
        f"黑卡範圍：{black} 件\n\n"
        + (
            "\n".join(preview)
            if preview
            else "目前沒有商品。"
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
            "僅限伺服器群主管理 B10。"
        )


# ==========================================
# Discord 指令
# ==========================================

class B10Shop(commands.Cog):

    group = app_commands.Group(
        name="b10",
        description="B10 黑卡隱藏商店管理",
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
        description="群主批量上架 20 件黑卡商品",
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
            result = await asyncio.to_thread(
                import_catalog,
                interaction.guild_id,
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            result = f"⚠️ 上架失敗：{exc}"

        except Exception:
            LOG.exception(
                "B10 批量上架失敗"
            )

            result = (
                "⚠️ 上架失敗，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )

    @group.command(
        name="查詢",
        description="查看 B10 黑卡商品上架狀態",
    )
    async def query(
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
            result = await asyncio.to_thread(
                query_catalog,
                interaction.guild_id,
            )

        except Exception:
            LOG.exception(
                "B10 商品查詢失敗"
            )

            result = (
                "⚠️ 查詢失敗，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


# ==========================================
# 模組初始化
# ==========================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        B10Shop(bot)
    )
