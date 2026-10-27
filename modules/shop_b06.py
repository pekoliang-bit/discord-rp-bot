
"""Discord RP｜B06 書籍文具店。

正式指令：
/b06 上架
/b06 查詢

功能：
- 40 件商品
- 10 件公關專業書籍
- 一次性使用，永久增加屬性
- 群主批量上架
- 重複上架不重複建立
- 沿用 shop_products 與原商城交易
- 不清除玩家背包、角色或銀行資料

不建立第二個 /商城 指令。
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

SHOP_CODE = "B06"
SHOP_CATEGORY = "B06-書籍文具"
EXPECTED_COUNT = 40


# ==========================================
# 商品資料
# ==========================================

# 格式：
# 商品代碼|商品名稱|屬性|單次增加數值|價格
#
# 商品全部為 instant 類型。
# 每使用一件，永久增加一次數值。
# 價格不得低於每點 300 金幣。

CATALOG_TEXT = """
BK-001|演員角色心理學|演繹|3|1200
BK-002|鏡頭前的表演藝術|演繹|5|2000
BK-003|經典戲劇文本解析|演繹|7|2800
BK-004|聲樂基礎與呼吸技巧|歌唱|3|1200
BK-005|流行音樂演唱技巧|歌唱|6|2400
BK-006|現代舞蹈基礎教本|舞蹈|3|1200
BK-007|舞台編舞構成學|舞蹈|7|2800
BK-008|專業主持人培訓手冊|主持|5|2000
BK-009|訪談與語言表達藝術|主持|7|2800
BK-010|綜藝節目即興指南|綜藝|8|3200
BK-011|邏輯思維訓練大全|才智|3|1200
BK-012|世界文明與藝術史|才智|5|2000
BK-013|批判性思考研究手冊|才智|8|3200
BK-014|商業管理入門|商業價值|3|1200
BK-015|演藝產業經營學|商業價值|5|2000
BK-016|個人品牌經營策略|商業價值|8|3200
BK-017|公共溝通與禮儀|路人緣|3|1200
BK-018|跨文化交流指南|路人緣|5|2000
BK-019|社會心理與人際理解|路人緣|7|2800
BK-020|國際市場與商業談判|商業價值|10|4000
BK-021|專業創作筆記本|個人才藝|2|800
BK-022|靈感收集手帳|個人才藝|3|1200
BK-023|藝術速寫工具組|個人才藝|4|1600
BK-024|創意發想便利貼組|個人才藝|5|2000
BK-025|專業劇本標註筆組|演繹|4|1600
BK-026|分鏡創作筆記套組|個人才藝|6|2400
BK-027|故事結構規劃板|才智|5|2000
BK-028|角色設定資料夾|演繹|6|2400
BK-029|高階創作繪圖套組|個人才藝|8|3200
BK-030|劇本企劃工作手冊|才智|9|3600
BK-031|公共關係基礎理論|公關|3|1200
BK-032|媒體關係管理實務|公關|4|1600
BK-033|新聞稿寫作技巧|公關|5|2000
BK-034|藝人形象與危機管理|公關|6|2400
BK-035|社群輿情分析手冊|公關|7|2800
BK-036|危機公關應對策略|公關|8|3200
BK-037|媒體採訪與話術訓練|公關|9|3600
BK-038|企業聲譽管理學|公關|10|4000
BK-039|大型公關事件處理實錄|公關|12|4800
BK-040|國際公共關係戰略大全|公關|15|6000
""".strip()


# ==========================================
# 商品驗證
# ==========================================

def catalog():
    products = []
    seen = set()

    for line_number, raw in enumerate(
        CATALOG_TEXT.splitlines(),
        start=1,
    ):
        line = raw.strip()

        if not line:
            continue

        fields = [
            part.strip()
            for part in line.split("|")
        ]

        if len(fields) != 5:
            raise ValueError(
                f"第 {line_number} 行商品格式錯誤。"
            )

        sku, name, stat, bonus_raw, price_raw = fields

        if not sku or not name or not stat:
            raise ValueError(
                f"第 {line_number} 行有空白欄位。"
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
                f"{sku} 的加成必須大於零。"
            )

        if price < bonus * 300:
            raise ValueError(
                f"{sku} 售價不得低於 {bonus * 300}。"
            )

        products.append(
            (sku, name, stat, bonus, price)
        )

    if len(products) != EXPECTED_COUNT:
        raise ValueError(
            f"B06 必須有 {EXPECTED_COUNT} 件商品，"
            f"目前有 {len(products)} 件。"
        )

    expected = {
        f"BK-{number:03d}"
        for number in range(
            1,
            EXPECTED_COUNT + 1,
        )
    }

    if seen != expected:
        raise ValueError(
            "B06 商品代碼必須為 BK-001～BK-040。"
        )

    pr_count = sum(
        1
        for item in products
        if item[2] == "公關"
    )

    if pr_count != 10:
        raise ValueError(
            "B06 必須保留 10 件公關商品。"
        )

    return products


# ==========================================
# 資料庫初始化
# ==========================================

def init_db():
    shop.init_shop_db()

    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS b06_catalog (
                guild_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                product_id INTEGER NOT NULL,

                PRIMARY KEY (
                    guild_id,
                    sku
                ),

                UNIQUE (
                    guild_id,
                    product_id
                )
            )
            """
        )


# ==========================================
# 商品上架
# ==========================================

def import_catalog(guild_id):
    gid = str(guild_id)

    init_db()

    created = 0
    skipped = 0

    with transaction() as conn:
        existing = {
            str(row["sku"])
            for row in conn.execute(
                """
                SELECT sku
                FROM b06_catalog
                WHERE guild_id=?
                """,
                (gid,),
            ).fetchall()
        }

        for (
            sku,
            name,
            stat,
            bonus,
            price,
        ) in catalog():

            if sku in existing:
                skipped += 1
                continue

            # 使用 UTC ISO 時間。
            now = datetime.now(
                timezone.utc
            ).isoformat()

            description = (
                f"B06 書籍文具｜{sku}｜"
                "一次性消耗品｜"
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
                    SHOP_CATEGORY,
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

            product_id = int(
                result.lastrowid
            )

            conn.execute(
                """
                INSERT INTO b06_catalog (
                    guild_id,
                    sku,
                    product_id
                )
                VALUES (?, ?, ?)
                """,
                (
                    gid,
                    sku,
                    product_id,
                ),
            )

            created += 1

    return (
        "✅ **B06 書籍文具店上架完成！**\n\n"
        f"新增商品：{created} 件\n"
        f"已有紀錄：{skipped} 件\n"
        f"預設商品總數：{EXPECTED_COUNT} 件\n\n"
        "📚 全部商品均可在商城購買。\n"
        "使用後永久增加相應屬性。"
    )


# ==========================================
# 查詢上架進度
# ==========================================

def query_catalog(guild_id):
    init_db()

    with transaction() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM b06_catalog
            WHERE guild_id=?
            """,
            (str(guild_id),),
        ).fetchone()

        total = int(row["total"])

        rows = conn.execute(
            """
            SELECT
                b.sku,
                p.name,
                p.effect_stat,
                p.effect_value,
                p.price,
                p.active
            FROM b06_catalog AS b
            JOIN shop_products AS p
              ON p.guild_id=b.guild_id
             AND p.id=b.product_id
            WHERE b.guild_id=?
            ORDER BY b.sku
            """,
            (str(guild_id),),
        ).fetchall()

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
        "📚 **B06 書籍文具店上架進度**\n\n"
        f"已登記：{total}/{EXPECTED_COUNT} 件\n"
        f"目前啟用：{active} 件\n\n"
        + (
            "\n".join(sample)
            if sample
            else "目前尚未上架商品。"
        )
    )


# ==========================================
# 權限
# ==========================================

def require_owner(
    interaction: discord.Interaction,
):
    if interaction.guild is None:
        raise ValueError(
            "此功能只能在伺服器內使用。"
        )

    if (
        interaction.user.id
        != interaction.guild.owner_id
    ):
        raise ValueError(
            "只有伺服器群主可以批量上架。"
        )


# ==========================================
# Discord 指令
# ==========================================

class B06Shop(commands.Cog):

    group = app_commands.Group(
        name="b06",
        description="B06 書籍文具商品管理",
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
        description="一次上架 40 件書籍文具商品",
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
            message = (
                f"⚠️ B06 上架失敗：{exc}"
            )

        except Exception:
            LOG.exception(
                "B06 批量上架發生錯誤"
            )

            message = (
                "⚠️ 上架發生系統錯誤，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )

    @group.command(
        name="查詢",
        description="查詢 B06 已登記商品",
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
                "B06 商品查詢失敗"
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

async def setup(
    bot: commands.Bot,
):
    await bot.add_cog(
        B06Shop(bot)
    )
