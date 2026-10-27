
"""Discord RP｜B07 家居生活店。

指令：
/b07 上架
/b07 查詢

功能：
- 30 件居家生活商品
- 22 件普通屬性商品
- 8 件 CP 同款購買專用商品
- 商品價格與效果公開
- 群主批量上架
- 重複上架不重複新增
- 不清空任何角色、銀行或背包資料
- 沿用原本商城及 CP 關係系統

CP 好感度：
由現有 zz_shop_relationships.py 判定，
本模組不重複增加 CP 分數。
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

SHOP_CODE = "B07"
CATEGORY = "B07-家居生活"

EXPECTED_COUNT = 30
MIN_PRICE_PER_POINT = 300


# ==========================================
# 商品清單
# ==========================================

# 格式：
# 代碼|名稱|價格|效果類型|屬性|增加數值
#
# instant：
# 使用一件商品，永久增加指定屬性。
#
# cp：
# 只有正式 CP 雙方購買相同官方商品，
# 才透過既有關係系統增加 CP 好感度。
#
# CP 商品本身不直接增加角色十三項屬性。

CATALOG_TEXT = """
HM-001|手工陶藝製作套組|1800|instant|個人才藝|5
HM-002|家庭烘焙入門套組|2400|instant|個人才藝|7
HM-003|高階咖啡拉花工具組|3200|instant|個人才藝|9
HM-004|創意料理研究套組|4000|instant|個人才藝|12
HM-005|家庭園藝培養箱|2000|instant|個人才藝|6
HM-006|室內花藝設計套組|2800|instant|個人才藝|8
HM-007|居家香氛禮盒|1600|instant|路人緣|4
HM-008|手作香氛蠟燭套組|2000|instant|個人才藝|6
HM-009|家庭餐桌禮儀教具|2400|instant|路人緣|7
HM-010|精緻下午茶餐具組|3200|instant|路人緣|9
HM-011|私人空間設計手冊|3600|instant|商業價值|10
HM-012|居家品牌經營課程組|4800|instant|商業價值|14
HM-013|桌遊派對企劃盒|1600|instant|路人緣|4
HM-014|家庭影像剪輯教材|2400|instant|個人才藝|7
HM-015|經典電影收藏套組|2800|instant|流量熱度|8
HM-016|私人收藏展示企劃組|3200|instant|商業價值|9
HM-017|手工模型製作套組|2400|instant|個人才藝|7
HM-018|藝術拼貼收藏盒|2000|instant|個人才藝|6
HM-019|社交派對佈置工具組|2800|instant|路人緣|8
HM-020|家庭攝影創作套組|4000|instant|流量熱度|12
HM-021|經典唱片賞析收藏組|3200|instant|個人才藝|9
HM-022|高階生活美學研習盒|4800|instant|商業價值|14
HM-023|雙人情侶對杯|3000|cp||0
HM-024|雙人居家拖鞋組|2400|cp||0
HM-025|情侶同款抱枕組|3600|cp||0
HM-026|雙人拼圖紀念盒|4000|cp||0
HM-027|雙人紀念相框|4500|cp||0
HM-028|情侶同款居家睡衣|6000|cp||0
HM-029|雙人紀念音樂盒|7500|cp||0
HM-030|雙人限定居家收藏禮盒|9000|cp||0
""".strip()


# ==========================================
# 資料驗證
# ==========================================

def catalog():
    result = []
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

        if len(parts) != 6:
            raise ValueError(
                f"商品第 {index} 行格式錯誤。"
            )

        sku, name, price_raw, kind, stat, value_raw = parts

        if not sku or not name:
            raise ValueError(
                f"第 {index} 行缺少商品名稱。"
            )

        if sku in seen:
            raise ValueError(
                f"商品代碼重複：{sku}"
            )

        seen.add(sku)

        price = int(price_raw)
        value = int(value_raw)

        if price < 0:
            raise ValueError(
                f"{sku} 售價不能是負數。"
            )

        if kind not in ("instant", "cp"):
            raise ValueError(
                f"{sku} 商品類型錯誤。"
            )

        if kind == "instant":
            if not stat or value <= 0:
                raise ValueError(
                    f"{sku} 必須指定有效屬性。"
                )

            if price < value * MIN_PRICE_PER_POINT:
                raise ValueError(
                    f"{sku} 售價低於最低定價。"
                )

        if kind == "cp":
            if stat or value != 0:
                raise ValueError(
                    f"{sku} CP 商品不能有普通屬性加成。"
                )

        result.append(
            (
                sku,
                name,
                price,
                kind,
                stat,
                value,
            )
        )

    if len(result) != EXPECTED_COUNT:
        raise ValueError(
            "B07 商品數量必須為 30 件。"
        )

    expected = {
        f"HM-{number:03d}"
        for number in range(1, 31)
    }

    if seen != expected:
        raise ValueError(
            "商品代碼必須為 HM-001～HM-030。"
        )

    cp_count = sum(
        1
        for product in result
        if product[3] == "cp"
    )

    if cp_count != 8:
        raise ValueError(
            "B07 必須包含 8 件 CP 商品。"
        )

    return result


# ==========================================
# 初始化商品登記資料表
# ==========================================

def init_db():
    # 主商城的資料表由原模組建立。
    # 這裡只建立 B07 的商品索引表。
    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS b07_catalog (
                guild_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,

                PRIMARY KEY(guild_id, sku),
                UNIQUE(guild_id, product_id)
            )
            """
        )


# ==========================================
# 商品介紹
# ==========================================

def make_description(
    sku,
    kind,
    stat,
    value,
):
    if kind == "cp":
        return (
            f"B07 家居生活｜{sku}｜"
            "CP 同款收藏商品。"
            "正式 CP 雙方都購買相同商品後，"
            "透過關係系統增加 CP 好感度。"
            "每對 CP 每款商品只獎勵一次。"
        )

    return (
        f"B07 家居生活｜{sku}｜"
        "一次性使用商品。"
        f"使用後永久增加 {stat} +{value}。"
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
                FROM b07_catalog
                WHERE guild_id=?
                """,
                (gid,),
            ).fetchall()
        }

        for (
            sku,
            name,
            price,
            kind,
            stat,
            value,
        ) in products:

            if sku in existing:
                skipped += 1
                continue

            # 若舊版已上架過同代碼商品，
            # 不重複建立，也不覆蓋玩家既有資料。
            legacy = conn.execute(
                """
                SELECT id
                FROM shop_products
                WHERE guild_id=?
                  AND category LIKE 'B07%'
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
                    INSERT INTO b07_catalog (
                        guild_id,
                        sku,
                        product_id,
                        item_type
                    )
                    VALUES (?,?,?,?)
                    """,
                    (
                        gid,
                        sku,
                        int(legacy["id"]),
                        kind,
                    ),
                )

                skipped += 1
                continue

            now = datetime.now(
                timezone.utc
            ).isoformat()

            # CP 商品使用 none，
            # 避免玩家按下使用就直接加好感度。
            effect_kind = (
                "none"
                if kind == "cp"
                else "instant"
            )

            description = make_description(
                sku,
                kind,
                stat,
                value,
            )

            effect_context = (
                "正式 CP 雙方購買同款才增加好感度"
                if kind == "cp"
                else "使用後永久增加"
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
                    effect_kind,
                    stat or None,
                    value,
                    effect_context,
                    now,
                    now,
                ),
            )

            product_id = int(
                result.lastrowid
            )

            conn.execute(
                """
                INSERT INTO b07_catalog (
                    guild_id,
                    sku,
                    product_id,
                    item_type
                )
                VALUES (?,?,?,?)
                """,
                (
                    gid,
                    sku,
                    product_id,
                    kind,
                ),
            )

            created += 1

    return (
        "✅ **B07 家居生活店上架完成！**\n\n"
        f"新增商品：{created} 件\n"
        f"已有紀錄：{skipped} 件\n"
        "預設商品：30 件\n\n"
        "🏠 普通居家用品：使用後永久加成\n"
        "💕 雙人商品：依正式 CP 同款購買規則結算"
    )


# ==========================================
# 查詢上架進度
# ==========================================

def query_catalog(guild_id):
    gid = str(guild_id)

    init_db()

    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                b.sku,
                b.item_type,
                p.name,
                p.price,
                p.effect_stat,
                p.effect_value,
                p.active
            FROM b07_catalog AS b
            JOIN shop_products AS p
              ON p.guild_id=b.guild_id
             AND p.id=b.product_id
            WHERE b.guild_id=?
            ORDER BY b.sku
            """,
            (gid,),
        ).fetchall()

    total = len(rows)

    ordinary = sum(
        1
        for row in rows
        if row["item_type"] == "instant"
    )

    cp_count = sum(
        1
        for row in rows
        if row["item_type"] == "cp"
    )

    active = sum(
        1
        for row in rows
        if int(row["active"]) == 1
    )

    return (
        "🏠 **B07 家居生活店**\n\n"
        f"已登記：{total}/30 件\n"
        f"普通商品：{ordinary}/22 件\n"
        f"CP 同款商品：{cp_count}/8 件\n"
        f"目前上架：{active} 件"
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
            "只有伺服器群主可以管理 B07 商品。"
        )


# ==========================================
# 指令
# ==========================================

class B07Shop(commands.Cog):

    group = app_commands.Group(
        name="b07",
        description="B07 家居生活商品管理",
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
        description="一次上架 30 件家居生活商品",
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
                "B07 商品上架失敗"
            )

            result = (
                "⚠️ 上架發生系統錯誤，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            result[:1900],
            ephemeral=True,
        )

    @group.command(
        name="查詢",
        description="查看 B07 商品上架狀態",
    )
    async def query(
        self,
        interaction: discord.Interaction,
    ):
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
                "B07 查詢失敗"
            )

            result = (
                "⚠️ 查詢失敗，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            result[:1900],
            ephemeral=True,
        )


# ==========================================
# 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        B07Shop(bot)
    )
