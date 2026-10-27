
"""Discord RP｜B05 演藝用品店。

功能：
- 30 件演藝用品
- 一次性消耗品
- 使用後永久增加角色屬性
- 群主批量上架
- 查詢上架進度
- 重複上架不新增相同商品
- 沿用既有商城、銀行及背包

指令：
/b05 上架
/b05 查詢

不建立第二個 /商城 指令。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import transaction
from modules import shop


LOG = logging.getLogger(__name__)


# ==========================================
# B05 商品清單
# ==========================================

# 格式：
# 商品代碼|商品名稱|加成屬性|單次加成|價格
#
# 每件商品可使用一次。
# 使用後永久增加指定屬性。
# 價格至少為每點加成 300 金幣。

CATALOG_TEXT = """
ST-001|劇本解析筆記組|演繹|3|1200
ST-002|情緒節奏排練卡|演繹|4|1600
ST-003|鏡頭走位標記組|演繹|5|2000
ST-004|角色傳記研究盒|才智|4|1600
ST-005|經典獨白訓練稿|演繹|6|2400
ST-006|情境表演練習組|演繹|8|3200
ST-007|聲樂暖嗓練習組|歌唱|3|1200
ST-008|節奏與音準訓練器|歌唱|5|2000
ST-009|錄音室試唱套件|歌唱|6|2400
ST-010|舞台發聲課程卡|歌唱|8|3200
ST-011|舞步基礎記錄板|舞蹈|3|1200
ST-012|節拍訓練地墊|舞蹈|4|1600
ST-013|動作校準練習鏡|舞蹈|5|2000
ST-014|舞台編舞解析冊|舞蹈|7|2800
ST-015|即興舞蹈挑戰盒|舞蹈|8|3200
ST-016|主持開場練習卡|主持|3|1200
ST-017|訪談提問訓練冊|主持|5|2000
ST-018|臨場救場應對題庫|主持|7|2800
ST-019|節目控場模擬器|主持|9|3600
ST-020|綜藝反應訓練卡|綜藝|3|1200
ST-021|互動遊戲企劃盒|綜藝|5|2000
ST-022|即興喜劇挑戰題庫|綜藝|7|2800
ST-023|團隊默契排練組|綜藝|9|3600
ST-024|採訪應對話術手冊|公關|4|1600
ST-025|媒體危機情境沙盤|公關|6|2400
ST-026|新聞發布會演練組|公關|8|3200
ST-027|舞台鏡頭表現教材|容貌|4|1600
ST-028|舞台形象分析套件|路人緣|5|2000
ST-029|演藝合約解析課程|商業價值|6|2400
ST-030|藝人品牌策略研習盒|商業價值|8|3200
""".strip()


# ==========================================
# 商品資料驗證
# ==========================================

def catalog():
    result = []

    for line in CATALOG_TEXT.splitlines():
        fields = line.split("|")

        if len(fields) != 5:
            raise ValueError(
                f"商品資料格式不正確：{line}"
            )

        sku, name, stat, bonus_text, price_text = fields

        bonus = int(bonus_text)
        price = int(price_text)

        if bonus <= 0:
            raise ValueError(
                f"{sku} 加成數值必須大於零。"
            )

        if price < bonus * 300:
            raise ValueError(
                f"{sku} 售價低於最低加成定價。"
            )

        result.append(
            (sku, name, stat, bonus, price)
        )

    if len(result) != 30:
        raise ValueError(
            "B05 必須有 30 件商品。"
        )

    if len({row[0] for row in result}) != 30:
        raise ValueError(
            "B05 存在重複商品代碼。"
        )

    return result


# ==========================================
# 建立商品登記表
# ==========================================

def init_db():
    shop.init_shop_db()

    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            b05_catalog (
                guild_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                product_id INTEGER NOT NULL,

                PRIMARY KEY(guild_id, sku),
                UNIQUE(guild_id, product_id)
            )
            """
        )


# ==========================================
# 批量上架商品
# ==========================================

def import_catalog(guild_id):
    gid = str(guild_id)

    created = 0
    skipped = 0

    with transaction() as conn:
        existing = {
            row["sku"]
            for row in conn.execute(
                """
                SELECT sku
                FROM b05_catalog
                WHERE guild_id=?
                """,
                (gid,),
            )
        }

        for sku, name, stat, bonus, price in catalog():

            if sku in existing:
                skipped += 1
                continue

            now = shop.utc_now()

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
                    ?,?,?,?,?,?,?,?,?,?,?,1,?,?
                )
                """,
                (
                    gid,
                    name,
                    "B05-演藝用品",
                    (
                        f"B05 演藝用品｜{sku}｜"
                        "一次性道具｜"
                        f"使用後永久增加 {stat} +{bonus}"
                    ),
                    price,
                    None,
                    "public",
                    "instant",
                    stat,
                    bonus,
                    "背包使用後永久生效",
                    now,
                    now,
                ),
            )

            conn.execute(
                """
                INSERT INTO b05_catalog (
                    guild_id,
                    sku,
                    product_id
                )
                VALUES (?,?,?)
                """,
                (
                    gid,
                    sku,
                    int(result.lastrowid),
                ),
            )

            created += 1

    return (
        "✅ B05 上架完成！\n"
        f"新增商品：{created} 件\n"
        f"已登記略過：{skipped} 件"
    )


# ==========================================
# 查詢上架進度
# ==========================================

def status(guild_id):
    with transaction() as conn:
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM b05_catalog
            WHERE guild_id=?
            """,
            (str(guild_id),),
        ).fetchone()[0]

    return (
        "🎭 B05 演藝用品店\n"
        f"已登記商品：{count}/30 件"
    )


# ==========================================
# 群主權限
# ==========================================

def owner_only(interaction):
    if (
        not interaction.guild
        or interaction.user.id
        != interaction.guild.owner_id
    ):
        raise ValueError(
            "僅限伺服器群主上架 B05 商品。"
        )


# ==========================================
# Discord 指令
# ==========================================

class B05Shop(commands.Cog):

    group = app_commands.Group(
        name="b05",
        description="B05 演藝用品商品管理",
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
        description="群主一次上架 B05 的 30 件演藝用品",
    )
    async def publish(
        self,
        interaction: discord.Interaction,
    ):
        try:
            owner_only(interaction)

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
                f"⚠️ 上架失敗：{exc}"
            )

        except Exception:
            LOG.exception(
                "B05 上架失敗"
            )

            message = (
                "⚠️ 上架失敗，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            message,
            ephemeral=True,
        )

    @group.command(
        name="查詢",
        description="查詢 B05 商品上架進度",
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
                status,
                interaction.guild_id,
            )

        except Exception:
            LOG.exception(
                "B05 查詢失敗"
            )

            message = (
                "⚠️ 查詢失敗，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            message,
            ephemeral=True,
        )


# ==========================================
# 模組初始化
# ==========================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        B05Shop(bot)
    )
